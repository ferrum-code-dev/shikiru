from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from shikiru.config.settings import RepoConfig
from shikiru.dashboard.contract import DiagnosticSampleState
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
    assert status.root == str(repo_dir.resolve())
    assert status.added_lines == 0
    assert status.deleted_lines == 0
    assert status.untracked_paths == 1


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


async def test_repo_status_counts_staged_and_unstaged_lines_and_classifies_other_changes(
    tmp_path: Path,
) -> None:
    runner = SubprocessRunner()
    git = Git(runner, command_timeout=GIT_COMMAND_TIMEOUT)
    repo_dir = tmp_path / "repo"
    await init_repo(runner, repo_dir, initial_branch="main")
    (repo_dir / "tracked.txt").write_text("one\ntwo\nthree\n")
    (repo_dir / "renamed.txt").write_text("first\nsecond\n")
    (repo_dir / "deleted.txt").write_text("gone one\ngone two\n")
    (repo_dir / "staged-only.txt").write_text("before\n")
    (repo_dir / "unstaged-only.txt").write_text("before\n")
    (repo_dir / "tracked.bin").write_bytes(b"\x00before")
    await commit_all(runner, repo_dir, "baseline")

    (repo_dir / "tracked.txt").write_text("one\nstaged\nthree\n")
    await runner.run(["git", "add", "--", "tracked.txt"], cwd=repo_dir, timeout=10.0, check=True)
    (repo_dir / "tracked.txt").write_text("one\nunstaged\nthree\nfour\n")
    (repo_dir / "staged-only.txt").write_text("before\nstaged\n")
    await runner.run(
        ["git", "add", "--", "staged-only.txt"],
        cwd=repo_dir,
        timeout=GIT_COMMAND_TIMEOUT,
        check=True,
    )
    (repo_dir / "unstaged-only.txt").write_text("unstaged\n")
    (repo_dir / "tracked.bin").write_bytes(b"\x00after")
    (repo_dir / "new.txt").write_text("new one\nnew two\n")
    (repo_dir / "new.bin").write_bytes(b"\x00new binary")
    await runner.run(
        ["git", "mv", "--", "renamed.txt", "renamed-again.txt"],
        cwd=repo_dir,
        timeout=10.0,
        check=True,
    )
    (repo_dir / "deleted.txt").unlink()

    stats = await git.working_tree_line_changes(repo_dir)

    assert stats.added_lines == 4
    assert stats.deleted_lines == 4
    assert stats.binary_entries == 1
    assert stats.untracked_paths == 2


async def test_repo_status_retains_last_success_as_stale_and_reports_first_failure(
    tmp_path: Path,
) -> None:
    runner = SubprocessRunner()
    git = Git(runner, command_timeout=GIT_COMMAND_TIMEOUT)
    repo_dir = tmp_path / "repo"
    await init_repo(runner, repo_dir, initial_branch="main")
    (repo_dir / "a.txt").write_text("initial\n")
    await commit_all(runner, repo_dir, "initial")
    clock = FakeClock(START)
    service = GitRepoStatusService(git, RepoConfig(base=None), repo_dir, clock, cache_ttl_seconds=1)

    previous = await service.repo_status()
    (repo_dir / ".git").rename(repo_dir / "git-metadata-unavailable")
    clock.advance(2)

    stale = await service.repo_status()

    assert stale.state is DiagnosticSampleState.STALE
    assert stale.branch == previous.branch == "main"
    assert stale.dirty == previous.dirty == 0
    assert stale.sampled_ago == "0:00:02"
    assert stale.error

    unavailable_dir = tmp_path / "not-a-repository"
    unavailable_dir.mkdir()
    unavailable = await GitRepoStatusService(
        git, RepoConfig(base=None), unavailable_dir, clock, cache_ttl_seconds=1
    ).repo_status()

    assert unavailable.state is DiagnosticSampleState.FAILED
    assert unavailable.branch is None
    assert unavailable.dirty is None
    assert unavailable.root == str(unavailable_dir.resolve())
    assert unavailable.error
