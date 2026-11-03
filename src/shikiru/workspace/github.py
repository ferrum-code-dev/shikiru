"""The only place gh argv is built: pull requests for `integration = "pr"`, and their feedback."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, cast

from shikiru.errors import ShikiruError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable
    from pathlib import Path

    from shikiru.process import ProcessResult, SubprocessRunner

_GH_BIN = "gh"
_COMMAND_TIMEOUT_SECONDS = 60.0
_OPEN_STATE_FILTER = "open"
_CONFLICTING = "CONFLICTING"
_CHECK_COMPLETED = "COMPLETED"
_FAILING_CHECK_RESULTS = frozenset(
    ("FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "STARTUP_FAILURE", "ACTION_REQUIRED")
)
_PENDING_CHECK_STATES = frozenset(("PENDING", "EXPECTED"))
_GITHUB_MAX_PAGE_SIZE = 100
_DECIMAL_DIGITS = re.compile(r"[0-9]+")
_PULL_REQUEST_URL = re.compile(
    r"https://(?P<host>[A-Za-z0-9.-]+)/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)"
    r"/pull/(?P<number>[0-9]+)"
)
_MALFORMED_RESPONSE = "gh returned an unexpected response"
_NO_COMMENT_TO_REPLY_TO = "review thread has no comment to reply to"
_GRAPHQL_ERRORS = "GitHub GraphQL returned errors"
_REST_POST_METHOD = "POST"
_NAMED_FIELD_FLAG = "-f"
_TYPED_FIELD_FLAG = "-F"

_REVIEW_COMMENT_FRAGMENT = """
fragment ReviewComment on PullRequestReviewComment {
  id
  fullDatabaseId
  url
  body
  createdAt
  author { login }
  pullRequestReview { id }
}
"""

_HEAD_QUERY = """
query PullRequestHead($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      state
      headRefOid
    }
  }
}
"""

_REVIEWS_QUERY = """
query PullRequestReviews(
  $owner: String!, $repo: String!, $number: Int!, $pageSize: Int!, $cursor: String
) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      reviews(first: $pageSize, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes { id url state body submittedAt author { login } }
      }
    }
  }
}
"""

_THREADS_QUERY = (
    _REVIEW_COMMENT_FRAGMENT
    + """
query PullRequestReviewThreads(
  $owner: String!, $repo: String!, $number: Int!, $pageSize: Int!, $cursor: String
) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      reviewThreads(first: $pageSize, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          path
          line
          isOutdated
          isResolved
          comments(first: $pageSize) {
            pageInfo { hasNextPage endCursor }
            nodes { ...ReviewComment }
          }
        }
      }
    }
  }
}
"""
)

_THREAD_COMMENTS_QUERY = (
    _REVIEW_COMMENT_FRAGMENT
    + """
