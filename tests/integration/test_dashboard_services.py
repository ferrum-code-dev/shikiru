from __future__ import annotations

import fcntl
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from shikiru.config.settings import RepoConfig
from shikiru.dashboard.services import (
    ENGINE_LOCK_RELATIVE_PATH,
    GitRepoStatusService,
    engine_is_running,
)
from shikiru.process import SubprocessRunner
from shikiru.workspace.git import Git
from tests.support.clock import FakeClock
from tests.support.git_repo import commit_all, init_repo

if TYPE_CHECKING:
    from pathlib import Path

START = datetime(2026, 1, 1, tzinfo=UTC)


async def test_engine_is_not_running_without_a_lock_file(tmp_path: Path) -> None:
    assert engine_is_running(tmp_path) is False


async def test_engine_is_running_while_another_process_holds_the_lock(tmp_path: Path) -> None:
    lock_path = tmp_path / ENGINE_LOCK_RELATIVE_PATH
    lock_path.parent.mkdir(parents=True)
    lock_path.touch()

    holder = lock_path.open("a+")
    try:
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert engine_is_running(tmp_path) is True
    finally:
        fcntl.flock(holder, fcntl.LOCK_UN)
        holder.close()

    assert engine_is_running(tmp_path) is False


async def test_repo_status_reports_branch_ahead_behind_and_dirty_count(tmp_path: Path) -> None:
    runner = SubprocessRunner()
    git = Git(runner)
    repo_dir = tmp_path / "repo"
    await init_repo(runner, repo_dir, initial_branch="main")
    (repo_dir / "a.txt").write_text("1")
    await commit_all(runner, repo_dir, "initial")
    await runner.run(["git", "checkout", "-b", "feature"], cwd=repo_dir, timeout=10.0, check=True)
    (repo_dir / "b.txt").write_text("2")
    await commit_all(runner, repo_dir, "feature commit")
    (repo_dir / "c.txt").write_text("dirty")

    service = GitRepoStatusService(
        git, [RepoConfig(path="repo", base="main")], tmp_path, FakeClock(START)
    )
    statuses = await service.repo_status()

    assert len(statuses) == 1
    status = statuses[0]
    assert status.branch == "feature"
    assert status.ahead == 1
    assert status.behind == 0
    assert status.dirty == 1


async def test_repo_status_is_cached_within_the_ttl(tmp_path: Path) -> None:
    runner = SubprocessRunner()
    git = Git(runner)
    repo_dir = tmp_path / "repo"
    await init_repo(runner, repo_dir, initial_branch="main")
    (repo_dir / "a.txt").write_text("1")
    await commit_all(runner, repo_dir, "initial")

    clock = FakeClock(START)
    service = GitRepoStatusService(git, [RepoConfig(path="repo", base="main")], tmp_path, clock)

    first = await service.repo_status()
    (repo_dir / "b.txt").write_text("2")
    cached = await service.repo_status()
    assert cached[0].dirty == first[0].dirty == 0

    clock.advance(11.0)
    refreshed = await service.repo_status()
    assert refreshed[0].dirty == 1
