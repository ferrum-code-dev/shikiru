"""Engine restart recovery (03 §7)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.domain.tasks import Status
from shikiru.paths import task_root

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

    from shikiru.domain.tasks import Task
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
        finish_task: Callable[[Task], Awaitable[None]],
        restore_escalation: Callable[[Task], Awaitable[None]],
        project_root: Path,
    ) -> None:
        """Resume in-flight tasks, finish terminal ones, restore escalations of escalated ones."""
        self._tasks = tasks
        self._layout = layout
        self._processes = processes
        self._resume_task = resume_task
        self._finish_task = finish_task
        self._restore_escalation = restore_escalation
        self._project_root = project_root

    async def reconcile(self) -> None:
        """Bring a restarted engine back in line with the persisted state (03 §7).

        Pending commands need nothing here: the engine loop processes them as usual.
        """
        active = self._tasks.list_active()
        await self._layout.restore(active)
        await self._processes.end_orphans()
        for task in self._tasks.list_all():
            if task.status in _TERMINAL and task_root(self._project_root, task.id).exists():
                await self._finish_task(task)
        await self.restore_escalations()
        for task in active:
            if task.status in _IN_FLIGHT and not task.held:
                await self._resume_task(task.id)

    async def restore_escalations(self) -> None:
        """Give every escalated task an open escalation with its packet on disk (03 §7, §8)."""
        for task in self._tasks.list_active():
            if task.status is Status.ESCALATED:
                await self._restore_escalation(task)
