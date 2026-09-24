"""Task roots: one worktree of the project repository per task, and cleanup (03 §5)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from shikiru.config.loader import SHIKIRU_DIR_NAME
from shikiru.domain.tasks import RepoState

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.config.settings import RepoConfig
    from shikiru.workspace.git import Git

WORKTREES_DIR_NAME = "worktrees"
RUNS_DIR_NAME = "runs"
BRANCH_PREFIX = "shikiru/"
EXCLUDE_LINE = f"/{SHIKIRU_DIR_NAME}/"

_SLUG_MAX_LENGTH = 40
_NON_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")


def slugify(title: str) -> str:
    """Turn `title` into a stable, filesystem-safe branch slug.

    Lowercases, replaces every run of non-alphanumeric characters with a single `-`, trims
    leading and trailing `-`, and caps the result at `_SLUG_MAX_LENGTH` characters.
    """
    collapsed = _NON_ALPHANUMERIC.sub("-", title.lower()).strip("-")
    return collapsed[:_SLUG_MAX_LENGTH].strip("-")


def branch_name(task_id: str, title: str) -> str:
    """Build the deterministic task branch name `shikiru/T-n-<slug>`."""
    return f"{BRANCH_PREFIX}{task_id}-{slugify(title)}"


def run_dir(project_root: Path, task_id: str) -> Path:
    """Return `task_id`'s run directory `.shikiru/runs/T-n/` (02 §5)."""
    return project_root / SHIKIRU_DIR_NAME / RUNS_DIR_NAME / task_id


def task_root(project_root: Path, task_id: str) -> Path:
    """Return `task_id`'s task root directory `.shikiru/worktrees/T-n/` (03 §5)."""
    return project_root / SHIKIRU_DIR_NAME / WORKTREES_DIR_NAME / task_id


class TaskRootBuilder:
    """Builds and tears down `.shikiru/worktrees/T-n/` for a task (03 §5)."""

    def __init__(self, git: Git, project_root: Path) -> None:
        """Build task roots for the repository at `project_root`, running git through `git`."""
        self._git = git
        self._project_root = project_root

    def task_root(self, task_id: str) -> Path:
        """Return the task root directory for `task_id`."""
        return task_root(self._project_root, task_id)

    async def build(self, task_id: str, title: str, repo: RepoConfig) -> RepoState:
        """Build (or reuse) `task_id`'s task root: one worktree of the project repository.

        Idempotent: calling this again with the same arguments creates nothing twice, whether
        the branch, the worktree or both already exist.
        """
        root = self.task_root(task_id)
        root.parent.mkdir(parents=True, exist_ok=True)
        branch = branch_name(task_id, title)
        project = self._project_root
        base = repo.base if repo.base is not None else await self._git.current_branch(project)
        await self._git.append_to_exclude(project, EXCLUDE_LINE)
        if not await self._git.worktree_exists(project, root):
            if await self._git.branch_exists(project, branch):
                await self._git.worktree_add_existing_branch(project, root, branch)
            else:
                await self._git.worktree_add(project, root, new_branch=branch, base=base)
        base_sha = await self._git.rev_parse(project, base)
        return RepoState(base=base, base_sha=base_sha, branch=branch)

    async def cleanup(self, task_id: str, repo: RepoState | None) -> None:
        """Remove the task's worktree and delete its branch once merged into base (03 §5).

        Idempotent and tolerant of a worktree already removed. Never deletes an unmerged branch.
        """
        if repo is None:
            return
        project = self._project_root
        root = self.task_root(task_id)
        if await self._git.worktree_exists(project, root):
            await self._git.worktree_remove(project, root)
        if await self._git.branch_exists(
            project, repo.branch
        ) and await self._git.merge_base_is_ancestor(project, repo.branch, repo.base):
            await self._git.delete_branch(project, repo.branch)