query ReviewThreadComments($threadId: ID!, $pageSize: Int!, $cursor: String) {
  node(id: $threadId) {
    ... on PullRequestReviewThread {
      comments(first: $pageSize, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes { ...ReviewComment }
      }
    }
  }
}
"""
)

_CONVERSATION_QUERY = """
query PullRequestConversation(
  $owner: String!, $repo: String!, $number: Int!, $pageSize: Int!, $cursor: String
) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      comments(first: $pageSize, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes { id fullDatabaseId url body createdAt author { login } }
      }
    }
  }
}
"""


class _Key(StrEnum):
    """The JSON keys gh and GitHub responses use, named once."""

    AUTHOR = "author"
    BODY = "body"
    CHECKS = "statusCheckRollup"
    COMMENTS = "comments"
    CONCLUSION = "conclusion"
    CREATED_AT = "createdAt"
    DATA = "data"
    END_CURSOR = "endCursor"
    ERRORS = "errors"
    FULL_DATABASE_ID = "fullDatabaseId"
    HAS_NEXT_PAGE = "hasNextPage"
    HEAD_SHA = "headRefOid"
    HTML_URL = "html_url"
    ID = "id"
    IS_OUTDATED = "isOutdated"
    IS_RESOLVED = "isResolved"
    LINE = "line"
    LOGIN = "login"
    MERGE_COMMIT = "mergeCommit"
    MERGEABLE = "mergeable"
    NODE = "node"
    NODE_ID = "node_id"
    NODES = "nodes"
    OID = "oid"
    PAGE_INFO = "pageInfo"
    PATH = "path"
    PULL_REQUEST = "pullRequest"
    REPOSITORY = "repository"
    REST_CREATED_AT = "created_at"
    REVIEW = "pullRequestReview"
    REVIEW_THREADS = "reviewThreads"
    REVIEWS = "reviews"
    STATE = "state"
    STATUS = "status"
    SUBMITTED_AT = "submittedAt"
    URL = "url"
    USER = "user"


class PullRequestState(StrEnum):
    """The pull request states `gh pr view --json state` reports."""

    OPEN = "OPEN"
    CLOSED = "CLOSED"
    MERGED = "MERGED"


class ReviewState(StrEnum):
    """The states GitHub reports for a submitted or unsubmitted pull request review."""

    PENDING = "PENDING"
    COMMENTED = "COMMENTED"
    APPROVED = "APPROVED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    DISMISSED = "DISMISSED"


@dataclass(frozen=True, slots=True)
class PullRequestStatus:
    """A pull request's state, whether it can land, and the commit it landed as once merged.

    `checks_failing` is true when any check failed; `checks_pending` when any check has not
    completed yet, so a failure is final only while nothing is pending.
    """

    state: PullRequestState
    merge_commit: str | None
    conflicting: bool
    checks_failing: bool
    checks_pending: bool


@dataclass(frozen=True, slots=True)
class Review:
    """A pull request review: its state, author, submission time and body, and its page URL."""

    id: str
    url: str
    state: ReviewState
    author: str | None
    submitted_at: datetime | None
    body: str


@dataclass(frozen=True, slots=True)
class PullRequestComment:
    """A comment in an inline discussion or the PR conversation, with the IDs needed to reply.

    `review_id` is the review an inline comment was submitted in. It is None for conversation
    comments and for replies this engine posts, which are not part of a review.
    """

    id: str
    database_id: int
    review_id: str | None
    url: str
    author: str | None
    body: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ReviewThread:
    """An inline discussion: where it sits, whether it is outdated or resolved, and its comments.

    Each comment carries the review it was submitted in, so a discussion reopened by a later
    review shows which comments that review added. Outdated and resolved are separate facts:
    an outdated discussion may still be unresolved.
    """

    id: str
    path: str
    line: int | None
    is_outdated: bool
    is_resolved: bool
    comments: tuple[PullRequestComment, ...]

    @property
    def reply_to_id(self) -> int:
        """The database ID a reply to this discussion must name: its first comment's."""
        if not self.comments:
            raise ShikiruError(_NO_COMMENT_TO_REPLY_TO)
        return self.comments[0].database_id


@dataclass(frozen=True, slots=True)
class PullRequestFeedback:
    """The PR's state, head commit, submitted reviews and their inline discussions."""

    url: str
    state: PullRequestState
    head_sha: str
    reviews: tuple[Review, ...]
    threads: tuple[ReviewThread, ...]


@dataclass(frozen=True, slots=True)
class _PullRequestAddress:
    host: str
    owner: str
    repo: str
    number: int

    @classmethod
    def parse(cls, url: str) -> _PullRequestAddress:
        match = _PULL_REQUEST_URL.fullmatch(url)
        if match is None:
            message = f"not a pull request URL: {url}"
            raise ShikiruError(message)
        return cls(
            host=match.group("host"),
            owner=match.group("owner"),
            repo=match.group("repo"),
            number=int(match.group("number")),
        )


@dataclass(frozen=True, slots=True)
class _Page:
    nodes: tuple[dict[str, Any], ...]
    next_cursor: str | None


def _check_pending(check: dict[str, Any]) -> bool:
    if _Key.STATUS in check:
        return check[_Key.STATUS] != _CHECK_COMPLETED
    return check.get(_Key.STATE) in _PENDING_CHECK_STATES


def _malformed(key: _Key) -> ShikiruError:
    return ShikiruError(f"{_MALFORMED_RESPONSE}: {key.value}")


def _json(text: str) -> object:
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise ShikiruError(_MALFORMED_RESPONSE) from error


