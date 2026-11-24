"""Engine restart recovery."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.domain.tasks import Status
from shikiru.paths import task_root

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

    from shikiru.domain.tasks import Task
    from shikiru.engine.fault_reporter import FaultReporter
    from shikiru.engine.layout_guard import LayoutGuard
    from shikiru.engine.turn_processes import TurnProcesses
    from shikiru.store.tasks import TaskStore

_IN_FLIGHT = (Status.RUNNING, Status.INTEGRATING)
_TERMINAL = (Status.DONE, Status.CANCELLED)


class Reconciler:
    """Restore windows, end orphaned turn processes and re-run in-flight steps at engine start."""

    def __init__(
        self,
        *,
        tasks: TaskStore,
        layout: LayoutGuard,
        processes: TurnProcesses,
        resume_task: Callable[[str], Awaitable[None]],
        finish_task: Callable[[Task], Awaitable[Path | None]],
        restore_escalation: Callable[[Task], Awaitable[None]],
        restore_pull_request_closes: Callable[[], None],
        faults: FaultReporter,
        project_root: Path,
    ) -> None:
        """Resume in-flight tasks, finish terminal ones, restore escalations of escalated ones.

        A fault in one step or task is recorded through `faults`; the rest goes on.
        """
        self._tasks = tasks
        self._layout = layout
        self._processes = processes
        self._resume_task = resume_task
        self._finish_task = finish_task
        self._restore_escalation = restore_escalation
        self._restore_pull_request_closes = restore_pull_request_closes
        self._faults = faults
        self._project_root = project_root
        self._orphans_ended = False

    async def reconcile(self) -> None:
        """Bring a restarted engine back in line with the persisted state.

        Pending commands need nothing here: the engine loop processes them once this has
        completed. A fault in one step or one task is recorded and never stops the others.
        Ending the orphaned turn processes is the exception: a fault there aborts the reconcile,
        which is retried before anything starts, so no flow runs beside a live orphan and none
        ends a turn the engine started itself.
        """
        active = self._tasks.list_active()
        terminal = [
            task
            for task in self._tasks.list_all()
            if task.status in _TERMINAL and task_root(self._project_root, task.id).exists()
        ]
        async with self._faults.isolate("reconcile layout"):
            await self._layout.restore(active)
        if not self._orphans_ended:
            await self._processes.end_orphans()
            self._orphans_ended = True
        for task in terminal:
            async with self._faults.isolate("reconcile cleanup", task_id=task.id):
                await self._finish_task(task)
        async with self._faults.isolate("reconcile pull request closes"):
            self._restore_pull_request_closes()
        await self.restore_escalations()
        for task in active:
            if task.status in _IN_FLIGHT and not task.held:
                async with self._faults.isolate("reconcile resume", task_id=task.id):
                    await self._resume_task(task.id)

    async def restore_escalations(self) -> None:
        """Give every escalated task an open escalation with its packet on disk."""
        for task in self._tasks.list_active():
            if task.status is Status.ESCALATED:
                async with self._faults.isolate("escalation restore", task_id=task.id):
                    await self._restore_escalation(task)
