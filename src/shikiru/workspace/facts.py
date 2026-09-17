"""Git facts for `facts.md`, computed per repo and outranking the Implementer's claims (03 §2)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from shikiru.domain.tasks import RepoState
    from shikiru.workspace.git import Git


def _tracker_relative_to_repo(tracker: str, repo_path: str) -> str | None:
    if repo_path == ".":
        return tracker
    prefix = f"{repo_path.rstrip('/')}/"
    if tracker.startswith(prefix):
        return tracker[len(prefix) :]
    return None


@dataclass(frozen=True, slots=True)
class RepoFacts:
    """The facts computed for one repo."""

    path: str
    commits: tuple[str, ...] = field(default_factory=tuple)
    diff_stat: str = ""
    status: tuple[str, ...] = field(default_factory=tuple)
    changed_trackers: tuple[str, ...] = field(default_factory=tuple)
    ahead_of_base: bool = False


@dataclass(frozen=True, slots=True)
class WorkspaceFacts:
    """The facts computed for every repo of a task."""

    repos: tuple[RepoFacts, ...] = field(default_factory=tuple)


class GitFacts:
    """Computes `RepoFacts` for every repo of a task, from real git state."""

    def __init__(self, git: Git) -> None:
        """Read git state through `git`."""
        self._git = git

    async def collect(
        self, task_root: Path, repos: Sequence[RepoState], trackers: Sequence[str]
    ) -> WorkspaceFacts:
        """Collect facts for every repo in `repos`, whose worktrees sit under `task_root`."""
        repo_facts = [await self._collect_one(task_root, repo, trackers) for repo in repos]
        return WorkspaceFacts(repos=tuple(repo_facts))

    async def _collect_one(
        self, task_root: Path, repo: RepoState, trackers: Sequence[str]
    ) -> RepoFacts:
        repo_dir = task_root if repo.path == "." else task_root / repo.path
        commit_range = f"{repo.base}..HEAD"
        diff_range = f"{repo.base}...HEAD"
        commits = await self._git.log_oneline(repo_dir, commit_range)
        diff_stat = await self._git.diff_stat(repo_dir, diff_range)
        status = await self._git.status_porcelain(repo_dir)
        changed_paths = set(await self._git.diff_name_only(repo_dir, diff_range))
        changed_trackers = tuple(
            tracker
            for tracker in trackers
            if (relative := _tracker_relative_to_repo(tracker, repo.path)) is not None
            and relative in changed_paths
        )
        return RepoFacts(
            path=repo.path,
            commits=tuple(commits),
            diff_stat=diff_stat,
            status=tuple(status),
            changed_trackers=changed_trackers,
            ahead_of_base=bool(commits),
        )
