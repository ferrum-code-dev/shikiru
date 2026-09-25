"""Integration tests for `TaskRootBuilder` against real git repos (03 §5)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from shikiru.config.settings import RepoConfig
from shikiru.paths import EXCLUDE_LINE, task_root
from shikiru.process import SubprocessRunner
from shikiru.workspace.caches import CACHE_COW_UNSUPPORTED_WARNING, CacheCloner, default_cow_args
from shikiru.workspace.git import Git
from shikiru.workspace.roots import TaskRootBuilder, branch_name, slugify
from tests.support.git_repo import commit_all, init_repo
from tests.support.workspace import task_root_builder

if TYPE_CHECKING:
    from pathlib import Path

_TASK_ID = "T-1"
_TASK_TITLE = "Add Retry to Parser!!"


def _builder(project_root: Path, repo: RepoConfig | None = None) -> TaskRootBuilder:
    resolved_repo = repo if repo is not None else RepoConfig()
    return task_root_builder(SubprocessRunner(), project_root, resolved_repo)


async def _init_repo_with_commit(project_root: Path) -> None:
    runner = SubprocessRunner()
    await init_repo(runner, project_root)
    (project_root / "README.md").write_text("hello\n")
    await commit_all(runner, project_root, "initial commit")


def test_slugify_is_stable_and_filesystem_safe() -> None:
    assert slugify(_TASK_TITLE) == "add-retry-to-parser"
    assert slugify("  --Weird///Title--  ") == "weird-title"
    assert slugify("a" * 100) == "a" * 40


async def test_build_makes_the_worktree_the_task_root(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    await _init_repo_with_commit(project_root)
    builder = _builder(project_root)

    state = await builder.build(_TASK_ID, _TASK_TITLE)

    root = task_root(project_root, _TASK_ID)
    assert (root / "README.md").exists()
    assert state.base == "main"
    assert state.branch == branch_name(_TASK_ID, _TASK_TITLE)
    runner = SubprocessRunner()
    expected_sha = await Git(runner).rev_parse(project_root, "main")
    assert state.base_sha == expected_sha


async def test_task_root_includes_configured_ignored_cache(tmp_path: Path) -> None:
    runner = SubprocessRunner()
    project_root = tmp_path / "project"
    await init_repo(runner, project_root)
    (project_root / ".gitignore").write_text("node_modules/\n")
    (project_root / "README.md").write_text("tracked\n")
    await commit_all(runner, project_root, "initial commit")
    cache_file = project_root / "node_modules" / "package" / "cache.txt"
    cache_file.parent.mkdir(parents=True)
    cache_file.write_text("warm cache\n")
    probe_dir = tmp_path / "cow-probe"
    probe_dir.mkdir()
    probe = await CacheCloner(runner, cow_args=default_cow_args()).clone(
        probe_dir, probe_dir, (), ()
    )
    if probe.warning == CACHE_COW_UNSUPPORTED_WARNING:
        pytest.skip(probe.warning)

    await _builder(project_root, RepoConfig(caches=["node_modules"])).build(_TASK_ID, _TASK_TITLE)

    copied_cache = project_root / ".shikiru" / "worktrees" / _TASK_ID / "node_modules"
    assert (copied_cache / "package" / "cache.txt").read_text() == "warm cache\n"


async def test_build_is_idempotent(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    await _init_repo_with_commit(project_root)
    builder = _builder(project_root)

    first = await builder.build(_TASK_ID, _TASK_TITLE)
    second = await builder.build(_TASK_ID, _TASK_TITLE)

    assert first == second
    runner = SubprocessRunner()
    result = await runner.run(
        ["git", "worktree", "list", "--porcelain"],
        timeout=10,
        cwd=project_root,
        check=True,
    )
    assert result.stdout.count("worktree ") == 2


async def test_build_adds_shikiru_to_exclude_without_duplicating(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    await _init_repo_with_commit(project_root)
    builder = _builder(project_root)

    await builder.build(_TASK_ID, _TASK_TITLE)
    await builder.build(_TASK_ID, _TASK_TITLE)

    exclude_text = (project_root / ".git" / "info" / "exclude").read_text()
    assert exclude_text.count(EXCLUDE_LINE) == 1


@pytest.mark.parametrize(("extra_commit", "branch_kept"), [(False, False), (True, True)])
async def test_cleanup_removes_the_worktree_and_deletes_the_branch_only_when_merged(
    tmp_path: Path, *, extra_commit: bool, branch_kept: bool
) -> None:
    project_root = tmp_path / "project"
    await _init_repo_with_commit(project_root)
    builder = _builder(project_root)
    state = await builder.build(_TASK_ID, _TASK_TITLE)
    root = task_root(project_root, _TASK_ID)
    runner = SubprocessRunner()
    if extra_commit:
        (root / "new.txt").write_text("change\n")
        await commit_all(runner, root, "extra commit")

    await builder.cleanup(_TASK_ID, state)

    assert not root.exists()
    branches = await _local_branches(runner, project_root)
    assert (branch_name(_TASK_ID, _TASK_TITLE) in branches) is branch_kept


async def test_append_to_exclude_adds_exactly_one_line_when_the_file_already_ends_in_a_newline(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    await _init_repo_with_commit(project_root)
    git = Git(SubprocessRunner())
    exclude_path = project_root / ".git" / "info" / "exclude"
    exclude_path.write_text("*.log\n")

    await git.append_to_exclude(project_root, EXCLUDE_LINE)

    assert exclude_path.read_text() == f"*.log\n{EXCLUDE_LINE}\n"


async def test_append_to_exclude_adds_a_newline_first_when_the_file_lacks_a_trailing_one(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    await _init_repo_with_commit(project_root)
    git = Git(SubprocessRunner())
    exclude_path = project_root / ".git" / "info" / "exclude"
    exclude_path.write_text("*.log")

    await git.append_to_exclude(project_root, EXCLUDE_LINE)

    assert exclude_path.read_text() == f"*.log\n{EXCLUDE_LINE}\n"


async def test_build_recreates_the_worktree_when_the_branch_exists_but_it_was_removed(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    await _init_repo_with_commit(project_root)
    builder = _builder(project_root)
    await builder.build(_TASK_ID, _TASK_TITLE)
    root = task_root(project_root, _TASK_ID)
    runner = SubprocessRunner()
    await runner.run(
        ["git", "worktree", "remove", "--force", str(root)],
        timeout=10,
        cwd=project_root,
        check=True,
    )
    assert not root.exists()

    state = await builder.build(_TASK_ID, _TASK_TITLE)

    assert (root / "README.md").exists()
    assert state.branch == branch_name(_TASK_ID, _TASK_TITLE)


async def test_cleanup_is_idempotent_and_tolerates_an_already_removed_worktree(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    await _init_repo_with_commit(project_root)
    builder = _builder(project_root)
    state = await builder.build(_TASK_ID, _TASK_TITLE)

    await builder.cleanup(_TASK_ID, state)
    await builder.cleanup(_TASK_ID, state)


async def _local_branches(runner: SubprocessRunner, repo_dir: Path) -> list[str]:
    result = await runner.run(
        ["git", "branch", "--format=%(refname:short)"], timeout=10, cwd=repo_dir, check=True
    )
    return result.stdout.splitlines()
