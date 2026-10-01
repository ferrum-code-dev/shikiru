"""The only place gh argv is built: pull requests for `integration = "pr"` (03 §6)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from shikiru.errors import ShikiruError

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.process import ProcessResult, SubprocessRunner

_GH_BIN = "gh"
_COMMAND_TIMEOUT_SECONDS = 60.0
_STATE_FIELD = "state"
_MERGE_COMMIT_FIELD = "mergeCommit"
_MERGEABLE_FIELD = "mergeable"
_CHECKS_FIELD = "statusCheckRollup"
_CONFLICTING = "CONFLICTING"
_CHECK_CONCLUSION_FIELD = "conclusion"
_CHECK_STATE_FIELD = "state"
_FAILING_CHECK_RESULTS = frozenset(
    ("FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "STARTUP_FAILURE", "ACTION_REQUIRED")
)
_CHECK_STATUS_FIELD = "status"
_CHECK_COMPLETED = "COMPLETED"
_PENDING_CHECK_STATES = frozenset(("PENDING", "EXPECTED"))
_OID_FIELD = "oid"
_URL_FIELD = "url"
_OPEN_STATE_FILTER = "open"


class PullRequestState(StrEnum):
    """The pull request states `gh pr view --json state` reports."""

    OPEN = "OPEN"
    CLOSED = "CLOSED"
    MERGED = "MERGED"


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


def _check_pending(check: dict[str, Any]) -> bool:
    if _CHECK_STATUS_FIELD in check:
        return check[_CHECK_STATUS_FIELD] != _CHECK_COMPLETED
    return check.get(_CHECK_STATE_FIELD) in _PENDING_CHECK_STATES


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
            _URL_FIELD,
        )
        pulls = json.loads(result.stdout)
        return pulls[0][_URL_FIELD] if pulls else None

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
        fields = (_STATE_FIELD, _MERGE_COMMIT_FIELD, _MERGEABLE_FIELD, _CHECKS_FIELD)
        result = await self._run(repo_dir, "pr", "view", url, "--json", ",".join(fields))
        view = json.loads(result.stdout)
        merge_commit = view[_MERGE_COMMIT_FIELD]
        return PullRequestStatus(
            state=PullRequestState(view[_STATE_FIELD]),
            merge_commit=merge_commit[_OID_FIELD] if merge_commit is not None else None,
            conflicting=view[_MERGEABLE_FIELD] == _CONFLICTING,
            checks_failing=any(
                {check.get(_CHECK_CONCLUSION_FIELD), check.get(_CHECK_STATE_FIELD)}
                & _FAILING_CHECK_RESULTS
                for check in view[_CHECKS_FIELD]
            ),
            checks_pending=any(_check_pending(check) for check in view[_CHECKS_FIELD]),
        )

    async def close_pull_request(self, repo_dir: Path, url: str) -> None:
        """Close the open pull request at `url` without merging it."""
        await self._run(repo_dir, "pr", "close", url)

    async def _run(self, repo_dir: Path, *args: str) -> ProcessResult:
        return await self._runner.run(
            [_GH_BIN, *args], timeout=_COMMAND_TIMEOUT_SECONDS, cwd=repo_dir, check=True
        )
