"""Integration tests for `TaskRootBuilder` against real git repos (03 §5)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.config.settings import RepoConfig
from shikiru.process import SubprocessRunner
from shikiru.workspace.git import Git
from shikiru.workspace.roots import EXCLUDE_LINE, TaskRootBuilder, branch_name, slugify
from tests.support.git_repo import commit_all, init_repo

if TYPE_CHECKING:
    from pathlib import Path

_TASK_ID = "T-1"
_TASK_TITLE = "Add Retry to Parser!!"


def _builder(project_root: Path) -> TaskRootBuilder:
    return TaskRootBuilder(Git(SubprocessRunner()), project_root)


async def _init_repo_with_commit(project_root: Path, relative: str = ".") -> Path:
    repo_dir = project_root if relative == "." else project_root / relative
    runner = SubprocessRunner()
    await init_repo(runner, repo_dir)
    (repo_dir / "README.md").write_text("hello\n")
    await commit_all(runner, repo_dir, "initial commit")
    return repo_dir


def test_slugify_is_stable_and_filesystem_safe() -> None:
    assert slugify(_TASK_TITLE) == "add-retry-to-parser"
    assert slugify("  --Weird///Title--  ") == "weird-title"
    assert slugify("a" * 100) == "a" * 40


async def test_build_monorepo_worktree_is_the_task_root(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    await _init_repo_with_commit(project_root)
    builder = _builder(project_root)

    states = await builder.build(_TASK_ID, _TASK_TITLE, [RepoConfig(path=".")])

    root = builder.task_root(_TASK_ID)
    assert (root / "README.md").exists()
    assert len(states) == 1
    state = states[0]
    assert state.path == "."
    assert state.base == "main"
    assert state.branch == branch_name(_TASK_ID, _TASK_TITLE)
    runner = SubprocessRunner()
    expected_sha = await Git(runner).rev_parse(project_root, "main")
    assert state.base_sha == expected_sha


async def test_build_multirepo_symlinks_shared_top_level_entries(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    await _init_repo_with_commit(project_root, "backend")
    await _init_repo_with_commit(project_root, "frontend")
    (project_root / "CLAUDE.md").write_text("shared rules\n")
    tasks_dir = project_root / "tasks"
    tasks_dir.mkdir()
    (tasks_dir / "plan.md").write_text("plan\n")
    builder = _builder(project_root)

    states = await builder.build(
        _TASK_ID, _TASK_TITLE, [RepoConfig(path="backend"), RepoConfig(path="frontend")]
    )

    root = builder.task_root(_TASK_ID)
    assert (root / "backend" / "README.md").exists()
    assert not (root / "backend").is_symlink()
    assert (root / "frontend" / "README.md").exists()
    assert (root / "CLAUDE.md").is_symlink()
    assert (root / "CLAUDE.md").resolve() == (project_root / "CLAUDE.md").resolve()
    assert (root / "tasks").is_symlink()
    assert [repo.path for repo in states] == ["backend", "frontend"]


async def test_build_is_idempotent(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    await _init_repo_with_commit(project_root)
    builder = _builder(project_root)

    first = await builder.build(_TASK_ID, _TASK_TITLE, [RepoConfig(path=".")])
    second = await builder.build(_TASK_ID, _TASK_TITLE, [RepoConfig(path=".")])

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

    await builder.build(_TASK_ID, _TASK_TITLE, [RepoConfig(path=".")])
    await builder.build(_TASK_ID, _TASK_TITLE, [RepoConfig(path=".")])

    exclude_text = (project_root / ".git" / "info" / "exclude").read_text()
    assert exclude_text.count(EXCLUDE_LINE) == 1


async def test_cleanup_removes_worktree_and_deletes_only_the_merged_branch(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    merged_repo = await _init_repo_with_commit(project_root, "merged")
    unmerged_repo = await _init_repo_with_commit(project_root, "unmerged")
    builder = _builder(project_root)

    states = await builder.build(
        _TASK_ID, _TASK_TITLE, [RepoConfig(path="merged"), RepoConfig(path="unmerged")]
    )
    root = builder.task_root(_TASK_ID)
    runner = SubprocessRunner()
    (root / "unmerged" / "new.txt").write_text("change\n")
    await commit_all(runner, root / "unmerged", "extra commit")

    await builder.cleanup(_TASK_ID, states)

    assert not (root / "merged").exists()
    assert not (root / "unmerged").exists()
    merged_branches = await _local_branches(runner, merged_repo)
    unmerged_branches = await _local_branches(runner, unmerged_repo)
    assert branch_name(_TASK_ID, _TASK_TITLE) not in merged_branches
    assert branch_name(_TASK_ID, _TASK_TITLE) in unmerged_branches


async def test_cleanup_is_idempotent_and_tolerates_an_already_removed_worktree(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    await _init_repo_with_commit(project_root)
    builder = _builder(project_root)
    states = await builder.build(_TASK_ID, _TASK_TITLE, [RepoConfig(path=".")])

    await builder.cleanup(_TASK_ID, states)
    await builder.cleanup(_TASK_ID, states)


async def _local_branches(runner: SubprocessRunner, repo_dir: Path) -> list[str]:
    result = await runner.run(
        ["git", "branch", "--format=%(refname:short)"], timeout=10, cwd=repo_dir, check=True
    )
    return result.stdout.splitlines()
