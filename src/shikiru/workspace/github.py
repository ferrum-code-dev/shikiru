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
_PASSING_CHECK_RESULTS = frozenset(("SUCCESS", "NEUTRAL", "SKIPPED"))
_PENDING_CHECK_STATES = frozenset(("PENDING", "EXPECTED"))
_RUNNING_CHECK_STATUSES = frozenset(("QUEUED", "IN_PROGRESS", "WAITING", "PENDING", "REQUESTED"))
_CHECK_RUN_TYPENAME = "CheckRun"
_STATUS_CONTEXT_TYPENAME = "StatusContext"
_UNKNOWN_CHECK_NAME = "unknown check"
_UNKNOWN_CHECK_KIND = "unknown"
_MISSING_CHECK_EVIDENCE = "check evidence unavailable"
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
_AUTO_FLAG = "--auto"
_DISABLE_AUTO_FLAG = "--disable-auto"
_SQUASH_FLAG = "--squash"
_SUBJECT_FLAG = "--subject"
_TITLE_FLAG = "--title"
_BODY_FLAG = "--body"
_MATCH_HEAD_FLAG = "--match-head-commit"

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
    AUTO_MERGE = "autoMergeRequest"
    BODY = "body"
    CHECKS = "statusCheckRollup"
    COMMIT_BODY = "commitBody"
    COMMIT_HEADLINE = "commitHeadline"
    COMMENTS = "comments"
    CONCLUSION = "conclusion"
    CONTEXT = "context"
    CREATED_AT = "createdAt"
    DATA = "data"
    DETAILS_URL = "detailsUrl"
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
    MERGE_METHOD = "mergeMethod"
    MERGEABLE = "mergeable"
    NAME = "name"
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
    TARGET_URL = "targetUrl"
    TYPENAME = "__typename"
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
class FailingCheck:
    """One settled failing check: its name, result, available detail link and GitHub kind.

    `kind` is the `__typename` when present (`CheckRun` or `StatusContext`), else inferred
    from the entry's keys, else `unknown`. `details_url` is None when GitHub gives no link.
    """

    name: str
    result: str
    details_url: str | None
    kind: str


@dataclass(frozen=True, slots=True)
class PullRequestStatus:
    """A pull request's state, whether it can land, and the commit it landed as once merged.

    `checks_failing` is true when any check failed; `checks_pending` when any check has not
    completed yet, so a failure is final only while nothing is pending. `head_sha` is the head
    the evidence describes. `failing_checks` lists each settled failure with its name, result and
    available link. `evidence_error` names retrieval that failed or was unavailable; a status
    with one is never a passing check (`checks_failing` is true).
    """

    state: PullRequestState
    merge_commit: str | None
    conflicting: bool
    checks_failing: bool
    checks_pending: bool
    head_sha: str | None = None
    failing_checks: tuple[FailingCheck, ...] = ()
    evidence_error: str | None = None


@dataclass(frozen=True, slots=True)
class AutoMergeRequest:
    """An auto-merge request GitHub holds for a pull request: its method, subject and body."""

    method: str
    subject: str | None
    body: str


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


def parse_pull_request_number(url: str) -> int:
    """Return the number from a GitHub pull request URL."""
    return _PullRequestAddress.parse(url).number


@dataclass(frozen=True, slots=True)
class _Page:
    nodes: tuple[dict[str, Any], ...]
    next_cursor: str | None


def _check_pending(check: dict[str, Any], kind: str) -> tuple[bool, str | None]:
    """Return whether a check is still running, and an error when its lifecycle is unavailable.

    A check run is pending only in a recognised running status; a missing or unrecognised status
    is unavailable evidence, never a pending or passing check.
    """
    if kind != _CHECK_RUN_TYPENAME:
        state = check.get(_Key.STATE)
        return isinstance(state, str) and state in _PENDING_CHECK_STATES, None
    if _Key.STATUS not in check:
        return False, f"{_MISSING_CHECK_EVIDENCE}: status is missing"
    status = check[_Key.STATUS]
    if status == _CHECK_COMPLETED:
        return False, None
    if isinstance(status, str) and status in _RUNNING_CHECK_STATUSES:
        return True, None
    return False, f"{_MISSING_CHECK_EVIDENCE}: unrecognized status {status!r}"


def _check_kind(check: dict[str, Any]) -> str:
    typename = check.get(_Key.TYPENAME)
    if isinstance(typename, str) and typename in (_CHECK_RUN_TYPENAME, _STATUS_CONTEXT_TYPENAME):
        return typename
    if any(key in check for key in (_Key.NAME, _Key.CONCLUSION, _Key.DETAILS_URL, _Key.STATUS)):
        return _CHECK_RUN_TYPENAME
    if any(key in check for key in (_Key.CONTEXT, _Key.TARGET_URL)):
        return _STATUS_CONTEXT_TYPENAME
    return _UNKNOWN_CHECK_KIND


