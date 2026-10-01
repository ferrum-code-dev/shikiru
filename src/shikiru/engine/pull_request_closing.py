"""Off-loop, retried closing of the pull requests of cancelled tasks (03 §6)."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import TYPE_CHECKING

from shikiru.domain.faults import next_backoff
from shikiru.domain.tasks import Status
from shikiru.engine.fault_reporter import RECOVERABLE_FAULTS

if TYPE_CHECKING:
    from logging import Logger

    from shikiru.clock import Clock
    from shikiru.config.settings import OrchestrationConfig
    from shikiru.engine.fault_reporter import FaultReporter
    from shikiru.engine.integration import PullRequestCloser
    from shikiru.store.tasks import TaskStore

CLOSE_FAULT_SCOPE = "pull request close"


class PullRequestClosing:
    """Closes cancelled tasks' open pull requests on its own task, never on the command loop.

    A failed close is recorded through the `FaultReporter` and retried on a doubling backoff for
    `network.pr_close_attempts` tries; after that it waits for the next engine start, whose
    `restore` queues every cancelled task whose pull request is still open.
    """

    def __init__(
        self,
        *,
        closer: PullRequestCloser,
        tasks: TaskStore,
        faults: FaultReporter,
        clock: Clock,
        settings: OrchestrationConfig,
    ) -> None:
        self._closer = closer
        self._tasks = tasks
        self._faults = faults
        self._clock = clock
        self._settings = settings
        self._queue: dict[str, None] = {}
        self._pending = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Start the closing task."""
        self._task = asyncio.create_task(self._run())

    def request(self, task_id: str) -> None:
        """Queue closing the open pull request of cancelled `task_id`, if it has one."""
        self._queue[task_id] = None
        self._pending.set()

    def has_open(self, task_id: str) -> bool:
        """Return whether `task_id` has a pull request that was neither closed nor withdrawn."""
        return self._closer.has_open(task_id)

    def restore(self) -> None:
        """Queue every cancelled task whose pull request is still open."""
        for task in self._tasks.list_all():
            if task.status is Status.CANCELLED and self.has_open(task.id):
                self.request(task.id)

    def supervise(self, logger: Logger) -> None:
        """Log a closing task failure and start a replacement task."""
        task = self._task
        if task is None or not task.done():
            return
        if not task.cancelled() and (error := task.exception()) is not None:
            logger.error(
                "pull request closing failed", exc_info=(type(error), error, error.__traceback__)
            )
        self.start()

    async def shutdown(self) -> None:
        """Cancel the closing task; queued closes are repeated by the next engine start."""
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _run(self) -> None:
        while True:
            if not self._queue:
                self._pending.clear()
                await self._pending.wait()
                continue
            task_id = next(iter(self._queue))
            del self._queue[task_id]
            await self._close(task_id)

    async def _close(self, task_id: str) -> None:
        faults = self._settings.faults
        delay = 0.0
        for attempt in range(1, self._settings.network.pr_close_attempts + 1):
            try:
                await self._closer.close_open(task_id)
            except RECOVERABLE_FAULTS as error:
                self._faults.report(error, scope=CLOSE_FAULT_SCOPE, task_id=task_id)
                delay = next_backoff(
                    delay,
                    initial=faults.backoff_initial_seconds,
                    maximum=faults.backoff_max_seconds,
                )
                if attempt < self._settings.network.pr_close_attempts:
                    await self._clock.sleep(delay)
            else:
                self._faults.resolve(CLOSE_FAULT_SCOPE, task_id=task_id)
                return
