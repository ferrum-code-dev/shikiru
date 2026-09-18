"""Task roots: monorepo and multi-repo worktrees, symlinks and cleanup (03 §5)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from shikiru.domain.tasks import RepoState
from shikiru.workspace.repo_paths import is_monorepo_path, repo_dir

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from shikiru.config.settings import RepoConfig
    from shikiru.workspace.git import Git

SHIKIRU_DIR_NAME = ".shikiru"
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


def _is_monorepo(repos: Sequence[RepoConfig]) -> bool:
    return len(repos) == 1 and is_monorepo_path(repos[0].path)


class TaskRootBuilder:
    """Builds and tears down `.shikiru/worktrees/T-n/` for a task (03 §5)."""

    def __init__(self, git: Git, project_root: Path) -> None:
        """Build task roots for repos under `project_root`, running git through `git`."""
        self._git = git
        self._project_root = project_root

    def task_root(self, task_id: str) -> Path:
        """Return the task root directory for `task_id`."""
        return self._project_root / SHIKIRU_DIR_NAME / WORKTREES_DIR_NAME / task_id

    async def build(self, task_id: str, title: str, repos: Sequence[RepoConfig]) -> list[RepoState]:
        """Build (or reuse) the task root and every repo's worktree for `task_id`.

        Idempotent: calling this again with the same arguments creates nothing twice, whether
        the branch, the worktree or both already exist.
        """
        root = self.task_root(task_id)
        root.mkdir(parents=True, exist_ok=True)
        branch = branch_name(task_id, title)
        states = [await self._ensure_repo(root, repo, branch) for repo in repos]
        if not _is_monorepo(repos):
            self._symlink_shared_entries(root, repos)
        return states

    async def cleanup(self, task_id: str, repos: Sequence[RepoState]) -> None:
        """Remove every repo's worktree and delete its branch once merged into base (03 §5).

        Idempotent and tolerant of a worktree already removed. Never deletes an unmerged branch.
        """
        root = self.task_root(task_id)
        for repo in repos:
            repo_dir = self._repo_dir(repo.path)
            worktree_path = self._worktree_path(root, repo.path)
            if await self._git.worktree_exists(repo_dir, worktree_path):
                await self._git.worktree_remove(repo_dir, worktree_path)
            if await self._git.branch_exists(
                repo_dir, repo.branch
            ) and await self._git.merge_base_is_ancestor(repo_dir, repo.branch, repo.base):
                await self._git.delete_branch(repo_dir, repo.branch)

    async def _ensure_repo(self, root: Path, repo: RepoConfig, branch: str) -> RepoState:
        repo_dir = self._repo_dir(repo.path)
        worktree_path = self._worktree_path(root, repo.path)
        base = repo.base if repo.base is not None else await self._git.current_branch(repo_dir)
        await self._git.append_to_exclude(repo_dir, EXCLUDE_LINE)
        if not await self._git.worktree_exists(repo_dir, worktree_path):
            if await self._git.branch_exists(repo_dir, branch):
                await self._git.worktree_add_existing_branch(repo_dir, worktree_path, branch)
            else:
                await self._git.worktree_add(repo_dir, worktree_path, new_branch=branch, base=base)
        base_sha = await self._git.rev_parse(repo_dir, base)
        return RepoState(path=repo.path, base=base, base_sha=base_sha, branch=branch)

    def _repo_dir(self, repo_path: str) -> Path:
        return repo_dir(self._project_root, repo_path)

    def _worktree_path(self, root: Path, repo_path: str) -> Path:
        return repo_dir(root, repo_path)

    def _symlink_shared_entries(self, root: Path, repos: Sequence[RepoConfig]) -> None:
        repo_top_level = {repo.path.split("/", 1)[0] for repo in repos}
        for entry in self._project_root.iterdir():
            if entry.name == SHIKIRU_DIR_NAME or entry.name in repo_top_level:
                continue
            link = root / entry.name
            if link.exists() or link.is_symlink():
                continue
            link.symlink_to(entry, target_is_directory=entry.is_dir())
