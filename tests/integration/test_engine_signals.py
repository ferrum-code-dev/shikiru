"""A real engine process shuts down gracefully on SIGTERM and SIGINT (03 §7, 06 §5.8)."""

from __future__ import annotations

import errno
import json
import os
import signal
from contextlib import asynccontextmanager, suppress
from typing import TYPE_CHECKING

import pytest
from fake_agent.scripted import SCRIPT_ENV_VAR, role_script_env_var

from shikiru.clock import SystemClock
from shikiru.domain.events import EventKind
from shikiru.domain.tasks import Role, Status
from shikiru.engine.daemon import EngineLock
from shikiru.engine.turn_processes import TURN_PID_NAME, read_turn_pid
from shikiru.paths import engine_lock_path, run_dir, state_db_path
from shikiru.process import SubprocessRunner
from shikiru.store.database import Database
from shikiru.store.events import EventLog
from shikiru.store.tasks import TaskStore
from tests.support.cli_project import prepare_project, run_cli, tmux_environment
from tests.support.factories import CPU_UNCAPPED_PCT
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path

    from shikiru.process import ProcessIdentity
    from tests.support.tmux import TmuxServer

pytestmark = pytest.mark.asyncio

TASK_ID = "T-1"
WAIT_SECONDS = 30.0
SETTINGS = (
    "--set",
    "orchestration.poll_seconds=1",
    "--set",
    f"orchestration.resources.max_cpu_pct={CPU_UNCAPPED_PCT}",
    "--set",
    "orchestration.resources.min_free_ram_gb=0",
    "--set",
    'dashboard.widgets=["header"]',
    "--set",
    'roles.leader.ladder=[{agent="fake",model="fake-1"}]',
    "--set",
    'roles.implementer.ladder=[{agent="fake",model="fake-1"}]',
    "--set",
    'roles.helper.ladder=[{agent="fake",model="fake-1"}]',
)
RUNNER = SubprocessRunner()


class EngineProject:
    def __init__(self, root: Path, environment: dict[str, str], gate: Path) -> None:
        self.root = root
        self.environment = environment
        self.gate = gate

    async def cli(self, *arguments: str) -> str:
        code, stdout, stderr = await run_cli(self.root, self.environment, *SETTINGS, *arguments)
        assert code == 0, stderr
        return stdout

    def engine_pid(self) -> int | None:
        return EngineLock.pid_if_running(engine_lock_path(self.root))

    def turn_identity(self) -> ProcessIdentity | None:
        paths = sorted(run_dir(self.root, TASK_ID).glob(f"*/{TURN_PID_NAME}"))
        return read_turn_pid(paths[0]) if paths else None

    def task_status(self) -> Status:
        connection = Database(state_db_path(self.root)).connect()
        try:
            task = TaskStore(connection, SystemClock()).get(TASK_ID)
            assert task is not None
            return task.status
        finally:
            connection.close()

    def event_kinds(self) -> list[EventKind]:
        connection = Database(state_db_path(self.root)).connect()
        try:
            return [event.kind for event in EventLog(connection, SystemClock()).since(0)]
        finally:
            connection.close()

    def open_gate(self) -> bool:
        try:
            descriptor = os.open(self.gate, os.O_WRONLY | os.O_NONBLOCK)
        except OSError as error:
            if error.errno == errno.ENXIO:
                return False
            raise
        with os.fdopen(descriptor, "w") as gate:
            gate.write("go")
        return True

    async def engine_down(self) -> bool:
        return self.engine_pid() is None


@asynccontextmanager
async def engine_project(tmp_path: Path, tmux_server: TmuxServer) -> AsyncGenerator[EngineProject]:
    prepared = await prepare_project(tmp_path)
    gate = tmp_path / "gate.fifo"
    os.mkfifo(gate)
    environment = tmux_environment(prepared.root, tmux_server)
    gated = {"kind": "accept", "release_fifo": str(gate)}
    environment[SCRIPT_ENV_VAR] = json.dumps([{"kind": "accept"}])
    environment[role_script_env_var(Role.IMPLEMENTER.value)] = json.dumps(
        [gated, gated, {"kind": "accept"}]
    )
    project = EngineProject(prepared.root, environment, gate)
    await project.cli("init", "--yes")
    try:
        yield project
    finally:
        await run_cli(prepared.root, environment, "stop", "--kill-session")
        pid = project.engine_pid()
        if pid is not None:
            with suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
        identity = project.turn_identity()
        if identity is not None:
            RUNNER.signal_group(identity, signal.SIGKILL)


async def _start_with_running_turn(project: EngineProject) -> tuple[int, ProcessIdentity]:
    await project.cli("start")
    await project.cli("submit", "Implement task A1 from tasks/plan.md")

    async def turn_running() -> bool:
        return project.turn_identity() is not None

    await wait_until(turn_running, timeout=WAIT_SECONDS)
    engine_pid = project.engine_pid()
    identity = project.turn_identity()
    assert engine_pid is not None
    assert identity is not None
    return engine_pid, identity


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
async def test_a_signal_ends_the_turn_releases_the_lock_and_a_restart_resumes(
    tmp_path: Path, tmux_server: TmuxServer, signum: signal.Signals
) -> None:
    async with engine_project(tmp_path, tmux_server) as project:
        engine_pid, identity = await _start_with_running_turn(project)

        os.kill(engine_pid, signum)
        await wait_until(project.engine_down, timeout=WAIT_SECONDS)

        assert not RUNNER.group_is_running(identity.pid)
        assert EventKind.ENGINE_STOPPED in project.event_kinds()
        assert project.task_status() is Status.RUNNING

        await project.cli("start")
        await wait_until(
            lambda: project.turn_identity() not in {None, identity}, timeout=WAIT_SECONDS
        )
        await wait_until(project.open_gate, timeout=WAIT_SECONDS)
        await wait_until(lambda: project.task_status() is Status.DONE, timeout=WAIT_SECONDS)


async def test_stop_returns_only_after_the_engine_released_its_lock(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    async with engine_project(tmp_path, tmux_server) as project:
        engine_pid, _ = await _start_with_running_turn(project)

        await project.cli("stop")
        assert project.engine_pid() is None

        await project.cli("start")
        assert project.engine_pid() not in {None, engine_pid}
