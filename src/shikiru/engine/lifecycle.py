"""Shared terminal task cleanup (03 §1.2, §6)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from shikiru.domain.tasks import Task
    from shikiru.engine.layout_guard import LayoutGuard
    from shikiru.engine.sessions import SessionManager
    from shikiru.workspace.roots import TaskRootBuilder


class TaskFinisher:
    """Close task sessions and window, then remove its worktree once."""

    def __init__(
        self, sessions: SessionManager, layout: LayoutGuard, roots: TaskRootBuilder
    ) -> None:
        self._sessions = sessions
        self._layout = layout
        self._roots = roots
        self._finished_tasks: set[str] = set()

    async def finish_task(self, task: Task) -> None:
        """Release task resources after it reaches a terminal lifecycle state."""
        if task.id in self._finished_tasks:
            return
        self._sessions.close_task(task.id)
        await self._roots.cleanup(task.id, task.repo)
        await self._layout.close(task)
        self._finished_tasks.add(task.id)
