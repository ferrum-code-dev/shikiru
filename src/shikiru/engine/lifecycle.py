"""Shared terminal task cleanup (03 §1.2, §6)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

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

    async def finish_task(self, task: Task) -> Path | None:
        """Release task resources after it reaches a terminal lifecycle state.

        The worktree goes last, so a task root left behind marks unfinished cleanup (03 §7).
        Returns the task root when uncommitted work kept it in place.
        """
        if task.id in self._finished_tasks:
            return None
        self._sessions.close_task(task.id)
        await self._layout.close(task)
        kept = await self._roots.cleanup(task.id, task.repo)
        self._finished_tasks.add(task.id)
        return kept
