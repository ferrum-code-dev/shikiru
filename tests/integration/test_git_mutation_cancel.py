"""A cancelled mutating git call removes its lock and leaves no hook running."""

from __future__ import annotations

import asyncio
import os
import shlex
import signal
import stat
from contextlib import suppress
from typing import TYPE_CHECKING, Any

import pytest

from shikiru.errors import CommandTimeoutError
from shikiru.process import SubprocessRunner
from shikiru.workspace.git import Git
from tests.support.git_repo import GIT_COMMAND_TIMEOUT, commit_all, init_repo
from tests.support.handshake import fifo_writer, release_fifo
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from shikiru.process import ProcessResult

pytestmark = pytest.mark.asyncio

_WAIT_SECONDS = 10.0
_HOOK = """#!/bin/sh
[ "$1" = prepared ] || exit 0
echo $$ > '{pid}'
cat {release} > /dev/null
"""


def _locks(repo: Path) -> list[Path]:
    return sorted((repo / ".git").rglob("*.lock"))


async def test_cancelled_merge_leaves_no_lock_and_no_hook(tmp_path: Path) -> None:
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
    release, pid_file = tmp_path / "release", tmp_path / "hook.pid"
    os.mkfifo(release)
    hook = repo / ".git" / "hooks" / "reference-transaction"
    hook.write_text(_HOOK.format(release=shlex.quote(str(release)), pid=pid_file))
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR)
    merge = asyncio.create_task(
        Git(runner, command_timeout=GIT_COMMAND_TIMEOUT).merge_ff_only(repo, "feature")
    )
    hook_pid: int | None = None
    writer: int | None = None
    try:
        writer = await fifo_writer(release, _WAIT_SECONDS)
        hook_pid = int(pid_file.read_text())
        assert _locks(repo)

        merge.cancel()
        with suppress(asyncio.CancelledError):
            await merge

        assert _locks(repo) == []
        await wait_until(lambda: runner.identify(hook_pid) is None, _WAIT_SECONDS)
    finally:
        if writer is not None:
            release_fifo(writer, release)
        if hook_pid is not None:
            with suppress(ProcessLookupError):
                os.kill(hook_pid, signal.SIGKILL)


_FSMONITOR = """#!/bin/sh
sleep 300 &
echo $! > '{pid}'
wait
"""
_TIMEOUT_ESCALATION_SECONDS = (1.0, 4.0, 16.0)


class _EscalatingTimeoutRunner(SubprocessRunner):
    def __init__(self) -> None:
        super().__init__()
        self.timeout_seconds = _TIMEOUT_ESCALATION_SECONDS[0]

    async def run(self, argv: Sequence[str], **kwargs: Any) -> ProcessResult:
        kwargs["timeout"] = self.timeout_seconds
        return await super().run(argv, **kwargs)


def _recorded_pid(pid_file: Path) -> int | None:
    recorded = pid_file.read_text().strip() if pid_file.exists() else ""
    return int(recorded) if recorded else None


async def test_a_timed_out_read_only_git_call_leaves_no_descendant(tmp_path: Path) -> None:
    setup = SubprocessRunner()
    repo = tmp_path / "repo"
    await init_repo(setup, repo)
    (repo / "a.txt").write_text("a")
    await commit_all(setup, repo, "initial")
    pid_file = tmp_path / "descendant.pid"
    monitor = tmp_path / "fsmonitor"
    monitor.write_text(_FSMONITOR.format(pid=pid_file))
    monitor.chmod(monitor.stat().st_mode | stat.S_IXUSR)
    await setup.run(
        ["git", "config", "core.fsmonitor", str(monitor)], cwd=repo, timeout=GIT_COMMAND_TIMEOUT
    )
    runner = _EscalatingTimeoutRunner()

    for seconds in _TIMEOUT_ESCALATION_SECONDS:
        runner.timeout_seconds = seconds
        with pytest.raises(CommandTimeoutError):
            await Git(runner, command_timeout=GIT_COMMAND_TIMEOUT).status_porcelain(repo)
        if _recorded_pid(pid_file) is not None:
            break

    descendant_pid = _recorded_pid(pid_file)
    assert descendant_pid is not None
    try:
        await wait_until(lambda: runner.identify(descendant_pid) is None, _WAIT_SECONDS)
    finally:
        with suppress(ProcessLookupError):
            os.kill(descendant_pid, signal.SIGKILL)
