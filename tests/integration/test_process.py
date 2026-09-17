from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from shikiru.errors import CommandFailed, CommandTimeoutError
from shikiru.process import SubprocessRunner

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.asyncio


async def test_run_returns_stdout_and_zero_returncode() -> None:
    runner = SubprocessRunner()

    result = await runner.run(["echo", "hello"], timeout=5)

    assert result.returncode == 0
    assert result.stdout.strip() == "hello"


async def test_run_passes_cwd_env_and_input() -> None:
    runner = SubprocessRunner()

    result = await runner.run(
        ["cat"],
        timeout=5,
        input_text="from stdin",
        env={"PATH": os.environ["PATH"]},
    )

    assert result.stdout == "from stdin"


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

    pid = int(pid_file.read_text().strip())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