def _check_result(check: dict[str, Any], kind: str) -> tuple[str | None, str | None]:
    """Return the check's result string, and an error when the result is missing or malformed."""
    keys = (
        (_Key.CONCLUSION, _Key.STATE)
        if kind == _CHECK_RUN_TYPENAME
        else (_Key.STATE, _Key.CONCLUSION)
    )
    for key in keys:
        if key in check:
            value = check[key]
            if value is None:
                return None, None
            if isinstance(value, str):
                return value, None
            return None, f"{_MISSING_CHECK_EVIDENCE}: {key.value} is not a result"
    return None, f"{_MISSING_CHECK_EVIDENCE}: no result field"


def _check_name(check: dict[str, Any], kind: str) -> tuple[str, str | None]:
    """Return a check's name, falling back to `unknown check` with an error when unavailable."""
    keys = (
        (_Key.NAME, _Key.CONTEXT) if kind != _STATUS_CONTEXT_TYPENAME else (_Key.CONTEXT, _Key.NAME)
    )
    for key in keys:
        if key in check:
            value = check[key]
            if isinstance(value, str) and value.strip():
                return value, None
            return _UNKNOWN_CHECK_NAME, f"{_MISSING_CHECK_EVIDENCE}: {key.value} is unavailable"
    return _UNKNOWN_CHECK_NAME, f"{_MISSING_CHECK_EVIDENCE}: no name field"


def _check_details_url(check: dict[str, Any], kind: str) -> tuple[str | None, str | None]:
    """Return the check's detail link, None when GitHub gives none, and an error when malformed."""
    key = _Key.TARGET_URL if kind == _STATUS_CONTEXT_TYPENAME else _Key.DETAILS_URL
    fallback = _Key.DETAILS_URL if kind == _STATUS_CONTEXT_TYPENAME else _Key.TARGET_URL
    for candidate in (key, fallback):
        if candidate in check:
            value = check[candidate]
            if value is None:
                return None, None
            if isinstance(value, str) and value.strip():
                return value, None
            return None, f"{_MISSING_CHECK_EVIDENCE}: {candidate.value} is not a link"
    return None, None


def _one_check(entry: object) -> tuple[list[str], FailingCheck | None, bool]:
    """Reduce one rollup entry to its errors, failing evidence and pending flag.

    A null result on a genuinely running check stays pending without error. A missing or
    unrecognized lifecycle status, or a null, empty or unrecognized result on a settled check, is
    unavailable evidence, never a pass.
    """
    if not isinstance(entry, dict):
        return [f"{_MISSING_CHECK_EVIDENCE}: entry is not an object"], None, False
    check = cast("dict[str, Any]", entry)
    kind = _check_kind(check)
    pending, lifecycle_error = _check_pending(check, kind)
    result, result_error = _check_result(check, kind)
    errors = [error for error in (lifecycle_error, result_error) if error is not None]
    if result is None:
        if not pending and not errors:
            errors.append(f"{_MISSING_CHECK_EVIDENCE}: settled check has no result")
        return errors, None, pending
    if result in _FAILING_CHECK_RESULTS:
        name, name_error = _check_name(check, kind)
        details_url, url_error = _check_details_url(check, kind)
        errors.extend(error for error in (name_error, url_error) if error is not None)
        return (
            errors,
            FailingCheck(name=name, result=result, details_url=details_url, kind=kind),
            pending,
        )
    if result in _PASSING_CHECK_RESULTS:
        return errors, None, pending
    if kind != _CHECK_RUN_TYPENAME and result in _PENDING_CHECK_STATES:
        return errors, None, pending
    errors.append(f"{_MISSING_CHECK_EVIDENCE}: unrecognized result {result!r}")
    return errors, None, pending


def _check_evidence(
    checks: list[Any],
) -> tuple[bool, bool, tuple[FailingCheck, ...], str | None]:
    """Reduce the rollup to failing/pending booleans, failing evidence and a retrieval error."""
    failing: list[FailingCheck] = []
    pending = False
    errors: list[str] = []
    for entry in checks:
        ev, failed, is_pending = _one_check(entry)
        errors.extend(ev)
        pending = pending or is_pending
        if failed is not None:
            failing.append(failed)
    evidence_error = errors[0] if errors else None
    return bool(failing) or evidence_error is not None, pending, tuple(failing), evidence_error


def _merge_commit(merge_commit: object) -> str | None:
    """Return the merged commit, None when unmerged, or raise when malformed."""
    if merge_commit is None:
        return None
    if isinstance(merge_commit, dict):
        oid: object = cast("dict[str, Any]", merge_commit).get(_Key.OID)
        if isinstance(oid, str):
            return oid
    raise _malformed(_Key.MERGE_COMMIT)


def _head_sha(head: object) -> tuple[str | None, str | None]:
    """Return the head the evidence describes, and an error when it is unavailable."""
    if isinstance(head, str) and head.strip():
        return head, None
    if head is None:
        return None, f"{_MISSING_CHECK_EVIDENCE}: head is unavailable"
    return None, f"{_MISSING_CHECK_EVIDENCE}: head is not a commit"


