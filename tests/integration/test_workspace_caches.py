"""Integration tests for `CacheCloner` copy-on-write cloning (03 §5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from shikiru.process import SubprocessRunner
from shikiru.workspace.caches import CACHE_COW_UNSUPPORTED_WARNING, CacheCloner, default_cow_args
from shikiru.workspace.git import Git
from tests.support.git_repo import init_repo


async def _cow_is_supported(worktree_dir: Path) -> bool:
    cow_args = default_cow_args()
    if cow_args is None:
        return False
    probe_cloner = CacheCloner(SubprocessRunner(), cow_args=cow_args)
    result = await probe_cloner.clone(worktree_dir, worktree_dir, [], [])
    return result.warning is None


async def _repo_with_ignored_cache(tmp_path: Path) -> Path:
    repo_dir = tmp_path / "repo"
    runner = SubprocessRunner()
    await init_repo(runner, repo_dir)
    (repo_dir / ".gitignore").write_text("node_modules/\n")
    cache_dir = repo_dir / "node_modules" / "pkg"
    cache_dir.mkdir(parents=True)
    (cache_dir / "file.txt").write_text("cached content\n")
    (repo_dir / "tracked.txt").write_text("tracked\n")
    return repo_dir


async def test_clone_copies_only_ignored_matching_paths(tmp_path: Path) -> None:
    repo_dir = await _repo_with_ignored_cache(tmp_path)
    worktree_dir = tmp_path / "worktree"
    worktree_dir.mkdir()
    if not await _cow_is_supported(worktree_dir):
        pytest.skip("this filesystem does not support copy-on-write copies")
    runner = SubprocessRunner()
    ignored = await Git(runner).ignored_paths(repo_dir)
    cloner = CacheCloner(runner, cow_args=default_cow_args())

    result = await cloner.clone(repo_dir, worktree_dir, ["node_modules", "not_a_cache"], ignored)

    assert result.warning is None
    assert result.copied == (Path("node_modules"),)
    copy = worktree_dir / "node_modules" / "pkg" / "file.txt"
    assert copy.read_text() == "cached content\n"
    assert not (worktree_dir / "tracked.txt").exists()


async def test_clone_copy_is_independent_of_the_source(tmp_path: Path) -> None:
    repo_dir = await _repo_with_ignored_cache(tmp_path)
    worktree_dir = tmp_path / "worktree"
    worktree_dir.mkdir()
    if not await _cow_is_supported(worktree_dir):
        pytest.skip("this filesystem does not support copy-on-write copies")
    runner = SubprocessRunner()
    ignored = await Git(runner).ignored_paths(repo_dir)
    cloner = CacheCloner(runner, cow_args=default_cow_args())
    await cloner.clone(repo_dir, worktree_dir, ["node_modules"], ignored)

    copy = worktree_dir / "node_modules" / "pkg" / "file.txt"
    copy.write_text("modified in the clone\n")

    original = repo_dir / "node_modules" / "pkg" / "file.txt"
    assert original.read_text() == "cached content\n"


async def test_clone_skips_a_glob_match_that_is_not_git_ignored(tmp_path: Path) -> None:
    repo_dir = await _repo_with_ignored_cache(tmp_path)
    tracked_cache_dir = repo_dir / "tracked_cache"
    tracked_cache_dir.mkdir()
    (tracked_cache_dir / "file.txt").write_text("tracked, not ignored\n")
    worktree_dir = tmp_path / "worktree"
    worktree_dir.mkdir()
    if not await _cow_is_supported(worktree_dir):
        pytest.skip("this filesystem does not support copy-on-write copies")
    runner = SubprocessRunner()
    ignored = await Git(runner).ignored_paths(repo_dir)
    cloner = CacheCloner(runner, cow_args=default_cow_args())

    result = await cloner.clone(repo_dir, worktree_dir, ["tracked_cache"], ignored)

    assert result.warning is None
    assert result.copied == ()
    assert not (worktree_dir / "tracked_cache").exists()


async def test_clone_reports_unsupported_cow_without_copying_anything(tmp_path: Path) -> None:
    repo_dir = await _repo_with_ignored_cache(tmp_path)
    worktree_dir = tmp_path / "worktree"
    worktree_dir.mkdir()
    runner = SubprocessRunner()
    ignored = await Git(runner).ignored_paths(repo_dir)
    cloner = CacheCloner(runner, cow_args=None)

    result = await cloner.clone(repo_dir, worktree_dir, ["node_modules"], ignored)

    assert result.copied == ()
    assert result.warning == CACHE_COW_UNSUPPORTED_WARNING
    assert not (worktree_dir / "node_modules").exists()
