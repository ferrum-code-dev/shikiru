"""Task roots: one worktree of the project repository per task, and cleanup (03 §5)."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from shikiru.domain.tasks import RepoState
from shikiru.paths import EXCLUDE_LINE, run_dir, task_root

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.config.settings import RepoConfig
    from shikiru.workspace.caches import CacheCloner
    from shikiru.workspace.git import Git

BRANCH_PREFIX = "shikiru/"
__all__ = ["EXCLUDE_LINE", "TaskRootBuilder", "branch_name", "run_dir", "slugify", "task_root"]

_SLUG_MAX_LENGTH = 40
_NON_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")
_LOGGER = logging.getLogger(__name__)


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


class TaskRootBuilder:
    """Builds and tears down `.shikiru/worktrees/T-n/` for a task (03 §5)."""

    def __init__(
        self, git: Git, project_root: Path, repo: RepoConfig, cache_cloner: CacheCloner
    ) -> None:
        """Build task roots with the project's repo settings and clone its configured caches."""
        self._git = git
        self._project_root = project_root
        self._repo = repo
        self._cache_cloner = cache_cloner
        self._warned_cow_unsupported = False

    async def build(self, task_id: str, title: str) -> RepoState:
        """Build (or reuse) `task_id`'s task root: one worktree of the project repository.

        Idempotent: calling this again with the same arguments creates nothing twice, whether
        the branch, the worktree or both already exist.
        """
        root = task_root(self._project_root, task_id)
        root.parent.mkdir(parents=True, exist_ok=True)
        branch = branch_name(task_id, title)
        project = self._project_root
        repo = self._repo
        base = repo.base if repo.base is not None else await self._git.current_branch(project)
        await self._git.append_to_exclude(project, EXCLUDE_LINE)
        if not await self._git.worktree_exists(project, root):
            if await self._git.branch_exists(project, branch):
                await self._git.worktree_add_existing_branch(project, root, branch)
            else:
                await self._git.worktree_add(project, root, new_branch=branch, base=base)
        if repo.caches:
            clone_result = await self._cache_cloner.clone(
                project, root, repo.caches, await self._git.ignored_paths(project)
            )
            if clone_result.warning is not None and not self._warned_cow_unsupported:
                _LOGGER.warning("%s", clone_result.warning)
                self._warned_cow_unsupported = True
        base_sha = await self._git.rev_parse(project, base)
        return RepoState(base=base, base_sha=base_sha, branch=branch)

    async def cleanup(self, task_id: str, repo: RepoState | None) -> None:
        """Remove the task's worktree and delete its branch once merged into base (03 §5).

        Idempotent and tolerant of a worktree already removed. Never deletes an unmerged branch.
        """
        if repo is None:
            return
        project = self._project_root
        root = task_root(project, task_id)
        if await self._git.worktree_exists(project, root):
            await self._git.worktree_remove(project, root)
        if await self._git.branch_exists(
            project, repo.branch
        ) and await self._git.merge_base_is_ancestor(project, repo.branch, repo.base):
            await self._git.delete_branch(project, repo.branch)
