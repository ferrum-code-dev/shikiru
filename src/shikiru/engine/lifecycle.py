"""Shared terminal task cleanup (03 §1.2, §6)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.domain.tasks import Status

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.domain.tasks import Task
    from shikiru.engine.integration import PullRequestCloser
    from shikiru.engine.layout_guard import LayoutGuard
    from shikiru.engine.sessions import SessionManager
    from shikiru.workspace.roots import TaskRootBuilder


class TaskFinisher:
    """Close task sessions and window, close a cancelled task's PR, then remove its worktree."""

    def __init__(
        self,
        sessions: SessionManager,
        layout: LayoutGuard,
        roots: TaskRootBuilder,
        pull_requests: PullRequestCloser,
    ) -> None:
        self._sessions = sessions
        self._layout = layout
        self._roots = roots
        self._pull_requests = pull_requests
        self._finished_tasks: set[str] = set()

    async def finish_task(self, task: Task) -> Path | None:
        """Release task resources after it reaches a terminal lifecycle state.

        A cancelled task's open pull request is closed before the worktree goes. The worktree
        goes last, so a task root left behind marks unfinished cleanup, a failed close included
        (03 §7).
        Returns the task root when uncommitted work kept it in place.
        """
        if task.id in self._finished_tasks:
            return None
        self._sessions.close_task(task.id)
        await self._layout.close(task)
        if task.status is Status.CANCELLED:
            await self._pull_requests.close_open(task.id)
        kept = await self._roots.cleanup(task.id, task.repo)
        self._finished_tasks.add(task.id)
        return kept
