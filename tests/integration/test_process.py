from __future__ import annotations

import asyncio
import os
import sys
from typing import TYPE_CHECKING

import pytest

from shikiru.errors import CommandFailed, CommandTimeoutError
from shikiru.process import SubprocessRunner
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.asyncio

_PID_FILE_WAIT_TIMEOUT = 5.0


async def test_run_returns_stdout_and_zero_returncode() -> None:
    runner = SubprocessRunner()

    result = await runner.run(["echo", "hello"], timeout=5)

    assert result.returncode == 0
    assert result.stdout.strip() == "hello"


async def test_run_passes_input_to_stdin() -> None:
    runner = SubprocessRunner()

    result = await runner.run(["cat"], timeout=5, input_text="from stdin")

    assert result.stdout == "from stdin"


async def test_run_executes_in_the_given_cwd(tmp_path: Path) -> None:
    runner = SubprocessRunner()

    result = await runner.run(["pwd"], timeout=5, cwd=tmp_path)

    assert result.stdout.strip() == str(tmp_path)


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


async def test_without_check_non_zero_exit_does_not_raise() -> None:
    runner = SubprocessRunner()

    result = await runner.run(["sh", "-c", "exit 7"], timeout=5)

    assert result.returncode == 7


async def test_timeout_raises_and_kills_the_process(tmp_path: Path) -> None:
    runner = SubprocessRunner()
    pid_file = tmp_path / "pid"

    with pytest.raises(CommandTimeoutError):
        await runner.run(
            ["sh", "-c", f"echo $$ > {pid_file}; sleep 30"],
            timeout=0.2,
        )

    await wait_until(pid_file.exists, _PID_FILE_WAIT_TIMEOUT)
    pid = int(pid_file.read_text().strip())
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
