from __future__ import annotations

import asyncio
import os
import signal
import sys
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from shikiru.errors import CommandFailed, CommandTimeoutError
from shikiru.process import STDERR_KEPT_BYTES, STDOUT_DRAIN_TIMEOUT, SubprocessRunner
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from shikiru.process import ProcessIdentity
    from tests.support.process_tree import ProcessTree

pytestmark = pytest.mark.asyncio

_PID_FILE_WAIT_TIMEOUT = 30.0
_KILL_TIMEOUT_SECONDS = 5.0
_STOP_BOUND_SECONDS = 5.0


async def test_run_feeds_stdin_runs_in_the_given_cwd_and_returns_the_exit_status_without_raising(
    tmp_path: Path,
) -> None:
    runner = SubprocessRunner()

    result = await runner.run(
        ["sh", "-c", "cat; echo; pwd; exit 7"], timeout=5, input_text="from stdin", cwd=tmp_path
    )

    assert result.returncode == 7
    assert result.stdout.split() == ["from", "stdin", str(tmp_path)]


async def test_run_passes_the_given_env_and_nothing_else() -> None:
    runner = SubprocessRunner()

    result = await runner.run(
        ["sh", "-c", "echo $SHIKIRU_TEST_VAR"],
        timeout=5,
        env={"SHIKIRU_TEST_VAR": "hello", "PATH": os.environ["PATH"]},
    )

    assert result.stdout.strip() == "hello"


async def test_check_raises_command_failed_on_non_zero_exit() -> None:
    runner = SubprocessRunner()

    with pytest.raises(CommandFailed) as excinfo:
        await runner.run(["sh", "-c", "echo oops >&2; exit 3"], timeout=5, check=True)

    assert excinfo.value.returncode == 3
    assert "oops" in excinfo.value.stderr


async def test_timeout_raises_and_kills_the_process(tmp_path: Path) -> None:
    runner = SubprocessRunner()
    pid_file = tmp_path / "pid"
    task = asyncio.create_task(
        runner.run(
            ["sh", "-c", f"echo $$ > {pid_file}; sleep 30"],
            timeout=_KILL_TIMEOUT_SECONDS,
        )
    )

    await wait_until(pid_file.exists, _PID_FILE_WAIT_TIMEOUT)
    pid = int(pid_file.read_text().strip())
    with pytest.raises(CommandTimeoutError):
        await task
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


async def test_cancellation_kills_and_reaps_the_process(tmp_path: Path) -> None:
    runner = SubprocessRunner()
    pid_file = tmp_path / "pid"
    program = (
        "from pathlib import Path; import os; import time; "
        f"Path({str(pid_file)!r}).write_text(str(os.getpid())); time.sleep(30)"
    )
    task = asyncio.create_task(
        runner.run(
            [sys.executable, "-c", program],
            timeout=30,
        )
    )

    await wait_until(pid_file.exists, _PID_FILE_WAIT_TIMEOUT)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    pid = int(pid_file.read_text().strip())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


async def test_cancelled_while_the_process_is_still_starting_leaves_no_descendant(
    tmp_path: Path,
) -> None:
    runner = SubprocessRunner()
    loop = asyncio.get_running_loop()
    pid_fifo = tmp_path / "descendant.fifo"
    os.mkfifo(pid_fifo)
    task = asyncio.create_task(
        runner.run(["sh", "-c", f"sleep 1000 & echo $! > {pid_fifo}; wait"], timeout=30)
    )
    started: list[ProcessIdentity | None] = []

    def cancel_once_the_descendant_ran() -> None:
        started.append(runner.identify(int(pid_fifo.read_text())))
        task.cancel()

    loop.call_soon(loop.call_soon, cancel_once_the_descendant_ran)

    with pytest.raises(asyncio.CancelledError):
        await task

    (descendant,) = started
    assert descendant is not None
    await wait_until(lambda: not runner.is_running(descendant), _KILL_TIMEOUT_SECONDS)


async def test_spawn_closes_stdin_streams_long_stdout_lines_and_captures_stderr() -> None:
    runner = SubprocessRunner()
    long_line = b"x" * 200_000 + b"\n"
    program = (
        "import sys; "
        "value = sys.stdin.read(); "
        "print('eof' if value == '' else 'input', flush=True); "
        "sys.stdout.buffer.write(b'x' * 200000 + b'\\n'); sys.stdout.flush(); "
        "sys.stderr.write('child warning\\n')"
    )

    process = await runner.spawn([sys.executable, "-c", program])
    lines = [line async for line in process.stdout_lines()]
    returncode = await process.wait()

    assert returncode == 0
    assert lines == [b"eof\n", long_line]
    assert process.stderr_bytes == b"child warning\n"


