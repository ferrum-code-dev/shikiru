"""The only place gh argv is built: pull requests for `integration = "pr"` (03 §6)."""

from __future__ import annotations

import json
from enum import StrEnum
from typing import TYPE_CHECKING

from shikiru.errors import ShikiruError

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.process import ProcessResult, SubprocessRunner

_GH_BIN = "gh"
_COMMAND_TIMEOUT_SECONDS = 60.0
_STATE_FIELD = "state"


class PullRequestState(StrEnum):
    """The pull request states `gh pr view --json state` reports."""

    OPEN = "OPEN"
    CLOSED = "CLOSED"
    MERGED = "MERGED"


class GitHub:
    """A small wrapper over the gh CLI, always through `SubprocessRunner`."""

    def __init__(self, runner: SubprocessRunner) -> None:
        """Run every gh command through `runner`, never a shell."""
        self._runner = runner

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

    async def pull_request_state(self, repo_dir: Path, url: str) -> PullRequestState:
        """Return the current state of the pull request at `url`."""
        result = await self._run(repo_dir, "pr", "view", url, "--json", _STATE_FIELD)
        return PullRequestState(json.loads(result.stdout)[_STATE_FIELD])

    async def _run(self, repo_dir: Path, *args: str) -> ProcessResult:
        return await self._runner.run(
            [_GH_BIN, *args], timeout=_COMMAND_TIMEOUT_SECONDS, cwd=repo_dir, check=True
        )