def _object(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ShikiruError(_MALFORMED_RESPONSE)
    return cast("dict[str, Any]", value)


def _field(node: dict[str, Any], key: _Key) -> object:
    if key not in node:
        raise _malformed(key)
    return node[key]


def _text(node: dict[str, Any], key: _Key) -> str:
    value = _field(node, key)
    if not isinstance(value, str):
        raise _malformed(key)
    return value


def _optional_text(node: dict[str, Any], key: _Key) -> str | None:
    value = _field(node, key)
    if value is None or isinstance(value, str):
        return value
    raise _malformed(key)


def _integer(node: dict[str, Any], key: _Key) -> int:
    value = _field(node, key)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise _malformed(key)


def _optional_integer(node: dict[str, Any], key: _Key) -> int | None:
    if _field(node, key) is None:
        return None
    return _integer(node, key)


def _big_integer(node: dict[str, Any], key: _Key) -> int:
    value = _text(node, key)
    if not _DECIMAL_DIGITS.fullmatch(value):
        raise _malformed(key)
    return int(value)


def _flag(node: dict[str, Any], key: _Key) -> bool:
    value = _field(node, key)
    if isinstance(value, bool):
        return value
    raise _malformed(key)


def _list_field(node: dict[str, Any], key: _Key) -> list[Any]:
    value = _field(node, key)
    if isinstance(value, list):
        return cast("list[Any]", value)
    raise _malformed(key)


def _object_field(node: dict[str, Any], key: _Key) -> dict[str, Any] | None:
    value = _field(node, key)
    return None if value is None else _object(value)


def _moment(node: dict[str, Any], key: _Key) -> datetime:
    text = _text(node, key)
    try:
        return datetime.fromisoformat(text)
    except ValueError as error:
        raise _malformed(key) from error


def _optional_moment(node: dict[str, Any], key: _Key) -> datetime | None:
    if _field(node, key) is None:
        return None
    return _moment(node, key)


def _enum[E: StrEnum](enum_type: type[E], node: dict[str, Any], key: _Key) -> E:
    try:
        return enum_type(_text(node, key))
    except ValueError as error:
        raise _malformed(key) from error


def _author(node: dict[str, Any]) -> str | None:
    author = _object_field(node, _Key.AUTHOR)
    return None if author is None else _text(author, _Key.LOGIN)


def _review(node: dict[str, Any]) -> Review:
    return Review(
        id=_text(node, _Key.ID),
        url=_text(node, _Key.URL),
        state=_enum(ReviewState, node, _Key.STATE),
        author=_author(node),
        submitted_at=_optional_moment(node, _Key.SUBMITTED_AT),
        body=_text(node, _Key.BODY),
    )


def _comment(node: dict[str, Any], review_id: str | None) -> PullRequestComment:
    return PullRequestComment(
        id=_text(node, _Key.ID),
        database_id=_big_integer(node, _Key.FULL_DATABASE_ID),
        review_id=review_id,
        url=_text(node, _Key.URL),
        author=_author(node),
        body=_text(node, _Key.BODY),
        created_at=_moment(node, _Key.CREATED_AT),
    )


def _review_comment(node: dict[str, Any]) -> PullRequestComment:
    review = _object_field(node, _Key.REVIEW)
    return _comment(node, None if review is None else _text(review, _Key.ID))


def _conversation_comment(node: dict[str, Any]) -> PullRequestComment:
    return _comment(node, None)


def _rest_comment(payload: dict[str, Any]) -> PullRequestComment:
    user = _object_field(payload, _Key.USER)
    return PullRequestComment(
        id=_text(payload, _Key.NODE_ID),
        database_id=_integer(payload, _Key.ID),
        review_id=None,
        url=_text(payload, _Key.HTML_URL),
        author=None if user is None else _text(user, _Key.LOGIN),
        body=_text(payload, _Key.BODY),
        created_at=_moment(payload, _Key.REST_CREATED_AT),
    )


def _page(connection: object) -> _Page:
    container = _object(connection)
    info = _object(_field(container, _Key.PAGE_INFO))
    nodes = tuple(_object(node) for node in _list_field(container, _Key.NODES))
    has_next = _flag(info, _Key.HAS_NEXT_PAGE)
    cursor = _optional_text(info, _Key.END_CURSOR)
    if has_next and cursor is None:
        raise _malformed(_Key.END_CURSOR)
    return _Page(nodes, cursor if has_next else None)


async def _collect(
    first: _Page, fetch: Callable[[str], Awaitable[_Page]]
) -> tuple[dict[str, Any], ...]:
    nodes = list(first.nodes)
    page = first
    while page.next_cursor is not None:
        page = await fetch(page.next_cursor)
        nodes.extend(page.nodes)
    return tuple(nodes)


def _pull_request_node(data: dict[str, Any]) -> dict[str, Any]:
    repository = _object(_field(data, _Key.REPOSITORY))
    return _object(_field(repository, _Key.PULL_REQUEST))


def _marked(comments: Iterable[PullRequestComment], marker: str) -> PullRequestComment | None:
    return next((comment for comment in comments if marker in comment.body), None)


def _address_variables(address: _PullRequestAddress) -> list[str]:
    return [
        _NAMED_FIELD_FLAG,
        f"owner={address.owner}",
        _NAMED_FIELD_FLAG,
        f"repo={address.repo}",
        _TYPED_FIELD_FLAG,
        f"number={address.number}",
    ]


def _page_variables(cursor: str | None) -> list[str]:
    variables = [_TYPED_FIELD_FLAG, f"pageSize={_GITHUB_MAX_PAGE_SIZE}"]
    if cursor is not None:
        variables += [_NAMED_FIELD_FLAG, f"cursor={cursor}"]
    return variables


def _thread_variables(thread_id: str) -> list[str]:
    return [_NAMED_FIELD_FLAG, f"threadId={thread_id}"]


class GitHub:
    """A small wrapper over the gh CLI, always through `SubprocessRunner`."""

    def __init__(self, runner: SubprocessRunner) -> None:
        """Run every gh command through `runner`, never a shell."""
        self._runner = runner

    async def open_pull_request(self, repo_dir: Path, *, base: str, head: str) -> str | None:
        """Return the URL of an open pull request of `head` into `base`, if there is one."""
        result = await self._run(
            repo_dir,
            "pr",
            "list",
            "--head",
            head,
            "--base",
            base,
            "--state",
            _OPEN_STATE_FILTER,
            "--json",
            _Key.URL,
        )
        pulls = json.loads(result.stdout)
        return pulls[0][_Key.URL] if pulls else None

    async def create_pull_request(
        self, repo_dir: Path, *, base: str, head: str, title: str, body: str
    ) -> str:
        """Open a pull request of pushed branch `head` into `base` and return its URL."""
        result = await self._run(
            repo_dir,
            "pr",
            "create",
            "--base",
            base,
            "--head",
            head,
            "--title",
            title,
            "--body",
            body,
        )
        lines = result.stdout.split()
        if not lines:
            message = f"{_GH_BIN} pr create printed no pull request URL"
            raise ShikiruError(message)
        return lines[-1]

    async def pull_request_status(self, repo_dir: Path, url: str) -> PullRequestStatus:
        """Return the state, merge commit, conflict and check results of the PR at `url`."""
        fields = (_Key.STATE, _Key.MERGE_COMMIT, _Key.MERGEABLE, _Key.CHECKS)
        result = await self._run(repo_dir, "pr", "view", url, "--json", ",".join(fields))
        view = json.loads(result.stdout)
        merge_commit = view[_Key.MERGE_COMMIT]
        return PullRequestStatus(
            state=PullRequestState(view[_Key.STATE]),
            merge_commit=merge_commit[_Key.OID] if merge_commit is not None else None,
            conflicting=view[_Key.MERGEABLE] == _CONFLICTING,
            checks_failing=any(
                {check.get(_Key.CONCLUSION), check.get(_Key.STATE)} & _FAILING_CHECK_RESULTS
                for check in view[_Key.CHECKS]
            ),
            checks_pending=any(_check_pending(check) for check in view[_Key.CHECKS]),
        )

    async def close_pull_request(self, repo_dir: Path, url: str) -> None:
        """Close the open pull request at `url` without merging it."""
        await self._run(repo_dir, "pr", "close", url)

    async def pull_request_feedback(self, repo_dir: Path, pr_url: str) -> PullRequestFeedback:
        """Return the PR's state, head commit, every submitted review and every inline discussion.

        Every page of reviews, discussions and discussion replies is read. Anything GitHub
        reports that cannot be parsed raises `ShikiruError` rather than reading as no feedback.
        """
        address = _PullRequestAddress.parse(pr_url)
        head = _pull_request_node(
            await self._graphql(repo_dir, address.host, _HEAD_QUERY, _address_variables(address))
        )
        raw_reviews = await self._all_nodes(repo_dir, address, _REVIEWS_QUERY, _Key.REVIEWS)
        raw_threads = await self._all_nodes(repo_dir, address, _THREADS_QUERY, _Key.REVIEW_THREADS)
        threads = [await self._thread(repo_dir, address.host, raw) for raw in raw_threads]
        return PullRequestFeedback(
            url=pr_url,
            state=_enum(PullRequestState, head, _Key.STATE),
            head_sha=_text(head, _Key.HEAD_SHA),
            reviews=tuple(_review(raw) for raw in raw_reviews),
            threads=tuple(threads),
        )

    async def reply_to_thread(
        self, repo_dir: Path, pr_url: str, *, thread: ReviewThread, body: str
    ) -> PullRequestComment:
        """Post `body` as a reply in `thread`, which stays attached even if its code is outdated."""
        address = _PullRequestAddress.parse(pr_url)
        return await self._post(
            repo_dir,
            address,
            f"pulls/{address.number}/comments",
            body,
            _TYPED_FIELD_FLAG,
            f"in_reply_to={thread.reply_to_id}",
        )

    async def post_conversation_comment(
        self, repo_dir: Path, pr_url: str, *, body: str
    ) -> PullRequestComment:
        """Post `body` as a comment in the PR conversation and return the posted comment."""
        address = _PullRequestAddress.parse(pr_url)
        return await self._post(repo_dir, address, f"issues/{address.number}/comments", body)

    async def find_thread_reply(
        self, repo_dir: Path, pr_url: str, *, thread: ReviewThread, marker: str
    ) -> PullRequestComment | None:
        """Return the comment in `thread` whose body carries `marker`, or None if there is none."""
        address = _PullRequestAddress.parse(pr_url)
        first = await self._thread_comment_page(repo_dir, address.host, thread.id, None)
        nodes = await _collect(
            first,
            lambda cursor: self._thread_comment_page(repo_dir, address.host, thread.id, cursor),
        )
        return _marked((_review_comment(node) for node in nodes), marker)

    async def find_conversation_comment(
        self, repo_dir: Path, pr_url: str, *, marker: str
    ) -> PullRequestComment | None:
        """Return the PR conversation comment whose body carries `marker`, or None if none does."""
        address = _PullRequestAddress.parse(pr_url)
        nodes = await self._all_nodes(repo_dir, address, _CONVERSATION_QUERY, _Key.COMMENTS)
        return _marked((_conversation_comment(node) for node in nodes), marker)

    async def _all_nodes(
        self,
        repo_dir: Path,
        address: _PullRequestAddress,
        query: str,
        connection: _Key,
    ) -> tuple[dict[str, Any], ...]:
        first = await self._pull_request_page(repo_dir, address, query, connection, None)
        return await _collect(
            first,
            lambda cursor: self._pull_request_page(repo_dir, address, query, connection, cursor),
        )

    async def _pull_request_page(
        self,
        repo_dir: Path,
        address: _PullRequestAddress,
        query: str,
        connection: _Key,
        cursor: str | None,
    ) -> _Page:
        data = await self._graphql(
            repo_dir,
            address.host,
            query,
            [*_address_variables(address), *_page_variables(cursor)],
        )
        return _page(_field(_pull_request_node(data), connection))

    async def _thread(self, repo_dir: Path, host: str, node: dict[str, Any]) -> ReviewThread:
        thread_id = _text(node, _Key.ID)
        first = _page(_field(node, _Key.COMMENTS))
        raw_comments = await _collect(
            first,
            lambda cursor: self._thread_comment_page(repo_dir, host, thread_id, cursor),
        )
        return ReviewThread(
            id=thread_id,
            path=_text(node, _Key.PATH),
            line=_optional_integer(node, _Key.LINE),
            is_outdated=_flag(node, _Key.IS_OUTDATED),
            is_resolved=_flag(node, _Key.IS_RESOLVED),
            comments=tuple(_review_comment(raw) for raw in raw_comments),
        )

    async def _thread_comment_page(
        self, repo_dir: Path, host: str, thread_id: str, cursor: str | None
    ) -> _Page:
        data = await self._graphql(
            repo_dir,
            host,
            _THREAD_COMMENTS_QUERY,
            [*_thread_variables(thread_id), *_page_variables(cursor)],
        )
        return _page(_field(_object(_field(data, _Key.NODE)), _Key.COMMENTS))

    async def _post(
        self,
        repo_dir: Path,
        address: _PullRequestAddress,
        resource: str,
        body: str,
        *typed_fields: str,
    ) -> PullRequestComment:
        endpoint = f"repos/{address.owner}/{address.repo}/{resource}"
        result = await self._run(
            repo_dir,
            "api",
            "--method",
            _REST_POST_METHOD,
            "--hostname",
            address.host,
            endpoint,
            _NAMED_FIELD_FLAG,
            f"body={body}",
            *typed_fields,
        )
        return _rest_comment(_object(_json(result.stdout)))

    async def _graphql(
        self, repo_dir: Path, host: str, query: str, variables: list[str]
    ) -> dict[str, Any]:
        result = await self._run(
            repo_dir,
            "api",
            "--hostname",
            host,
            "graphql",
            _NAMED_FIELD_FLAG,
            f"query={query}",
            *variables,
        )
        response = _object(_json(result.stdout))
        if _Key.ERRORS in response:
            message = f"{_GRAPHQL_ERRORS}: {response[_Key.ERRORS]}"
            raise ShikiruError(message)
        return _object(_field(response, _Key.DATA))

    async def _run(self, repo_dir: Path, *args: str) -> ProcessResult:
        return await self._runner.run(
            [_GH_BIN, *args], timeout=_COMMAND_TIMEOUT_SECONDS, cwd=repo_dir, check=True
        )
