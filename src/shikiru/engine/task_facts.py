"""Git facts of a task's worktree, shared by prompt, evidence and acceptance code (03 §2)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.errors import InvariantError
from shikiru.paths import task_root
from shikiru.workspace.facts import render_facts

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.config.settings import Settings
    from shikiru.domain.tasks import RepoState, Task
    from shikiru.workspace.facts import GitFacts, WorkspaceFacts


def require_repo(task: Task) -> RepoState:
    """Return the task's repo state or raise when its task root does not exist yet."""
    if task.repo is None:
        message = f"{task.id} has no task root yet"
        raise InvariantError(message)
    return task.repo


class TaskFacts:
    """Collects and renders the facts of one task's worktree."""

    def __init__(self, *, git_facts: GitFacts, project_root: Path, settings: Settings) -> None:
        """Collect facts from `git_facts` for worktrees under `project_root`."""
        self._git_facts = git_facts
        self._project_root = project_root
        self._settings = settings

    async def collect(self, task: Task) -> WorkspaceFacts:
        """Collect the raw facts of `task`'s worktree."""
        return await self._git_facts.collect(
            task_root(self._project_root, task.id),
            require_repo(task),
            self._settings.project.trackers,
        )

    async def text(self, task: Task) -> str:
        """Render the facts of `task`'s worktree as prompt markdown."""
        return render_facts(await self.collect(task))
