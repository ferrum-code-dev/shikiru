"""Releasing from a clean main bumps the version, commits, tags and pushes; the rest is refused."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
from scripts import release

from shikiru.process import SubprocessRunner
from tests.support.git_repo import commit_all, init_repo, run_git

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence
    from pathlib import Path

PYPROJECT = '[project]\nname = "shikiru"\nversion = "0.1.9"\n'
LOCKFILE = '[[package]]\nname = "shikiru"\nversion = "0.1.9"\nsource = { editable = "." }\n'
README = "Install v0.1.9 from releases/download/v0.1.9/shikiru-0.1.9-py3-none-any.whl.\n"
RELEASED_PYPROJECT = PYPROJECT.replace("0.1.9", "0.1.10")
NEUTRAL_GIT_ENVIRONMENT = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
}
RELEASE_TAG = "v0.1.10"
CURRENT_TAG = "v0.1.9"
DESCRIBE_LATEST_TAG = ("git", "describe", "--tags", "--abbrev=0", "--match", "v*")
CHECKS_ON_A_CLEAN_MAIN = (
    DESCRIBE_LATEST_TAG,
    ("git", "branch", "--show-current"),
    ("git", "status", "--porcelain"),
    ("git", "fetch", "--quiet", "origin"),
    ("git", "rev-parse", "HEAD"),
    ("git", "rev-parse", "origin/main"),
    ("git", "tag", "--list", RELEASE_TAG),
)
MUTATIONS = (
    ("git", "add", "pyproject.toml", "uv.lock", "README.md"),
    ("git", "commit", "-m", "chore: release 0.1.10", "-m", release.CO_AUTHOR_TRAILER),
    ("git", "push", "origin", "main"),
    ("git", "tag", RELEASE_TAG),
    ("git", "push", "origin", RELEASE_TAG),
)
FAKE_HEAD = "0123456789abcdef0123456789abcdef01234567"
FAKE_ANSWERS: dict[tuple[str, ...], str] = {
    DESCRIBE_LATEST_TAG: f"{CURRENT_TAG}\n",
    ("git", "branch", "--show-current"): "main\n",
    ("git", "rev-parse", "HEAD"): f"{FAKE_HEAD}\n",
    ("git", "rev-parse", "origin/main"): f"{FAKE_HEAD}\n",
}


class FakeGit:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def run(self, argv: Sequence[str], *, cwd: Path) -> release.CommandResult:
        del cwd
        call = tuple(argv)
        self.calls.append(call)
        return release.CommandResult(0, FAKE_ANSWERS.get(call, ""))


async def make_project(root: Path) -> Path:
    """Create a checkout on main at version 0.1.9, pushed to a bare `origin` in `root`."""
    runner = SubprocessRunner()
    origin = root / "origin.git"
    checkout = root / "checkout"
    await run_git(runner, None, "-c", "init.defaultBranch=main", "init", "--bare", str(origin))
    await init_repo(runner, checkout)
    (checkout / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    (checkout / "uv.lock").write_text(LOCKFILE, encoding="utf-8")
    (checkout / "README.md").write_text(README, encoding="utf-8")
    await commit_all(runner, checkout, "feat: start the project")
    await run_git(runner, checkout, "tag", CURRENT_TAG)
    await run_git(runner, checkout, "remote", "add", "origin", str(origin))
    await run_git(runner, checkout, "push", "origin", "main")
    return checkout


async def leave_main(checkout: Path) -> None:
    await run_git(SubprocessRunner(), checkout, "checkout", "-b", "feature")


async def leave_untracked_file(checkout: Path) -> None:
    (checkout / "notes.txt").write_text("draft\n", encoding="utf-8")


async def leave_unpushed_commit(checkout: Path) -> None:
    await commit_all(SubprocessRunner(), checkout, "feat: not pushed", allow_empty=True)


async def leave_tag_on_another_commit(checkout: Path) -> None:
    runner = SubprocessRunner()
    await run_git(runner, checkout, "checkout", "-b", "side")
    await commit_all(runner, checkout, "feat: side work", allow_empty=True)
    await run_git(runner, checkout, "tag", RELEASE_TAG)
    await run_git(runner, checkout, "checkout", release.RELEASE_BRANCH)


def neutral_runner() -> release.SubprocessRunner:
    return release.SubprocessRunner(NEUTRAL_GIT_ENVIRONMENT)


@pytest.mark.parametrize(
    ("leave_unready", "reason"),
    [
        pytest.param(leave_main, "the branch is feature", id="not-on-main"),
        pytest.param(leave_untracked_file, "uncommitted or untracked", id="dirty-work-tree"),
        pytest.param(leave_unpushed_commit, "is not origin/main", id="head-ahead-of-origin"),
        pytest.param(
            leave_tag_on_another_commit, "the tag v0.1.10 already exists", id="tag-exists"
        ),
    ],
)
async def test_refuses_an_unready_checkout_and_changes_nothing(
    tmp_path: Path,
    leave_unready: Callable[[Path], Awaitable[None]],
    reason: str,
) -> None:
    checkout = await make_project(tmp_path)
    await leave_unready(checkout)
    head_before = await run_git(SubprocessRunner(), checkout, "rev-parse", "HEAD")
    pyproject_before = (checkout / "pyproject.toml").read_text(encoding="utf-8")
    messages: list[str] = []

    with pytest.raises(release.ReleaseError, match=reason):
        release.release(neutral_runner(), checkout, "patch", dry_run=False, report=messages.append)

    assert (checkout / "pyproject.toml").read_text(encoding="utf-8") == pyproject_before
    assert await run_git(SubprocessRunner(), checkout, "rev-parse", "HEAD") == head_before


async def test_dry_run_prints_every_change_and_command_and_changes_nothing(tmp_path: Path) -> None:
    checkout = await make_project(tmp_path)
    head_before = await run_git(SubprocessRunner(), checkout, "rev-parse", "HEAD")
    messages: list[str] = []

    release.release(neutral_runner(), checkout, "minor", dry_run=True, report=messages.append)

    assert "would rewrite pyproject.toml: version 0.1.9 -> 0.2.0" in messages
    assert "would rewrite uv.lock: version 0.1.9 -> 0.2.0" in messages
    assert "would rewrite README.md: version 0.1.9 -> 0.2.0" in messages
    assert "would run: git tag v0.2.0" in messages
    assert (checkout / "pyproject.toml").read_text(encoding="utf-8") == PYPROJECT
    assert await run_git(SubprocessRunner(), checkout, "rev-parse", "HEAD") == head_before
    assert await run_git(SubprocessRunner(), checkout, "tag", "--list") == CURRENT_TAG


async def test_release_commits_tags_and_pushes_main_and_the_tag(tmp_path: Path) -> None:
    checkout = await make_project(tmp_path)
    runner = SubprocessRunner()
    messages: list[str] = []

    release.release(neutral_runner(), checkout, "patch", dry_run=False, report=messages.append)

    assert (checkout / "pyproject.toml").read_text(encoding="utf-8") == RELEASED_PYPROJECT
    assert await run_git(runner, checkout, "log", "-1", "--format=%s") == "chore: release 0.1.10"
    assert release.CO_AUTHOR_TRAILER in await run_git(runner, checkout, "log", "-1", "--format=%B")
    head = await run_git(runner, checkout, "rev-parse", "HEAD")
    origin = tmp_path / "origin.git"
    assert await run_git(runner, origin, "rev-parse", "main") == head
    assert await run_git(runner, origin, "rev-parse", f"refs/tags/{RELEASE_TAG}") == head
    assert f"Release workflow runs: {release.workflow_runs_url()}" in messages


def test_release_runs_checks_then_the_commit_push_and_tag_sequence(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    (tmp_path / "uv.lock").write_text(LOCKFILE, encoding="utf-8")
    (tmp_path / "README.md").write_text(README, encoding="utf-8")
    git = FakeGit()

    release.release(git, tmp_path, "patch", dry_run=False, report=lambda _message: None)

    assert git.calls == [*CHECKS_ON_A_CLEAN_MAIN, *MUTATIONS]


def test_dry_run_with_a_fake_runner_only_runs_the_checks(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    (tmp_path / "uv.lock").write_text(LOCKFILE, encoding="utf-8")
    (tmp_path / "README.md").write_text(README, encoding="utf-8")
    git = FakeGit()

    release.release(git, tmp_path, "patch", dry_run=True, report=lambda _message: None)

    assert git.calls == list(CHECKS_ON_A_CLEAN_MAIN)
