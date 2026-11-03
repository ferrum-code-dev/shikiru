"""Task roots: one worktree of the project repository per task, and cleanup."""

from __future__ import annotations

import logging
import re
import shutil
from typing import TYPE_CHECKING

from shikiru.domain.tasks import RepoState
from shikiru.errors import StaleTaskBranchError
from shikiru.paths import EXCLUDE_LINE, run_dir, task_root
from shikiru.private import make_private_dir

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from shikiru.config.settings import RepoConfig
    from shikiru.workspace.base_refs import BaseRefs
    from shikiru.workspace.caches import CacheCloner
    from shikiru.workspace.git import Git

BRANCH_PREFIX = "shikiru/"
SEED_COMMIT_MESSAGE = "Carry over the tracker changes of the superseded task"
__all__ = ["EXCLUDE_LINE", "TaskRootBuilder", "branch_name", "run_dir", "slugify", "task_root"]

_SLUG_MAX_LENGTH = 40
_SHORT_SHA = 10
_NON_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")
GIT_LINK_NAME = ".git"
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
    """Builds and tears down `.shikiru/worktrees/T-n/` for a task."""

    def __init__(
        self,
        git: Git,
        project_root: Path,
        repo: RepoConfig,
        cache_cloner: CacheCloner,
        base_refs: BaseRefs,
        *,
        trackers: Sequence[str],
    ) -> None:
        """Build task roots starting from the ref `base_refs` picks, cloning configured caches."""
        self._git = git
        self._project_root = project_root
        self._repo = repo
        self._trackers = trackers
        self._cache_cloner = cache_cloner
        self._base_refs = base_refs
        self._warned_cow_unsupported = False

    async def build(self, task_id: str, title: str, *, seed_branch: str | None = None) -> RepoState:
        """Build (or reuse) `task_id`'s task root: one worktree of the project repository.

        A new root of a replacement task gets, as its first commit, the tracker changes of
        `seed_branch` (the superseded task's branch) and nothing else of it.

        Idempotent: calling this again with the same arguments creates nothing twice, whether
        the branch, the worktree or both already exist.
        """
        root = task_root(self._project_root, task_id)
        make_private_dir(root.parent)
        branch = branch_name(task_id, title)
        project = self._project_root
        repo = self._repo
        base = repo.base if repo.base is not None else await self._git.current_branch(project)
        base_ref = await self._base_refs.resolve(base)
        base_sha = await self._git.rev_parse(project, base_ref)
        await self._git.append_to_exclude(project, EXCLUDE_LINE)
        if not await self._git.worktree_exists(project, root):
            if await self._git.branch_exists(project, branch):
                await self._require_fresh_branch(branch, base_sha)
                await self._git.worktree_add_existing_branch(project, root, branch)
            else:
                await self._git.worktree_add(project, root, new_branch=branch, base=base_ref)
                if seed_branch is not None:
                    await self._seed_trackers(root, branch, base_ref, seed_branch)
        if repo.caches:
            clone_result = await self._cache_cloner.clone(
                project, root, repo.caches, await self._git.ignored_paths(project)
            )
            if clone_result.warning is not None and not self._warned_cow_unsupported:
                _LOGGER.warning("%s", clone_result.warning)
                self._warned_cow_unsupported = True
        return RepoState(base=base, base_ref=base_ref, base_sha=base_sha, branch=branch)

    async def _require_fresh_branch(self, branch: str, base_sha: str) -> None:
        if await self._git.rev_parse(self._project_root, branch) != base_sha:
            message = (
                f"branch {branch} already exists and is not at the base {base_sha[:_SHORT_SHA]}: "
                f"it holds an earlier task's work; inspect it, then delete it "
                f"(git branch -D {branch}) or rename it and submit the task again"
            )
            raise StaleTaskBranchError(message)

    async def _seed_trackers(
        self, root: Path, branch: str, base_ref: str, seed_branch: str
    ) -> None:
        project = self._project_root
        if not self._trackers or not await self._git.branch_exists(project, seed_branch):
            return
        try:
            patch = await self._git.diff_patch(
                project, f"{base_ref}...{seed_branch}", self._trackers
            )
            if patch:
                await self._git.apply_patch(root, patch)
                await self._git.commit(root, SEED_COMMIT_MESSAGE)
        except Exception:
            await self._git.worktree_remove(project, root)
            await self._git.delete_branch(project, branch)
            raise

    async def _holds_unintegrated_work(self, root: Path, repo: RepoState) -> bool:
        if await self._git.worktree_removal_pending(self._project_root, root):
            return False
        if await self._git.head_branch(root) != repo.branch:
            return True
        return bool(await self._git.status_porcelain(root))

    async def cleanup(
        self,
        task_id: str,
        repo: RepoState | None,
        *,
        integrated: bool = False,
        keep_work: bool = False,
    ) -> Path | None:
        """Remove the task's worktree, its merged branch, and last its task root.

        The branch is merged when it is an ancestor of the local base or when the caller says
        the task is `integrated` (a pull request merged on the remote leaves the local base
        behind, and a squash leaves the branch out of its history). Idempotent and tolerant of
        a worktree already removed. Never deletes an unmerged branch, never follows a symlink
        out of the task root, and never erases unintegrated work: a worktree whose `HEAD` is off
        the task branch, one with tracked or untracked changes (git-ignored files excepted) and
        its branch stay, and so does a root that holds anything but the git link once its
        worktree metadata is gone (git cannot tell what is uncommitted there); the kept task
        root is returned. With `keep_work` the caller knows the branch holds commits that are
        nowhere else: its worktree and branch stay. A removal that began (it passed those checks)
        and was interrupted by a failure or timeout leaves a half-deleted worktree that the next
        cleanup removes without taking the deletions for uncommitted work.
        """
        project = self._project_root
        root = task_root(project, task_id)
        if repo is not None:
            if await self._git.worktree_exists(project, root):
                if keep_work or await self._holds_unintegrated_work(root, repo):
                    return root
                await self._git.worktree_remove(project, root)
            if await self._git.branch_exists(project, repo.branch) and (
                integrated
                or await self._git.merge_base_is_ancestor(project, repo.branch, repo.base)
            ):
                await self._git.delete_branch(project, repo.branch)
        if root.exists():
            if repo is not None and any(entry.name != GIT_LINK_NAME for entry in root.iterdir()):
                return root
            shutil.rmtree(root)
        return None
