"""`LocalBaseSync` against real git: the local base fast-forwards, or it is left as it was."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

import pytest

from shikiru.process import SubprocessRunner
from shikiru.workspace.base_refs import LocalBaseBlocked, LocalBaseSync
from shikiru.workspace.git import Git
from tests.support.git_repo import GIT_COMMAND_TIMEOUT, commit_all, init_repo, run_git
from tests.support.github import add_bare_remote

if TYPE_CHECKING:
    from pathlib import Path

_RUNNER = SubprocessRunner()
_BASE = "main"
_BASE_REF = f"refs/heads/{_BASE}"
_SIDE = "side"
_REMOTE = "origin"
_SHARED = "shared.txt"
_REMOTE_ONLY = "remote.txt"
_LOCAL_ONLY = "local.txt"
_UNRELATED = "notes.txt"
_UNRELATED_TEXT = "keep me\n"
_OVERLAPPING_TEXT = "my edit\n"
_WORKTREE = "base-worktree"
_REMOTE_TRACKING_REF = f"refs/remotes/{_REMOTE}/{_BASE}"
_Settings = tuple[tuple[str, str], ...]
_NO_SETTINGS: _Settings = ()
_AUTOSTASH: _Settings = (("merge.autostash", "true"),)
_SQUASH_OPTIONS: _Settings = ((f"branch.{_BASE}.mergeOptions", "--squash"),)
_OURS_STRATEGY: _Settings = ((f"branch.{_BASE}.mergeOptions", "-s ours"),)


class _Placement(StrEnum):
    CHECKED_OUT = "checked out in the project"
    IN_ANOTHER_WORKTREE = "checked out in another worktree"
    NOT_CHECKED_OUT = "not checked out anywhere"
    MISSING = "missing locally"


class _Obstacle(StrEnum):
    OVERLAPPING_UNCOMMITTED = "uncommitted work in a file the remote changed"
    LOCAL_COMMITS = "local commits the remote base does not have"


@dataclass(frozen=True)
class _Arranged:
    project: Path
    tip: str
    head_branch: str
    base_checkout: Path | None


def _sync(project: Path) -> LocalBaseSync:
    return LocalBaseSync(Git(_RUNNER, command_timeout=GIT_COMMAND_TIMEOUT), project)


async def _rev(repo_dir: Path, ref: str) -> str:
    return await run_git(_RUNNER, repo_dir, "rev-parse", ref)


async def _arrange(
    tmp_path: Path,
    placement: _Placement,
    obstacle: _Obstacle | None,
    settings: _Settings = _NO_SETTINGS,
) -> _Arranged:
    project = tmp_path / "project"
    remote = tmp_path / "origin.git"
    await init_repo(_RUNNER, project)
    (project / _SHARED).write_text("one\n")
    await commit_all(_RUNNER, project, "seed the base")
    await add_bare_remote(project, remote, _REMOTE)
    await _move_remote(tmp_path, remote)
    await run_git(_RUNNER, project, "fetch", _REMOTE, _BASE)
    tip = await _rev(remote, _BASE_REF)
    (project / _UNRELATED).write_text(_UNRELATED_TEXT)
    match obstacle:
        case _Obstacle.OVERLAPPING_UNCOMMITTED:
            (project / _SHARED).write_text(_OVERLAPPING_TEXT)
        case _Obstacle.LOCAL_COMMITS:
            (project / _LOCAL_ONLY).write_text("local\n")
            await commit_all(_RUNNER, project, "local work")
        case None:
            pass
    base_checkout = None
    head_branch = _SIDE
    match placement:
        case _Placement.CHECKED_OUT:
            head_branch = _BASE
            base_checkout = project
        case _Placement.IN_ANOTHER_WORKTREE:
            await run_git(_RUNNER, project, "checkout", "-b", _SIDE)
            base_checkout = tmp_path / _WORKTREE
            await run_git(_RUNNER, project, "worktree", "add", str(base_checkout), _BASE)
        case _Placement.NOT_CHECKED_OUT:
            await run_git(_RUNNER, project, "checkout", "-b", _SIDE)
        case _Placement.MISSING:
            await run_git(_RUNNER, project, "checkout", "-b", _SIDE)
            await run_git(_RUNNER, project, "branch", "-D", _BASE)
    for key, value in settings:
        await run_git(_RUNNER, project, "config", key, value)
    return _Arranged(project, tip, head_branch, base_checkout)


async def _move_remote(tmp_path: Path, remote: Path) -> None:
    other = tmp_path / "other"
    await run_git(_RUNNER, None, "clone", "--branch", _BASE, str(remote), str(other))
    (other / _SHARED).write_text("two\n")
    (other / _REMOTE_ONLY).write_text("remote\n")
    await commit_all(_RUNNER, other, "remote change")
    await run_git(_RUNNER, other, "push", _REMOTE, _BASE)


@pytest.mark.parametrize("placement", list(_Placement))
async def test_the_local_base_is_advanced_to_the_remote_tip(
    placement: _Placement, tmp_path: Path
) -> None:
    arranged = await _arrange(tmp_path, placement, None)

    await _sync(arranged.project).advance(_BASE, arranged.tip)

    assert await _rev(arranged.project, _BASE_REF) == arranged.tip
    if arranged.base_checkout is not None:
        assert await _rev(arranged.base_checkout, "HEAD") == arranged.tip
    assert await run_git(_RUNNER, arranged.project, "rev-parse", "--abbrev-ref", "HEAD") == (
        arranged.head_branch
    )
    assert (arranged.project / _UNRELATED).read_text() == _UNRELATED_TEXT


@pytest.mark.parametrize(
    ("placement", "obstacle", "settings"),
    [
        (_Placement.CHECKED_OUT, _Obstacle.OVERLAPPING_UNCOMMITTED, _NO_SETTINGS),
        (_Placement.CHECKED_OUT, _Obstacle.OVERLAPPING_UNCOMMITTED, _AUTOSTASH),
        (_Placement.CHECKED_OUT, _Obstacle.LOCAL_COMMITS, _NO_SETTINGS),
        (_Placement.NOT_CHECKED_OUT, _Obstacle.LOCAL_COMMITS, _NO_SETTINGS),
    ],
)
async def test_a_local_base_that_cannot_fast_forward_is_left_as_it_was(
    placement: _Placement, obstacle: _Obstacle, settings: _Settings, tmp_path: Path
) -> None:
    arranged = await _arrange(tmp_path, placement, obstacle, settings)
    base_before = await _rev(arranged.project, _BASE_REF)
    status_before = await run_git(_RUNNER, arranged.project, "status", "--porcelain")
    shared_before = (arranged.project / _SHARED).read_text()

    with pytest.raises(LocalBaseBlocked):
        await _sync(arranged.project).advance(_BASE, arranged.tip)

    assert await _rev(arranged.project, _BASE_REF) == base_before
    assert await run_git(_RUNNER, arranged.project, "status", "--porcelain") == status_before
    assert (arranged.project / _SHARED).read_text() == shared_before


@pytest.mark.parametrize(
    ("placement", "settings"),
    [
        (_Placement.CHECKED_OUT, _SQUASH_OPTIONS),
        (_Placement.CHECKED_OUT, _OURS_STRATEGY),
        (_Placement.IN_ANOTHER_WORKTREE, _SQUASH_OPTIONS),
    ],
)
async def test_inherited_merge_options_cannot_stop_the_base_from_advancing(
    placement: _Placement, settings: _Settings, tmp_path: Path
) -> None:
    arranged = await _arrange(tmp_path, placement, None, settings)

    await _sync(arranged.project).advance(_BASE, arranged.tip)

    assert await _rev(arranged.project, _BASE_REF) == arranged.tip
    assert await _rev(arranged.project, _REMOTE_TRACKING_REF) == arranged.tip
    assert arranged.base_checkout is not None
    assert await _rev(arranged.base_checkout, "HEAD") == arranged.tip
    assert await run_git(_RUNNER, arranged.base_checkout, "diff", "--name-only") == ""
    assert await run_git(_RUNNER, arranged.base_checkout, "diff", "--cached", "--name-only") == ""


async def test_advancing_to_the_tip_again_changes_nothing(tmp_path: Path) -> None:
    arranged = await _arrange(tmp_path, _Placement.CHECKED_OUT, None)
    sync = _sync(arranged.project)
    await sync.advance(_BASE, arranged.tip)

    await sync.advance(_BASE, arranged.tip)

    assert await _rev(arranged.project, _BASE_REF) == arranged.tip
