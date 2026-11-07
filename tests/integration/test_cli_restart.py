"""`shikiru restart` replaces a real engine and refuses to cut off a running task unless forced."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from shikiru.errors import EXIT_COMMAND_REJECTED
from tests.support.engine_project import RUNNER, engine_project, start_with_running_turn

if TYPE_CHECKING:
    from pathlib import Path

    from tests.support.tmux import TmuxServer

pytestmark = pytest.mark.asyncio

TASK_ID = "T-1"


async def test_restart_refuses_while_a_task_runs_and_leaves_the_engine_alone(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    async with engine_project(tmp_path, tmux_server) as project:
        engine_pid, identity = await start_with_running_turn(project)

        code, _, stderr = await project.run("restart")

        assert code == EXIT_COMMAND_REJECTED
        assert TASK_ID in stderr
        assert project.engine_pid() == engine_pid
        assert RUNNER.group_is_running(identity.pid)


async def test_restart_with_force_replaces_an_engine_with_a_running_task(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    async with engine_project(tmp_path, tmux_server) as project:
        engine_pid, _ = await start_with_running_turn(project)

        stdout = await project.cli("restart", "--force")

        assert "Engine restarted" in stdout
        assert project.engine_pid() not in {None, engine_pid}


async def test_restart_replaces_an_idle_engine_and_json_reports_the_stop(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    async with engine_project(tmp_path, tmux_server) as project:
        await project.cli("start")
        engine_pid = project.engine_pid()

        value = json.loads(await project.cli("--json", "restart"))

        assert value["restarted"] is True
        assert value["engine"] == "up"
        assert project.engine_pid() not in {None, engine_pid}


async def test_restart_starts_an_engine_when_none_runs(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    async with engine_project(tmp_path, tmux_server) as project:
        assert project.engine_pid() is None

        stdout = await project.cli("restart")

        assert "Engine started" in stdout
        assert project.engine_pid() is not None
