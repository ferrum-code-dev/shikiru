"""Supervise concurrent task flows for the engine daemon."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import TYPE_CHECKING

from shikiru.domain.tasks import Status, StatusReason

if TYPE_CHECKING:
    from logging import Logger

    from shikiru.engine.flow import TaskFlow
    from shikiru.engine.lifecycle import TaskFinisher
    from shikiru.store.tasks import TaskStore

_IN_FLIGHT = (Status.RUNNING, Status.INTEGRATING)


class TaskSupervisor:
    """Start, interrupt and reap the task flows owned by one engine daemon."""

    def __init__(self, tasks: TaskStore, flow: TaskFlow, finisher: TaskFinisher) -> None:
        self._tasks = tasks
        self._flow = flow
        self._finisher = finisher
        self._stop = asyncio.Event()
        self._flow_tasks: dict[str, asyncio.Task[None]] = {}

    @property
    def stop_requested(self) -> bool:
        return self._stop.is_set()

    def request_stop(self) -> None:
        """Ask the daemon command loop to stop after acknowledging the current command."""
        self._stop.set()

    async def start_task(self, task_id: str) -> None:
        """Schedule a task flow unless one is still live; `ensure_flows` covers a finishing one."""
        self._start(task_id, resumed=False)

    async def resume_task(self, task_id: str) -> None:
        """Schedule the flow of a task whose step an engine restart interrupted."""
        self._start(task_id, resumed=True)

    async def ensure_flows(self) -> None:
        """Start a resumed flow for each unheld running or integrating task without one."""
        for task in self._tasks.list_active():
            if task.status in _IN_FLIGHT and not task.held:
                self._start(task.id, resumed=True)

    def _start(self, task_id: str, *, resumed: bool) -> None:
        existing = self._flow_tasks.get(task_id)
        if existing is not None and not existing.done():
            return
        self._flow_tasks[task_id] = asyncio.create_task(self._run_flow(task_id, resumed=resumed))

    async def cancel_task(self, task_id: str) -> None:
        """Interrupt and await one supervised flow coroutine, if present."""
        current = self._flow_tasks.get(task_id)
        if current is None:
            return
        current.cancel()
        with suppress(asyncio.CancelledError):
            await current
        self._flow_tasks.pop(task_id, None)

    async def supervise(self, logger: Logger) -> None:
        """Log task flow failures and remove completed flows from the active set."""
        for task_id, flow_task in tuple(self._flow_tasks.items()):
            if not flow_task.done():
                continue
            self._flow_tasks.pop(task_id, None)
            if flow_task.cancelled():
                continue
            error = flow_task.exception()
            if error is not None:
                logger.error(
                    "task flow %s failed",
                    task_id,
                    exc_info=(type(error), error, error.__traceback__),
                )

    async def shutdown(self) -> None:
        """Cancel and await all task flows before the daemon closes its resources."""
        for task in tuple(self._flow_tasks.values()):
            task.cancel()
        if self._flow_tasks:
            await asyncio.gather(*self._flow_tasks.values(), return_exceptions=True)
        self._flow_tasks.clear()

    async def _run_flow(self, task_id: str, *, resumed: bool) -> None:
        try:
            await self._flow.run(task_id, resumed=resumed)
        finally:
            current = self._tasks.get(task_id)
            if current is not None and (
                current.status is Status.DONE or current.status_reason is StatusReason.SUPERSEDED
            ):
                await self._finisher.finish_task(current)
