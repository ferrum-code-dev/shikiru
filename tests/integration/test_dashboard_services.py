from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from shikiru.config.settings import RepoConfig
from shikiru.dashboard.services import GitRepoStatusService
from shikiru.process import SubprocessRunner
from shikiru.workspace.git import Git
from tests.support.clock import FakeClock
from tests.support.git_repo import GIT_COMMAND_TIMEOUT, commit_all, init_repo

if TYPE_CHECKING:
    from pathlib import Path

START = datetime(2026, 1, 1, tzinfo=UTC)


async def test_repo_status_reports_branch_ahead_behind_and_dirty_count(tmp_path: Path) -> None:
    runner = SubprocessRunner()
    git = Git(runner, command_timeout=GIT_COMMAND_TIMEOUT)
    repo_dir = tmp_path / "repo"
    await init_repo(runner, repo_dir, initial_branch="main")
    (repo_dir / "a.txt").write_text("1")
    await commit_all(runner, repo_dir, "initial")
    await runner.run(["git", "checkout", "-b", "feature"], cwd=repo_dir, timeout=10.0, check=True)
    (repo_dir / "b.txt").write_text("2")
    await commit_all(runner, repo_dir, "feature commit")
    (repo_dir / "c.txt").write_text("dirty")

    service = GitRepoStatusService(
        git, RepoConfig(base="main"), repo_dir, FakeClock(START), cache_ttl_seconds=10
    )
    status = await service.repo_status()

    assert status.branch == "feature"
    assert status.ahead == 1
    assert status.behind == 0
    assert status.dirty == 1


async def test_repo_status_is_cached_within_the_ttl(tmp_path: Path) -> None:
    runner = SubprocessRunner()
    git = Git(runner, command_timeout=GIT_COMMAND_TIMEOUT)
    repo_dir = tmp_path / "repo"
    await init_repo(runner, repo_dir, initial_branch="main")
    (repo_dir / "a.txt").write_text("1")
    await commit_all(runner, repo_dir, "initial")

    clock = FakeClock(START)
    cache_ttl_seconds = 3
    service = GitRepoStatusService(
        git,
        RepoConfig(base="main"),
        repo_dir,
        clock,
        cache_ttl_seconds=cache_ttl_seconds,
    )

    first = await service.repo_status()
    (repo_dir / "b.txt").write_text("2")
    clock.advance(2)
    cached = await service.repo_status()
    assert cached.dirty == first.dirty == 0
    assert first.sampled_ago == "0:00:00"
    assert cached.sampled_ago == "0:00:02"

    clock.advance(cache_ttl_seconds - 1)
    refreshed = await service.repo_status()
    assert refreshed.dirty == 1