async def test_spawn_stop_sends_sigterm_then_sigkill_and_reaps_child() -> None:
    runner = SubprocessRunner()
    program = (
        "import signal, sys, threading; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "print('ready', flush=True); threading.Event().wait()"
    )

    process = await runner.spawn([sys.executable, "-c", program])
    output = process.stdout_lines()
    assert await anext(output) == b"ready\n"
    pid = process.pid
    await process.stop(grace=0.01)

    assert process.returncode == -signal.SIGKILL
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


async def test_stop_returns_within_a_bound_and_ends_a_descendant_holding_the_pipes(
    process_tree: ProcessTree,
) -> None:
    runner = SubprocessRunner()
    process = await runner.spawn(process_tree.argv)
    process_tree.parent_pid = process.pid
    descendant = runner.identify(await process_tree.descendant_started())
    assert descendant is not None

    await asyncio.wait_for(process.stop(grace=0.5), timeout=_STOP_BOUND_SECONDS)

    assert [line async for line in process.stdout_lines()] == []
    await wait_until(lambda: not runner.is_running(descendant), _STOP_BOUND_SECONDS)


_LEADER_EXITS_PROGRAM = 'sleep 300 & echo "$!" > "$1"; echo done; exit 0'


async def test_an_exited_leader_ends_the_output_stream_and_its_descendant_within_the_drain_bound(
    tmp_path: Path,
) -> None:
    runner = SubprocessRunner()
    pid_file = tmp_path / "descendant.pid"
    process = await runner.spawn(["/bin/sh", "-c", _LEADER_EXITS_PROGRAM, "sh", str(pid_file)])

    async with asyncio.timeout(STDOUT_DRAIN_TIMEOUT + _STOP_BOUND_SECONDS):
        lines = [line async for line in process.stdout_lines()]
        returncode = await process.wait()

    assert (lines, returncode) == ([b"done\n"], 0)
    descendant = runner.identify(int(pid_file.read_text()))
    assert descendant is None or not runner.is_running(descendant)


_MARKER_PROGRAM = "import pathlib, sys, time; pathlib.Path(sys.argv[1]).touch(); time.sleep(300)"
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


class _RecordingError(Exception):
    pass


async def test_spawned_process_runs_only_after_its_start_was_recorded(tmp_path: Path) -> None:
    runner = SubprocessRunner()
    marker = tmp_path / "ran"
    ran_when_recorded: list[bool] = []

    process = await runner.spawn(
        [sys.executable, "-c", _MARKER_PROGRAM, str(marker)],
        on_started=lambda _pid: ran_when_recorded.append(marker.exists()),
    )
    try:
        await wait_until(marker.exists, _PID_FILE_WAIT_TIMEOUT)
    finally:
        await process.stop(grace=0.5)

    assert ran_when_recorded == [False]


async def test_a_spawned_process_whose_start_could_not_be_recorded_never_runs(
    tmp_path: Path,
) -> None:
    runner = SubprocessRunner()
    marker = tmp_path / "ran"
    spawned: list[int] = []

    def fail_to_record(pid: int) -> None:
        spawned.append(pid)
        raise _RecordingError

    with pytest.raises(_RecordingError):
        await runner.spawn(
            [sys.executable, "-c", _MARKER_PROGRAM, str(marker)], on_started=fail_to_record
        )

    await wait_until(lambda: runner.identify(spawned[0]) is None, _PID_FILE_WAIT_TIMEOUT)
    assert not marker.exists()


async def test_a_process_spawned_by_an_engine_killed_before_recording_it_never_runs(
    tmp_path: Path,
) -> None:
    runner = SubprocessRunner()
    marker = tmp_path / "ran"

    engine = await runner.run(
        [sys.executable, "-m", "tests.support.spawn_then_die", str(marker)],
        timeout=_PID_FILE_WAIT_TIMEOUT,
        cwd=_PROJECT_ROOT,
    )

    orphan = int(engine.stdout)
    try:
        assert engine.returncode == -signal.SIGKILL
        await wait_until(lambda: runner.identify(orphan) is None, _PID_FILE_WAIT_TIMEOUT)
        assert not marker.exists()
    finally:
        with suppress(ProcessLookupError):
            os.kill(orphan, signal.SIGKILL)


_STDERR_FLOOD_LINES = 3000


async def test_spawn_keeps_only_a_bounded_tail_of_a_flood_of_stderr() -> None:
    runner = SubprocessRunner()
    program = (
        "import sys; "
        f"[sys.stderr.write('x' * 1023 + '\\n') for _ in range({_STDERR_FLOOD_LINES})]; "
        "sys.stderr.write('last line\\n')"
    )

    process = await runner.spawn([sys.executable, "-c", program])
    [_ async for _ in process.stdout_lines()]
    await process.wait()

    assert len(process.stderr_bytes) <= STDERR_KEPT_BYTES
    assert process.stderr_bytes.endswith(b"x\nlast line\n")
    assert process.stderr.endswith("last line\n")
