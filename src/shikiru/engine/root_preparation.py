"""Off-tick task root preparation under a per-tick budget."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Collection
    from datetime import datetime

    from shikiru.clock import Clock
    from shikiru.domain.tasks import RepoState
    from shikiru.workspace.roots import TaskRootBuilder


class RootPreparation:
    """Builds task roots on their own tasks so a slow fetch or cache copy never holds the tick.

    A tick may wait `budget_seconds` in total for the roots it starts; a root that is not ready
    by then keeps building and is collected by a later tick, which never waits for it again. At most
    one build runs at a time and none starts once a tick's budget is spent, so a hung remote costs
    one budget, not one per ready task and tick, and builds in one repository never overlap.
    """

    def __init__(self, roots: TaskRootBuilder, clock: Clock, *, budget_seconds: float) -> None:
        """Build through `roots`, measuring each tick's budget on `clock`."""
        self._roots = roots
        self._clock = clock
        self._budget = timedelta(seconds=budget_seconds)
        self._expires: datetime = clock.now()
        self._builds: dict[str, asyncio.Task[RepoState]] = {}

    def begin_tick(self, queued: Collection[str]) -> None:
        """Start a tick's budget and let go of the builds of tasks that are no longer `queued`.

        A build in flight for such a task (cancelled meanwhile) is cancelled, and counts as
        running until it has ended; a finished one is dropped.
        """
        self._expires = self._clock.now() + self._budget
        for task_id, build in tuple(self._builds.items()):
            if task_id in queued:
                continue
            if not build.done():
                build.cancel()
                continue
            del self._builds[task_id]
            if not build.cancelled():
                build.exception()

    async def prepare(self, task_id: str, title: str, seed_branch: str | None) -> RepoState | None:
        """Return `task_id`'s built root, or `None` while it is not ready within this tick.

        Raises what the build raised.
        """
        build = self._builds.get(task_id)
        if build is None:
            remaining = (self._expires - self._clock.now()).total_seconds()
            if remaining <= 0 or any(not other.done() for other in self._builds.values()):
                return None
            build = asyncio.create_task(self._roots.build(task_id, title, seed_branch=seed_branch))
            self._builds[task_id] = build
            await self._wait(build, remaining)
        if not build.done():
            return None
        del self._builds[task_id]
        return build.result()

    async def shutdown(self) -> None:
        """Cancel every build in flight and wait until its processes are gone."""
        builds = tuple(self._builds.values())
        self._builds.clear()
        for build in builds:
            build.cancel()
        await asyncio.gather(*builds, return_exceptions=True)

    async def _wait(self, build: asyncio.Task[RepoState], seconds: float) -> None:
        timer = asyncio.create_task(self._clock.sleep(seconds))
        try:
            await asyncio.wait({build, timer}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            timer.cancel()
            with suppress(asyncio.CancelledError):
                await timer
