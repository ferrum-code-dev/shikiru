from __future__ import annotations

import asyncio
import json
import os
import signal
from dataclasses import asdict, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from shikiru.domain.tasks import Role
from shikiru.engine.turn_processes import TurnProcesses, turn_pid_path
from shikiru.paths import run_dir
from shikiru.process import START_TIME_TOLERANCE_SECONDS, SubprocessRunner
from tests.support.clock import FakeClock
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from tests.support.process_tree import ProcessTree

pytestmark = pytest.mark.asyncio

_WAIT_SECONDS = 10.0
_ADVANCE_SECONDS = 1.0
_STOP_GRACE_SECONDS = 0.5
_REUSE_SHIFT_SECONDS = START_TIME_TOLERANCE_SECONDS + 1.0
_TASK_ID = "T-1"
_START = datetime(2026, 1, 1, tzinfo=UTC)


async def _end_orphans(processes: TurnProcesses, clock: FakeClock) -> None:
    running = asyncio.create_task(processes.end_orphans())

    def finished() -> bool:
        if clock.has_pending_sleep():
            clock.advance(_ADVANCE_SECONDS)
        return running.done()

    await wait_until(finished, _WAIT_SECONDS)
    await running


async def test_orphan_cleanup_ends_the_leftover_group(
    tmp_path: Path, process_tree: ProcessTree
) -> None:
    runner = SubprocessRunner()
    clock = FakeClock(_START)
    processes = TurnProcesses(runner=runner, clock=clock, project_root=tmp_path)
    turn = await runner.spawn(process_tree.argv)
    process_tree.parent_pid = turn.pid
    descendant = runner.identify(await process_tree.descendant_started())
    assert descendant is not None
    processes.record(run_dir(tmp_path, _TASK_ID), Role.IMPLEMENTER, turn.pid)

    await _end_orphans(processes, clock)

    assert [line async for line in turn.stdout_lines()] == []
    await wait_until(lambda: not runner.is_running(descendant), _WAIT_SECONDS)
    assert not turn_pid_path(run_dir(tmp_path, _TASK_ID), Role.IMPLEMENTER).exists()


async def test_orphan_cleanup_never_signals_a_reused_pid(
    tmp_path: Path, process_tree: ProcessTree
) -> None:
    runner = SubprocessRunner()
    clock = FakeClock(_START)
    processes = TurnProcesses(runner=runner, clock=clock, project_root=tmp_path)
    unrelated = await runner.spawn(process_tree.argv)
    process_tree.parent_pid = unrelated.pid
    descendant = runner.identify(await process_tree.descendant_started())
    identity = runner.identify(unrelated.pid)
    assert descendant is not None
    assert identity is not None
    assert identity.started_at is not None
    path = turn_pid_path(run_dir(tmp_path, _TASK_ID), Role.IMPLEMENTER)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(asdict(replace(identity, started_at=identity.started_at - _REUSE_SHIFT_SECONDS)))
    )

    await _end_orphans(processes, clock)

    assert runner.is_running(identity)
    assert runner.is_running(descendant)
    assert not path.exists()
    await unrelated.stop(grace=_STOP_GRACE_SECONDS)
    assert [line async for line in unrelated.stdout_lines()] == []


async def test_orphan_cleanup_ends_a_group_whose_leader_died_before_it_was_recorded(
    tmp_path: Path, process_tree: ProcessTree
) -> None:
    runner = SubprocessRunner()
    clock = FakeClock(_START)
    processes = TurnProcesses(runner=runner, clock=clock, project_root=tmp_path)
    turn = await runner.spawn(process_tree.argv)
    process_tree.parent_pid = turn.pid
    descendant = runner.identify(await process_tree.descendant_started())
    assert descendant is not None
    os.kill(turn.pid, signal.SIGKILL)
    await wait_until(lambda: runner.identify(turn.pid) is None, _WAIT_SECONDS)
    processes.record(run_dir(tmp_path, _TASK_ID), Role.IMPLEMENTER, turn.pid)

    await _end_orphans(processes, clock)

    await wait_until(lambda: not runner.is_running(descendant), _WAIT_SECONDS)
    assert not turn_pid_path(run_dir(tmp_path, _TASK_ID), Role.IMPLEMENTER).exists()
    await turn.stop(grace=_STOP_GRACE_SECONDS)
