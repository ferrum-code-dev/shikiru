"""Off-loop publication of locally integrated base branches to the remote."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import timedelta
from typing import TYPE_CHECKING

from shikiru.domain.pushes import is_due

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime
    from logging import Logger
    from pathlib import Path

    from shikiru.clock import Clock
    from shikiru.domain.pushes import PushTarget
    from shikiru.engine.tick_guard import TickGuard
    from shikiru.store.pushes import PushTargets
    from shikiru.workspace.git import Git

PUSH_FAULT_SCOPE = "base push"
REMOTE_CHECK_FAULT_SCOPE = "remote check"


class PushService:
    """Registers integrated bases and pushes each when its deadline comes, on its own task.

    Each base pushes behind its own `TickGuard`, and the remote is probed behind `remote_guard`, so
    a failed probe, a rejected push or a timeout is recorded as a fault, keeps the work pending and
    is retried on the fault backoff without holding back the other bases, the engine loop or task
    dispatch. A remote that is not configured is not a fault: nothing is pushed, the work stays
    pending, and the remote is checked again after `remote_recheck_seconds`. A push acknowledges
    only the integrations that were registered when it started.
    """

    def __init__(
        self,
        *,
        targets: PushTargets,
        git: Git,
        guard_for: Callable[[str], TickGuard],
        remote_guard: TickGuard,
        clock: Clock,
        project_root: Path,
        remote: str,
        interval_seconds: int,
        remote_recheck_seconds: int,
    ) -> None:
        """Push from `project_root` to `remote`, an interval of `interval_seconds` apart."""
        self._targets = targets
        self._git = git
        self._guard_for = guard_for
        self._guards: dict[str, TickGuard] = {}
        self._remote_guard = remote_guard
        self._clock = clock
        self._project_root = project_root
        self._remote = remote
        self._interval = interval_seconds
        self._remote_recheck = timedelta(seconds=remote_recheck_seconds)
        self._task: asyncio.Task[None] | None = None
        self._next_remote_check: datetime | None = None

    def register(self, base: str) -> None:
        """Record an integration of `base` that must reach the remote."""
        self._targets.register(base, now=self._clock.now(), interval=self._interval)

    def poll(self, logger: Logger) -> None:
        """Log a failed push task and start one when work is due and none is running."""
        task = self._task
        if task is not None:
            if not task.done():
                return
            self._task = None
            if not task.cancelled() and (error := task.exception()) is not None:
                logger.error("base push failed", exc_info=(type(error), error, error.__traceback__))
        if self._due():
            self._task = asyncio.create_task(self._push_due())

    async def finish_outstanding(self) -> None:
        """Wait for the push in flight, if there is one."""
        task = self._task
        if task is not None:
            await asyncio.wait((task,))

    async def shutdown(self) -> None:
        """Cancel an outstanding push; its pending work is sent by the next engine start."""
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    def _due(self) -> bool:
        now = self._clock.now()
        if self._next_remote_check is not None and now < self._next_remote_check:
            return False
        return any(
            is_due(target, now=now, interval=self._interval) for target in self._targets.pending()
        )

    async def _push_due(self) -> None:
        configured = await self._remote_guard.attempt(self._remote_configured)
        if configured is None:
            return
        if not configured:
            self._next_remote_check = self._clock.now() + self._remote_recheck
            return
        for target in self._targets.pending():
            if is_due(target, now=self._clock.now(), interval=self._interval):
                await self._guard(target.base).attempt(lambda target=target: self._push(target))

    async def _remote_configured(self) -> bool:
        return await self._git.remote_configured(self._project_root, self._remote)

    def _guard(self, base: str) -> TickGuard:
        if base not in self._guards:
            self._guards[base] = self._guard_for(base)
        return self._guards[base]

    async def _push(self, target: PushTarget) -> bool:
        await self._git.push_base(self._project_root, self._remote, target.base)
        self._targets.acknowledge(
            target.base,
            registered=target.registered,
            now=self._clock.now(),
            interval=self._interval,
        )
        return True
