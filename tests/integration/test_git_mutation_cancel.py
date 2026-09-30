"""A cancelled mutating git call is stopped gracefully, so git removes its own lock."""

from __future__ import annotations

import asyncio
import os
import stat
from contextlib import suppress
from typing import TYPE_CHECKING

import pytest

from shikiru.process import SubprocessRunner
from shikiru.workspace.git import Git
from tests.support.git_repo import commit_all, init_repo
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.asyncio

_WAIT_SECONDS = 10.0
_HOOK = """#!/bin/sh
[ "$1" = prepared ] || exit 0
touch '{ready}'
cat '{release}'
"""


def _locks(repo: Path) -> list[Path]:
    return sorted((repo / ".git").rglob("*.lock"))


async def test_cancelled_merge_leaves_no_lock(tmp_path: Path) -> None:
    runner = SubprocessRunner()
    repo = tmp_path / "repo"
    await init_repo(runner, repo)
    (repo / "a.txt").write_text("a")
    await commit_all(runner, repo, "initial")
    await runner.run(["git", "branch", "feature"], timeout=_WAIT_SECONDS, cwd=repo, check=True)
    await runner.run(["git", "checkout", "feature"], timeout=_WAIT_SECONDS, cwd=repo, check=True)
    (repo / "a.txt").write_text("changed")
    await commit_all(runner, repo, "change")
    await runner.run(["git", "checkout", "main"], timeout=_WAIT_SECONDS, cwd=repo, check=True)
    ready, release = tmp_path / "ready", tmp_path / "release"
    os.mkfifo(release)
    hook = repo / ".git" / "hooks" / "reference-transaction"
    hook.write_text(_HOOK.format(ready=ready, release=release))
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR)
    merge = asyncio.create_task(Git(runner).merge_ff_only(repo, "feature"))
    try:
        await wait_until(ready.exists, _WAIT_SECONDS)
        assert _locks(repo)

        merge.cancel()
        with suppress(asyncio.CancelledError):
            await merge

        assert _locks(repo) == []
    finally:
        with release.open("w") as fifo:
            fifo.write("")
