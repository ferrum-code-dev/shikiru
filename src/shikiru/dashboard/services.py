"""Real `WidgetServices` implementations, wired by `bootstrap` (docs/design/06-operator.md §5.4).

`engine_up` mirrors 02 §1's liveness check: a non-blocking `flock` attempt on
`.shikiru/engine.lock`, held by the engine for its lifetime. `GitRepoStatusService` is the
`header` widget's only source of git data, cached for `REPO_STATUS_CACHE_TTL_SECONDS` so a
0.5 s dashboard refresh never spawns a git process on every tick.
"""

from __future__ import annotations

import fcntl
from dataclasses import dataclass
from typing import TYPE_CHECKING

from shikiru.config.loader import SHIKIRU_DIR_NAME
from shikiru.dashboard.contract import RepoStatus

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

    from shikiru.clock import Clock
    from shikiru.config.settings import RepoConfig
    from shikiru.workspace.git import Git

ENGINE_LOCK_RELATIVE_PATH = f"{SHIKIRU_DIR_NAME}/engine.lock"
REPO_STATUS_CACHE_TTL_SECONDS = 10.0


def engine_is_running(project_dir: Path) -> bool:
    """Return whether another process holds an exclusive lock on `project_dir`'s engine lock."""
    lock_path = project_dir / ENGINE_LOCK_RELATIVE_PATH
    if not lock_path.exists():
        return False
    with lock_path.open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(handle, fcntl.LOCK_UN)
        return False


@dataclass(frozen=True)
class _CachedRepoStatus:
    fetched_at: datetime
    status: RepoStatus


class GitRepoStatusService:
    """Reports `header`'s repo row, refreshing git data at most once per 10 s (06 §5.4)."""

    def __init__(self, git: Git, repo: RepoConfig, project_dir: Path, clock: Clock) -> None:
        """Read `project_dir`'s checkout, configured by `repo`, through `git` when asked."""
        self._git = git
        self._repo = repo
        self._project_dir = project_dir
        self._clock = clock
        self._cache: _CachedRepoStatus | None = None

    async def repo_status(self) -> RepoStatus:
        """Return the checkout's branch, ahead/behind `base` and dirty count."""
        now = self._clock.now()
        if self._cache is not None:
            age = (now - self._cache.fetched_at).total_seconds()
            if age < REPO_STATUS_CACHE_TTL_SECONDS:
                return self._cache.status
        status = await self._status()
        self._cache = _CachedRepoStatus(fetched_at=now, status=status)
        return status

    async def _status(self) -> RepoStatus:
        repo = self._repo
        repo_dir = self._project_dir
        branch = await self._git.current_branch(repo_dir)
        dirty = len(await self._git.status_porcelain(repo_dir))
        ahead, behind = 0, 0
        if repo.base is not None and repo.base != branch:
            ahead = len(await self._git.log_oneline(repo_dir, f"{repo.base}..{branch}"))
            behind = len(await self._git.log_oneline(repo_dir, f"{branch}..{repo.base}"))
        return RepoStatus(branch=branch, ahead=ahead, behind=behind, dirty=dirty)


class DashboardServices:
    """The `WidgetServices` bootstrap wires into every `dash` process (06 §5.4)."""

    def __init__(self, repo_status_service: GitRepoStatusService, project_dir: Path) -> None:
        """Delegate `repo_status` to `repo_status_service`, `engine_up` to `project_dir`'s lock."""
        self._repo_status_service = repo_status_service
        self._project_dir = project_dir

    async def repo_status(self) -> RepoStatus:
        """See `GitRepoStatusService.repo_status`."""
        return await self._repo_status_service.repo_status()

    def engine_up(self) -> bool:
        """See `engine_is_running`."""
        return engine_is_running(self._project_dir)
