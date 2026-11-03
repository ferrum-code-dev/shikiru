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

    Each base's reconciliation and push use its own `TickGuard`, while remote configuration uses
    `remote_guard`. A failed base check, rejected push or timeout is recorded and retried on fault
    backoff without holding back other bases. While work is pending, the service refreshes remote
    refs every `remote_recheck_seconds`; matching local and remote SHAs clear only the registrations
    observed by that check. A remote that is not configured is not a fault: work stays pending and
    the remote is checked again after `remote_recheck_seconds`. A push acknowledges only the
    integrations that were registered when it started.
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
        fetch_timeout_seconds: float,
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
        self._fetch_timeout = fetch_timeout_seconds
        self._task: asyncio.Task[None] | None = None
        self._next_remote_check: datetime | None = None
        self._remote_available: bool | None = None

    def register(self, base: str) -> None:
        """Record an integration of `base` that must reach the remote."""
        current = self._targets.get(base)
        self._targets.register(base, now=self._clock.now(), interval=self._interval)
        if current is None or not current.pending:
            self._remote_available = None
            self._next_remote_check = None

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
        pending = self._targets.pending()
        if not pending:
            return False
        remote_check_due = self._next_remote_check is None or now >= self._next_remote_check
        if remote_check_due:
            return True
        if self._remote_available is False:
            return False
        return any(is_due(target, now=now, interval=self._interval) for target in pending)

    async def _push_due(self) -> None:
        pending = self._targets.pending()
        if not pending:
            return
        now = self._clock.now()
        remote_check_due = self._next_remote_check is None or now >= self._next_remote_check
        push_due = any(is_due(target, now=now, interval=self._interval) for target in pending)
        if not remote_check_due and not push_due:
            return
        configured = await self._remote_guard.attempt(self._remote_configured)
        if configured is None:
            if remote_check_due:
                self._next_remote_check = self._clock.now() + self._remote_recheck
            return
        self._remote_available = configured
        if not configured:
            self._next_remote_check = self._clock.now() + self._remote_recheck
            return
        if remote_check_due:
            self._next_remote_check = self._clock.now() + self._remote_recheck
            for target in pending:
                await self._guard(target.base).attempt(
                    lambda target=target: self._reconcile(target)
                )
        for target in self._targets.pending():
            if is_due(target, now=self._clock.now(), interval=self._interval):
                await self._guard(target.base).attempt(lambda target=target: self._push(target))

    async def _reconcile(self, target: PushTarget) -> None:
        remote_sha = await self._git.fetch_remote_branch(
            self._project_root,
            self._remote,
            target.base,
            timeout=self._fetch_timeout,
        )
        local_sha = await self._git.local_branch_sha(self._project_root, target.base)
        if remote_sha is not None and remote_sha == local_sha:
            self._targets.reconcile(
                target.base,
                registered=target.registered,
                now=self._clock.now(),
                interval=self._interval,
            )

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
