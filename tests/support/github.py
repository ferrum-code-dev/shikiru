"""A fake `gh` on PATH and a local bare remote, so PR tests never reach GitHub or a network."""

from __future__ import annotations

import errno
import json
import os
import shutil
import sys
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

import pytest

from shikiru.process import SubprocessRunner
from tests.support import fake_gh
from tests.support.git_repo import COMMIT_IDENTITY_ARGS, run_git
from tests.support.permissions import EXECUTABLE_MODE

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from shikiru.workspace.github import PullRequestState

_GH_ENV_TO_DROP = ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GH_HOST", "GH_REPO")


@dataclass(frozen=True)
class FakeGitHub:
    """The fake `gh`'s pull requests and the calls it received."""

    state: Path

    def pull_requests(self) -> list[dict[str, Any]]:
        pulls = self.state / fake_gh.PULLS_DIR
        count = len(list(pulls.glob("*.json")))
        return [json.loads((pulls / f"{n}.json").read_text()) for n in range(1, count + 1)]

    def hold(self, subcommand: str) -> None:
        """Make `gh pr <subcommand>` block (`create` after recording, `close` before acting)."""
        os.mkfifo(self.state / f"{fake_gh.HOLD_PREFIX}{subcommand}")

    def release(self, subcommand: str) -> None:
        """Remove the hold of `subcommand` and unblock any call still waiting on it."""
        hold = self.state / f"{fake_gh.HOLD_PREFIX}{subcommand}"
        if not hold.exists():
            return
        try:
            descriptor = os.open(hold, os.O_WRONLY | os.O_NONBLOCK)
        except OSError as error:
            if error.errno != errno.ENXIO:
                raise
        else:
            os.close(descriptor)
        hold.unlink()

    def fail_next(self, subcommand: str, calls: int) -> None:
        """Make the next `calls` calls of `gh pr <subcommand>` fail like a network blip."""
        (self.state / f"{fake_gh.FAIL_PREFIX}{subcommand}").write_text(str(calls))

    def merge(self, number: int, commit: str) -> None:
        """Record pull request `number` as merged by `commit`, as gh reports a merged PR."""
        path = self.state / fake_gh.PULLS_DIR / f"{number}.json"
        record = json.loads(path.read_text())
        fake_gh.write_record(path, {**record, "state": "MERGED", "mergeCommit": {"oid": commit}})

    def set_state(self, number: int, state: PullRequestState) -> None:
        path = self.state / fake_gh.PULLS_DIR / f"{number}.json"
        record = json.loads(path.read_text())
        fake_gh.write_record(path, {**record, "state": state.value})

    def set_fields(self, number: int, **fields: object) -> None:
        path = self.state / fake_gh.PULLS_DIR / f"{number}.json"
        fake_gh.write_record(path, {**json.loads(path.read_text()), **fields})

    def break_pull_requests(self) -> None:
        for path in (self.state / fake_gh.PULLS_DIR).glob("*.json"):
            path.write_text("{")

    def calls(self, subcommand: str) -> int:
        """Count the `gh pr <subcommand>` calls received, failed and blocked ones included."""
        log = self.state / fake_gh.CALLS_LOG
        lines = log.read_text().splitlines() if log.exists() else []
        prefix = [fake_gh.PR_COMMAND, subcommand]
        return sum(json.loads(line)[: len(prefix)] == prefix for line in lines)

    def views(self) -> int:
        return self.calls("view")


def install_fake_gh(root: Path) -> tuple[FakeGitHub, Path]:
    """Write a fake `gh` and its pull request state under `root`.

    Returns the view of that state and the directory that must come first on `PATH`.
    """
    bin_dir = root / "bin"
    state = root / "state"
    bin_dir.mkdir(parents=True)
    (state / fake_gh.PULLS_DIR).mkdir(parents=True)
    gh = bin_dir / "gh"
    gh.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{fake_gh.__file__}" "{state}" "$@"\n')
    gh.chmod(EXECUTABLE_MODE)
    return FakeGitHub(state), bin_dir


def neutral_gh_environment(root: Path) -> dict[str, str]:
    """Return the variables that keep `gh` and git away from the operator's own configuration."""
    return {
        "GH_CONFIG_DIR": str(root / "gh-config"),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
    }


def without_gh_credentials(environment: dict[str, str]) -> dict[str, str]:
    """Return `environment` without any GitHub credential or target."""
    return {key: value for key, value in environment.items() if key not in _GH_ENV_TO_DROP}


@pytest.fixture
def fake_github(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Iterator[FakeGitHub]:
    """Shadow any real `gh` on PATH, drop GitHub credentials and neutralise git config."""
    root = tmp_path_factory.mktemp("github")
    github, bin_dir = install_fake_gh(root)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    for name in _GH_ENV_TO_DROP:
        monkeypatch.delenv(name, raising=False)
    for name, value in neutral_gh_environment(root).items():
        monkeypatch.setenv(name, value)
    assert shutil.which("gh") == str(bin_dir / "gh")
    yield github
    github.release("create")
    github.release("close")


async def add_bare_remote(
    project_root: Path, remote_dir: Path, name: str, *, base: str = "main"
) -> None:
    """Create a bare repository at `remote_dir` with `base`, added to `project_root` as `name`."""
    runner = SubprocessRunner()
    await run_git(runner, None, "init", "--bare", str(remote_dir))
    await run_git(runner, project_root, "remote", "add", name, str(remote_dir))
    await run_git(runner, project_root, "push", name, base)


class MergeStyle(StrEnum):
    """How a hosting service lands a pull request on its base."""

    MERGE_COMMIT = "merge commit"
    SQUASH = "squash"
    FAST_FORWARD = "fast-forward"


async def merge_on_remote(remote_dir: Path, base: str, head: str, style: MergeStyle) -> str:
    """Land `head` on `base` in the bare repository at `remote_dir` and return the landed commit."""
    runner = SubprocessRunner()

    async def git(*args: str) -> str:
        return await run_git(runner, remote_dir, *COMMIT_IDENTITY_ARGS, *args)

    base_sha = await git("rev-parse", base)
    head_sha = await git("rev-parse", head)
    if style is MergeStyle.FAST_FORWARD:
        landed = head_sha
    else:
        tree = await git("merge-tree", "--write-tree", base_sha, head_sha)
        parents = ["-p", base_sha] + (["-p", head_sha] if style is MergeStyle.MERGE_COMMIT else [])
        landed = await git("commit-tree", tree, *parents, "-m", f"merge {head}")
    await git("update-ref", f"refs/heads/{base}", landed, base_sha)
    return landed
