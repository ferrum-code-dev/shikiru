"""Engine restart recovery (03 §7)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.domain.tasks import Status

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from shikiru.engine.layout_guard import LayoutGuard
    from shikiru.engine.turn_processes import TurnProcesses
    from shikiru.store.tasks import TaskStore

_IN_FLIGHT = (Status.RUNNING, Status.INTEGRATING)


class Reconciler:
    """Restore windows, end orphaned turn processes and re-run in-flight steps at engine start."""

    def __init__(
        self,
        *,
        tasks: TaskStore,
        layout: LayoutGuard,
        processes: TurnProcesses,
        resume_task: Callable[[str], Awaitable[None]],
    ) -> None:
        """Read tasks from `tasks` and resume each in-flight one through `resume_task`."""
        self._tasks = tasks
        self._layout = layout
        self._processes = processes
        self._resume_task = resume_task

    async def reconcile(self) -> None:
        """Bring a restarted engine back in line with the persisted state (03 §7).

        Pending commands need nothing here: the engine loop processes them as usual.
        """
        active = self._tasks.list_active()
        await self._layout.restore(active)
        await self._processes.end_orphans()
        for task in active:
            if task.status in _IN_FLIGHT:
                await self._resume_task(task.id)
