"""The service behind the fake `gh`: lands or closes the pull requests the engine opens."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from shikiru.clock import SystemClock
from shikiru.process import SubprocessRunner
from shikiru.workspace.github import PullRequestState
from tests.support.github import MergeStyle, merge_on_remote

if TYPE_CHECKING:
    import random

    from tests.soak.arena import Arena

CLOCK = SystemClock()
RUNNER = SubprocessRunner()
DECISION_DELAY_SECONDS = (0.5, 3.0)
CLOSE_PROBABILITY = 0.08
BASE_BRANCH = "main"
OPEN = PullRequestState.OPEN.value
GIT_TIMEOUT_SECONDS = 30.0


class Hosting:
    """Decides once per pull request when and how it ends, then does it."""

    def __init__(self, arena: Arena, rng: random.Random) -> None:
        self._arena = arena
        self._rng = rng
        self._due: dict[int, tuple[datetime, bool, MergeStyle]] = {}
        self._done: set[int] = set()
        self.landed = 0
        self.closed = 0

    async def serve(self) -> None:
        github = self._arena.github
        assert github is not None
        if not self._arena.remote.exists():
            return
        for number, record in enumerate(github.pull_requests(), start=1):
            if number in self._done or record["state"] != OPEN:
                continue
            due, close, style = self._due.setdefault(number, self._decide())
            if CLOCK.now() < due:
                continue
            self._done.add(number)
            if close:
                github.set_state(number, PullRequestState.CLOSED)
                self.closed += 1
            else:
                landed = await merge_on_remote(
                    self._arena.remote,
                    BASE_BRANCH,
                    record["head"],
                    await self._style(style, record["head"]),
                )
                github.merge(number, landed)
                self.landed += 1

    def _decide(self) -> tuple[datetime, bool, MergeStyle]:
        due = CLOCK.now() + timedelta(seconds=self._rng.uniform(*DECISION_DELAY_SECONDS))
        return due, self._rng.random() < CLOSE_PROBABILITY, self._rng.choice(list(MergeStyle))

    async def _style(self, style: MergeStyle, head: str) -> MergeStyle:
        if style is not MergeStyle.FAST_FORWARD:
            return style
        result = await RUNNER.run(
            ["git", "merge-base", "--is-ancestor", BASE_BRANCH, head],
            timeout=GIT_TIMEOUT_SECONDS,
            cwd=self._arena.remote,
        )
        return style if result.returncode == 0 else MergeStyle.MERGE_COMMIT
