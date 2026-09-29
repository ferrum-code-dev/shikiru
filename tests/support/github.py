"""A fake `gh` on PATH and a local bare remote, so PR tests never reach GitHub or a network."""

from __future__ import annotations

import errno
import json
import os
import shutil
import sys
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

import pytest

from shikiru.process import SubprocessRunner
from tests.support import fake_gh

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from shikiru.workspace.github import PullRequestState

_GIT_TIMEOUT_SECONDS = 10.0
_IDENTITY_ARGS = ("-c", "user.name=Shikiru Test", "-c", "user.email=test@shikiru.example")
_GH_ENV_TO_DROP = ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GH_HOST", "GH_REPO")


@dataclass(frozen=True)
class FakeGitHub:
    """The fake `gh`'s pull requests and the calls it received."""

    state: Path

    def pull_requests(self) -> list[dict[str, str]]:
        pulls = self.state / fake_gh.PULLS_DIR
        count = len(list(pulls.glob("*.json")))
        return [json.loads((pulls / f"{n}.json").read_text()) for n in range(1, count + 1)]

    def hold_create(self) -> None:
        """Make `pr create` block after recording its pull request, until `release_create`."""
        os.mkfifo(self.state / fake_gh.HOLD_CREATE)

    def release_create(self) -> None:
        """Remove the create hold and unblock any `pr create` still waiting on it."""
        hold = self.state / fake_gh.HOLD_CREATE
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

    def merge(self, number: int, commit: str) -> None:
        """Record pull request `number` as merged by `commit`, as gh reports a merged PR."""
        path = self.state / fake_gh.PULLS_DIR / f"{number}.json"
        record = json.loads(path.read_text())
        path.write_text(json.dumps({**record, "state": "MERGED", "mergeCommit": {"oid": commit}}))

    def set_state(self, number: int, state: PullRequestState) -> None:
        path = self.state / fake_gh.PULLS_DIR / f"{number}.json"
        record = json.loads(path.read_text())
        path.write_text(json.dumps({**record, "state": state.value}))

    def views(self) -> int:
        log = self.state / fake_gh.CALLS_LOG
        lines = log.read_text().splitlines() if log.exists() else []
        return sum(json.loads(line)[:2] == ["pr", "view"] for line in lines)


@pytest.fixture
def fake_github(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Iterator[FakeGitHub]:
    """Shadow any real `gh` on PATH, drop GitHub credentials and neutralise git config."""
    root = tmp_path_factory.mktemp("github")
    bin_dir = root / "bin"
    state = root / "state"
    bin_dir.mkdir()
    (state / fake_gh.PULLS_DIR).mkdir(parents=True)
    gh = bin_dir / "gh"
    gh.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{fake_gh.__file__}" "{state}" "$@"\n')
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    for name in _GH_ENV_TO_DROP:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GH_CONFIG_DIR", str(root / "gh-config"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    assert shutil.which("gh") == str(gh)
    github = FakeGitHub(state)
    yield github
    github.release_create()


async def add_bare_remote(
    project_root: Path, remote_dir: Path, name: str, *, base: str = "main"
) -> None:
    """Create a bare repository at `remote_dir` with `base`, added to `project_root` as `name`."""
    runner = SubprocessRunner()
    await runner.run(
        ["git", "init", "--bare", str(remote_dir)], timeout=_GIT_TIMEOUT_SECONDS, check=True
    )
    await runner.run(
        ["git", "remote", "add", name, str(remote_dir)],
        timeout=_GIT_TIMEOUT_SECONDS,
        cwd=project_root,
        check=True,
    )
    await runner.run(
        ["git", "push", name, base],
        timeout=_GIT_TIMEOUT_SECONDS,
        cwd=project_root,
        check=True,
    )


class MergeStyle(StrEnum):
    """How a hosting service lands a pull request on its base."""

    MERGE_COMMIT = "merge commit"
    SQUASH = "squash"
    FAST_FORWARD = "fast-forward"


async def merge_on_remote(remote_dir: Path, base: str, head: str, style: MergeStyle) -> str:
    """Land `head` on `base` in the bare repository at `remote_dir` and return the landed commit."""
    runner = SubprocessRunner()

    async def git(*args: str) -> str:
        result = await runner.run(
            ["git", *_IDENTITY_ARGS, *args],
            timeout=_GIT_TIMEOUT_SECONDS,
            cwd=remote_dir,
            check=True,
        )
        return result.stdout.strip()

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
