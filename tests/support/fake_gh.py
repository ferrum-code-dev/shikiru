"""A fake `gh` for pull request tests; its pull requests are JSON files in a state directory.

A pull request record holds its reviews, its inline discussions (each with its comments in
order) and its conversation comments. GraphQL reads serve them in pages of `PAGE_LIMIT` nodes;
REST posts append the posted comment to the record. A `fail-<operation>` budget file makes that
many calls of the operation fail, as a network blip does.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

URL_PREFIX = "https://github.invalid/shikiru/fake/pull/"
PR_COMMAND = "pr"
API_COMMAND = "api"
GRAPHQL_TARGET = "graphql"
POST_METHOD = "POST"
METHOD_FLAG = "--method"
HOST_FLAG = "--hostname"
RAW_FIELD_FLAG = "-f"
TYPED_FIELD_FLAG = "-F"
PULLS_DIR = "pulls"
CALLS_LOG = "calls.jsonl"
HOLD_PREFIX = "hold-"
FAIL_PREFIX = "fail-"
GRAPHQL_BUDGET = "graphql"
POST_BUDGET = "post"
LOST_POST_BUDGET = "lost-post"
PAGE_LIMIT = 2
FAKE_LOGIN = "shikiru-fake"
FAKE_TIMESTAMP = "2026-01-02T03:04:05Z"
_OPEN = "OPEN"
_PARTIAL_SUFFIX = ".partial"
_REVIEW_COMMENTS_KIND = "pulls"
_CONVERSATION_KIND = "issues"
_OPERATION_NAME = re.compile(r"\bquery (\w+)")
_COMMENTS_ENDPOINT = re.compile(
    r"repos/[^/]+/[^/]+/(?P<kind>pulls|issues)/(?P<number>[0-9]+)/comments"
)
_NETWORK_DOWN = "network unreachable"
_LOST_AFTER_RECORD = "network unreachable after the comment was recorded"
_NO_PULL_REQUEST = "Could not resolve to a PullRequest"
_NO_THREAD = "no review thread opens with the comment being replied to"
_UNSUPPORTED_GRAPHQL = "does not support this GraphQL operation"


class _GhError(Exception):
    """A call the fake answers with a failure, printed to stderr."""


def _unsupported(request: object) -> _GhError:
    return _GhError(f"does not support {request}")


@dataclass(frozen=True)
class _ApiRequest:
    method: str | None
    target: str
    fields: dict[str, Any]

    @classmethod
    def parse(cls, args: list[str]) -> _ApiRequest:
        method: str | None = None
        target = ""
        fields: dict[str, Any] = {}
        words = iter(args[1:])
        for word in words:
            if word == METHOD_FLAG:
                method = next(words)
            elif word == HOST_FLAG:
                next(words)
            elif word in (RAW_FIELD_FLAG, TYPED_FIELD_FLAG):
                key, _, value = next(words).partition("=")
                fields[key] = int(value) if word == TYPED_FIELD_FLAG else value
            else:
                target = word
        return cls(method, target, fields)


def write_record(path: Path, record: dict[str, Any]) -> None:
    partial = path.with_name(path.name + _PARTIAL_SUFFIX)
    partial.write_text(json.dumps(record))
    partial.replace(path)


def _hold(state: Path, subcommand: str) -> None:
    hold = state / f"{HOLD_PREFIX}{subcommand}"
    if hold.exists():
        with hold.open() as fifo:
            fifo.read()


def _failing(state: Path, operation: str) -> bool:
    budget = state / f"{FAIL_PREFIX}{operation}"
    remaining = int(budget.read_text()) if budget.exists() else 0
    if remaining > 0:
        budget.write_text(str(remaining - 1))
    return remaining > 0


def _pull_request_path(state: Path, number: str) -> Path:
    return state / PULLS_DIR / f"{number}.json"


def _find_record(state: Path, number: str) -> dict[str, Any] | None:
    path = _pull_request_path(state, number)
    return json.loads(path.read_text()) if path.exists() else None


def _records(state: Path) -> list[tuple[str, dict[str, Any]]]:
    pulls = state / PULLS_DIR
    count = len(list(pulls.glob("*.json")))
    return [
        (f"{URL_PREFIX}{number}", json.loads((pulls / f"{number}.json").read_text()))
        for number in range(1, count + 1)
    ]


def _flag_values(options: list[str]) -> dict[str, str]:
    pairs = iter(options)
    return dict(zip(pairs, pairs, strict=True))


def _create(state: Path, options: list[str]) -> str:
    values = _flag_values(options)
    pulls = state / PULLS_DIR
    number = len(list(pulls.glob("*.json"))) + 1
    record: dict[str, Any] = {
        "state": _OPEN,
        "mergeCommit": None,
        "mergeable": "MERGEABLE",
        "statusCheckRollup": [],
        "base": values["--base"],
        "head": values["--head"],
        "title": values["--title"],
        "body": values["--body"],
        "headRefOid": None,
        "reviews": [],
        "reviewThreads": [],
        "conversationComments": [],
    }
    write_record(_pull_request_path(state, str(number)), record)
    _hold(state, "create")
    return f"{URL_PREFIX}{number}"


def _list(state: Path, options: list[str]) -> str:
    values = _flag_values(options)
    matching = [
        {"url": url}
        for url, record in _records(state)
        if record["head"] == values["--head"]
        and record["base"] == values["--base"]
        and record["state"].lower() == values["--state"]
    ]
    return json.dumps(matching)


def _view(state: Path, url: str, options: list[str]) -> str:
    fields = options[options.index("--json") + 1].split(",")
    record = json.loads(_pull_request_path(state, url.removeprefix(URL_PREFIX)).read_text())
    return json.dumps({field: record[field] for field in fields})


def _close(state: Path, url: str) -> str:
    _hold(state, "close")
    path = _pull_request_path(state, url.removeprefix(URL_PREFIX))
    write_record(path, {**json.loads(path.read_text()), "state": "CLOSED"})
    return ""


def _connection(nodes: list[dict[str, Any]], cursor: str | None) -> dict[str, Any]:
    start = 0 if cursor is None else int(cursor)
    end = start + PAGE_LIMIT
    return {
        "nodes": nodes[start:end],
        "pageInfo": {"hasNextPage": end < len(nodes), "endCursor": str(end)},
    }


def _thread_payload(thread: dict[str, Any]) -> dict[str, Any]:
    return {**thread, "comments": _connection(thread["comments"], None)}


def _pull_request_data(
    state: Path, fields: dict[str, Any], build: Callable[[dict[str, Any]], dict[str, Any]]
) -> dict[str, Any]:
    record = _find_record(state, str(fields["number"]))
    return {"repository": {"pullRequest": None if record is None else build(record)}}


def _head_data(state: Path, fields: dict[str, Any]) -> dict[str, Any]:
    return _pull_request_data(
        state,
        fields,
        lambda record: {"state": record["state"], "headRefOid": record["headRefOid"]},
    )


def _reviews_data(state: Path, fields: dict[str, Any]) -> dict[str, Any]:
    return _pull_request_data(
        state,
        fields,
        lambda record: {"reviews": _connection(record["reviews"], fields.get("cursor"))},
    )


def _threads_data(state: Path, fields: dict[str, Any]) -> dict[str, Any]:
    return _pull_request_data(
        state,
        fields,
        lambda record: {
            "reviewThreads": _connection(
                [_thread_payload(thread) for thread in record["reviewThreads"]],
                fields.get("cursor"),
            )
        },
    )


def _conversation_data(state: Path, fields: dict[str, Any]) -> dict[str, Any]:
    return _pull_request_data(
        state,
        fields,
        lambda record: {
            "comments": _connection(record["conversationComments"], fields.get("cursor"))
        },
    )


def _find_thread(state: Path, thread_id: str) -> dict[str, Any] | None:
    for _, record in _records(state):
        for thread in record["reviewThreads"]:
            if thread["id"] == thread_id:
                return thread
    return None


def _thread_comments_data(state: Path, fields: dict[str, Any]) -> dict[str, Any]:
    thread = _find_thread(state, fields["threadId"])
    node = (
        None
        if thread is None
        else {"comments": _connection(thread["comments"], fields.get("cursor"))}
    )
    return {"node": node}


_GRAPHQL_OPERATIONS: dict[str, Callable[[Path, dict[str, Any]], dict[str, Any]]] = {
    "PullRequestHead": _head_data,
    "PullRequestReviews": _reviews_data,
    "PullRequestReviewThreads": _threads_data,
    "ReviewThreadComments": _thread_comments_data,
    "PullRequestConversation": _conversation_data,
}


def _graphql(state: Path, fields: dict[str, Any]) -> str:
    name = _OPERATION_NAME.search(fields["query"])
    operation = None if name is None else _GRAPHQL_OPERATIONS.get(name.group(1))
    if operation is None:
        raise _GhError(_UNSUPPORTED_GRAPHQL)
    return json.dumps({"data": operation(state, fields)})


def _comment_ids(record: dict[str, Any]) -> list[int]:
    threads = [comment for thread in record["reviewThreads"] for comment in thread["comments"]]
    return [comment["databaseId"] for comment in [*threads, *record["conversationComments"]]]


def _posted_comment(number: str, record: dict[str, Any], body: str) -> dict[str, Any]:
    database_id = max(_comment_ids(record), default=0) + 1
    return {
        "id": f"IC_fake_{database_id}",
        "databaseId": database_id,
        "url": f"{URL_PREFIX}{number}#comment-{database_id}",
        "body": body,
        "createdAt": FAKE_TIMESTAMP,
        "author": {"login": FAKE_LOGIN},
        "pullRequestReview": None,
    }


def _rest_payload(comment: dict[str, Any]) -> dict[str, Any]:
    return {
        "node_id": comment["id"],
        "id": comment["databaseId"],
        "html_url": comment["url"],
        "user": {"login": comment["author"]["login"]},
        "created_at": comment["createdAt"],
        "body": comment["body"],
    }


def _thread_opened_by(record: dict[str, Any], database_id: int) -> dict[str, Any]:
    for thread in record["reviewThreads"]:
        if thread["comments"] and thread["comments"][0]["databaseId"] == database_id:
            return thread
    raise _GhError(_NO_THREAD)


def _post(state: Path, request: _ApiRequest) -> str:
    endpoint = _COMMENTS_ENDPOINT.fullmatch(request.target)
    if endpoint is None:
        raise _unsupported(request.target)
    number = endpoint.group("number")
    record = _find_record(state, number)
    if record is None:
        raise _GhError(_NO_PULL_REQUEST)
    comment = _posted_comment(number, record, request.fields["body"])
    if endpoint.group("kind") == _REVIEW_COMMENTS_KIND:
        _thread_opened_by(record, request.fields["in_reply_to"])["comments"].append(comment)
    else:
        record["conversationComments"].append(comment)
    write_record(_pull_request_path(state, number), record)
    if _failing(state, LOST_POST_BUDGET):
        raise _GhError(_LOST_AFTER_RECORD)
    return json.dumps(_rest_payload(comment))


def _api(state: Path, request: _ApiRequest) -> str:
    if request.target == GRAPHQL_TARGET:
        if _failing(state, GRAPHQL_BUDGET):
            raise _GhError(_NETWORK_DOWN)
        return _graphql(state, request.fields)
    if request.method == POST_METHOD:
        if _failing(state, POST_BUDGET):
            raise _GhError(_NETWORK_DOWN)
        return _post(state, request)
    raise _unsupported(request.target)


def _serve(state: Path, args: list[str]) -> str:
    if args[:1] == [API_COMMAND]:
        return _api(state, _ApiRequest.parse(args))
    if args[:1] == [PR_COMMAND] and _failing(state, args[1]):
        raise _GhError(_NETWORK_DOWN)
    match args:
        case ["pr", "create", *options]:
            return _create(state, options)
        case ["pr", "list", *options]:
            return _list(state, options)
        case ["pr", "view", url, *options]:
            return _view(state, url, options)
        case ["pr", "close", url]:
            return _close(state, url)
        case _:
            raise _unsupported(args)


def main(argv: list[str]) -> int:
    """Serve `gh pr create`, `list`, `close URL` and `view URL --json FIELDS`, and the feedback
    GraphQL reads and REST comment posts of `gh api`; reject the rest.

    While the `hold-create` FIFO exists, `pr create` records its pull request and then blocks
    before printing it, as if the engine died between opening and recording it; `hold-close`
    blocks `pr close` before it acts. A `fail-<operation>` file holding a count makes that many
    calls of the operation fail, as a network blip does: `pr <subcommand>`, `graphql`, `post`
    (before it records anything) or `lost-post` (after it records the comment)."""
    state = Path(argv[0])
    args = argv[1:]
    with (state / CALLS_LOG).open("a") as log:
        log.write(json.dumps(args) + "\n")
    try:
        output = _serve(state, args)
    except _GhError as error:
        sys.stderr.write(f"fake gh: {error}\n")
        return 1
    sys.stdout.write(output + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