def _rollup(raw_checks: object) -> tuple[bool, bool, tuple[FailingCheck, ...], str | None]:
    """Reduce the raw rollup, reporting a missing or malformed rollup as evidence error."""
    if isinstance(raw_checks, list):
        return _check_evidence(cast("list[Any]", raw_checks))
    if raw_checks is None:
        return True, False, (), f"{_MISSING_CHECK_EVIDENCE}: statusCheckRollup is missing"
    return True, False, (), f"{_MISSING_CHECK_EVIDENCE}: statusCheckRollup is not a list"


def _combined_error(check_error: str | None, head_error: str | None) -> str | None:
    """Join check and head retrieval errors into one visible error, if any."""
    if check_error is None:
        return head_error
    if head_error is None:
        return check_error
    return f"{check_error}; {head_error}"


def parse_pull_request_status(text: str) -> PullRequestStatus:
    """Return the status that `gh pr view --json` printed as `text`.

    Check or head evidence that is missing or malformed is reported in `evidence_error` and forces
    `checks_failing`; any other unreadable field raises `ShikiruError`.
    """
    view = _object(_json(text))
    raw_state = _field(view, _Key.STATE)
    raw_mergeable = _field(view, _Key.MERGEABLE)
    if not isinstance(raw_state, str):
        raise _malformed(_Key.STATE)
    if not isinstance(raw_mergeable, str):
        raise _malformed(_Key.MERGEABLE)
    merge_commit = _merge_commit(_field(view, _Key.MERGE_COMMIT))
    head_sha, head_error = _head_sha(view.get(_Key.HEAD_SHA))
    checks_failing, checks_pending, failing_checks, check_error = _rollup(view.get(_Key.CHECKS))
    evidence_error = _combined_error(check_error, head_error)
    return PullRequestStatus(
        state=PullRequestState(raw_state),
        merge_commit=merge_commit,
        conflicting=raw_mergeable == _CONFLICTING,
        checks_failing=checks_failing or evidence_error is not None,
        checks_pending=checks_pending,
        head_sha=head_sha,
        failing_checks=failing_checks,
        evidence_error=evidence_error,
    )


def parse_auto_merge_request(text: str) -> AutoMergeRequest | None:
    """Return the auto-merge request that `gh pr view --json autoMergeRequest` printed, if any."""
    view = _object(_json(text))
    raw = _field(view, _Key.AUTO_MERGE)
    if raw is None:
        return None
    request = _object(raw)
    return AutoMergeRequest(
        method=_text(request, _Key.MERGE_METHOD),
        subject=_optional_text(request, _Key.COMMIT_HEADLINE),
        body=_optional_text(request, _Key.COMMIT_BODY) or "",
    )


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

    async def edit_pull_request(self, repo_dir: Path, url: str, *, title: str, body: str) -> None:
        """Replace the title and description of the pull request at `url`."""
        await self._run(repo_dir, "pr", "edit", url, _TITLE_FLAG, title, _BODY_FLAG, body)

    async def request_auto_merge(
        self, repo_dir: Path, url: str, *, head: str, subject: str, body: str
    ) -> None:
        """Ask GitHub to squash-merge the pull request at `url` once its requirements are met.

        The squash commit takes `subject` and `body`, and the request applies only while the head
        is still `head`. It never bypasses repository rules.
        """
        await self._run(
            repo_dir,
            "pr",
            "merge",
            url,
            _AUTO_FLAG,
            _SQUASH_FLAG,
            _SUBJECT_FLAG,
            subject,
            _BODY_FLAG,
            body,
            _MATCH_HEAD_FLAG,
            head,
        )

    async def cancel_auto_merge(self, repo_dir: Path, url: str) -> None:
        """Withdraw a pending auto-merge request of the pull request at `url`."""
        await self._run(repo_dir, "pr", "merge", url, _DISABLE_AUTO_FLAG)

    async def pull_request_status(self, repo_dir: Path, url: str) -> PullRequestStatus:
        """Return the state, merge commit, conflict and check evidence of the PR at `url`.

        `checks_failing` and `checks_pending` keep their meaning: a settled failure is final
        only while nothing is pending. `failing_checks` names each settled failure and
        `head_sha` the head it describes. Retrieval that is missing or malformed is visible in
        `evidence_error` and forces `checks_failing` so it is never read as a passing check.
        """
        fields = (_Key.STATE, _Key.MERGE_COMMIT, _Key.MERGEABLE, _Key.CHECKS, _Key.HEAD_SHA)
        result = await self._run(repo_dir, "pr", "view", url, "--json", ",".join(fields))
        return parse_pull_request_status(result.stdout)

    async def auto_merge_request(self, repo_dir: Path, url: str) -> AutoMergeRequest | None:
        """Return the auto-merge request GitHub currently holds for the PR at `url`, if any."""
        result = await self._run(repo_dir, "pr", "view", url, "--json", _Key.AUTO_MERGE)
        return parse_auto_merge_request(result.stdout)

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
