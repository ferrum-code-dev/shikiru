"""`start` reports an engine that ended before it held its lock instead of waiting for the bound."""

from __future__ import annotations

import json
import stat
from typing import TYPE_CHECKING

import pytest

from shikiru.bootstrap import build_operator
from shikiru.clock import SystemClock
from shikiru.engine.daemon import engine_is_running
from shikiru.operator.lifecycle import EngineExitedError, ProjectLifecycle
from shikiru.paths import engine_lock_path, engine_output_path, state_db_path
from shikiru.private import PRIVATE_FILE_MODE
from shikiru.process import SubprocessRunner
from shikiru.tmux.client import Tmux
from shikiru.tmux.session import SessionView
from tests.support.cli_project import prepare_project, run_cli, tmux_environment

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.config.settings import Settings
    from shikiru.operator.services import OperatorServices
    from tests.support.tmux import TmuxServer

pytestmark = pytest.mark.asyncio

NOT_A_DATABASE = b"this is not a database"
NOT_A_DATABASE_ERROR = "file is not a database"


async def test_start_fails_with_the_engine_exit_when_the_engine_dies_at_start(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    prepared = await prepare_project(tmp_path)
    environment = tmux_environment(prepared.root, tmux_server)
    code, _, stderr = await run_cli(prepared.root, environment, "init", "--yes")
    assert code == 0, stderr
    state_db_path(prepared.root).write_bytes(NOT_A_DATABASE)
    clock = SystemClock()
    tmux = Tmux(tmux_server.runner, socket=tmux_server.socket_name, env=tmux_server.env)

    async def accept_plugins(_settings: Settings) -> None:
        return None

    def build_services(root: Path, settings: Settings) -> OperatorServices:
        return build_operator(root, settings, clock=clock)

    lifecycle = ProjectLifecycle(
        runner=SubprocessRunner(),
        tmux=tmux,
        session=SessionView(tmux),
        clock=clock,
        operator_factory=build_services,
        plugin_validator=accept_plugins,
        engine_up=lambda root: engine_is_running(engine_lock_path(root)),
    )

    with pytest.raises(EngineExitedError, match=NOT_A_DATABASE_ERROR):
        await lifecycle.start(prepared.root, (), attach=False)

    assert NOT_A_DATABASE_ERROR in engine_output_path(prepared.root).read_text()
    assert stat.S_IMODE(engine_output_path(prepared.root).stat().st_mode) == PRIVATE_FILE_MODE


async def test_status_and_doctor_point_at_the_output_of_a_stopped_engine(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    prepared = await prepare_project(tmp_path)
    environment = tmux_environment(prepared.root, tmux_server)
    await run_cli(prepared.root, environment, "init", "--yes")
    output = engine_output_path(prepared.root)
    output.parent.mkdir(exist_ok=True)
    output.write_text("Traceback: the engine died\n")

    _, status, _ = await run_cli(prepared.root, environment, "--json", "status")
    _, doctor, _ = await run_cli(prepared.root, environment, "--json", "doctor")

    assert json.loads(status)["engine_output"] == str(output)
    assert any(check["name"] == "engine-output" for check in json.loads(doctor)["checks"])
