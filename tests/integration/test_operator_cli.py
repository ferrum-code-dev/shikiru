from __future__ import annotations

import json
import os
import sys
from typing import TYPE_CHECKING

import pytest
import typer

from shikiru.cli.app import emit_ack
from shikiru.cli.context import CliState
from shikiru.operator.commands import Ack, AckStatus
from shikiru.process import SubprocessRunner

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.asyncio


async def _git_project(root: Path) -> None:
    runner = SubprocessRunner()
    await runner.run(["git", "init", "-q"], timeout=10, cwd=root, check=True)
    (root / "README.md").write_text("operator test\n")
    await runner.run(["git", "add", "README.md"], timeout=10, cwd=root, check=True)
    await runner.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "initial",
        ],
        timeout=10,
        cwd=root,
        check=True,
    )


async def _cli(root: Path, *args: str) -> tuple[int, str, str]:
    env = dict(os.environ, NO_COLOR="1")
    result = await SubprocessRunner().run(
        [sys.executable, "-m", "shikiru", "--project", str(root), *args],
        timeout=20,
        cwd=root,
        env=env,
    )
    return result.returncode, result.stdout, result.stderr


async def test_cli_human_init_submit_status_and_already_stopped(tmp_path: Path) -> None:
    await _git_project(tmp_path)

    code, stdout, stderr = await _cli(tmp_path, "init", "--yes")
    assert code == 0
    assert stderr == ""
    assert str(tmp_path / ".shikiru" / "config.toml") in stdout

    code, stdout, stderr = await _cli(tmp_path, "submit", "human task")
    assert code == 0
    assert stderr == ""
    assert stdout.strip() == "T-1"

    code, stdout, stderr = await _cli(tmp_path, "status")
    assert code == 0
    assert stderr == ""
    assert stdout.strip()

    code, stdout, stderr = await _cli(tmp_path, "stop")
    assert code == 0
    assert stderr == ""
    assert stdout.strip()


async def test_cli_json_init_submit_status_and_stop(tmp_path: Path) -> None:
    await _git_project(tmp_path)

    code, stdout, stderr = await _cli(tmp_path, "--json", "init", "--yes")
    assert code == 0
    assert stderr == ""
    init_value = json.loads(stdout)
    assert init_value["config"].endswith(".shikiru/config.toml")

    code, stdout, stderr = await _cli(tmp_path, "--json", "submit", "json task")
    assert code == 0
    assert stderr == ""
    submit_value = json.loads(stdout)
    assert submit_value["task_id"] == "T-1"
    assert submit_value["status"] == "queued"

    code, stdout, stderr = await _cli(tmp_path, "--json", "status")
    assert code == 0
    assert stderr == ""
    status_value = json.loads(stdout)
    assert status_value["engine_up"] is False
    assert status_value["slots"] == {"active": 0, "maximum": 2}

    code, stdout, stderr = await _cli(tmp_path, "--json", "stop")
    assert code == 0
    assert stderr == ""
    stop_value = json.loads(stdout)
    assert stop_value["message"]
    assert stop_value["result"] == {"stopped": False}


async def test_cli_json_rejected_ack_has_rejected_exit_code(
    capsys: pytest.CaptureFixture[str],
) -> None:
    state = CliState(project=None, json_output=True, overrides=())
    ack = Ack(id=7, status=AckStatus.REJECTED, result={"error": "invalid"}, message="invalid")

    with pytest.raises(typer.Exit) as error:
        emit_ack(state, ack)

    assert error.value.exit_code == 4
    assert json.loads(capsys.readouterr().out)["status"] == "rejected"
