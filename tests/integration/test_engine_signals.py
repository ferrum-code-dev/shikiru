"""A real engine process shuts down gracefully on SIGTERM and SIGINT (03 §7, 06 §5.8)."""

from __future__ import annotations

import asyncio
import errno
import json
import os
import signal
import sys
from contextlib import asynccontextmanager, suppress
from typing import TYPE_CHECKING

import pytest
from fake_agent.scripted import SCRIPT_ENV_VAR, role_script_env_var

from shikiru.clock import SystemClock
from shikiru.domain.events import EventKind
from shikiru.domain.tasks import Role, Status
from shikiru.engine.daemon import EngineLock
from shikiru.engine.turn_processes import read_turn_pid, turn_pid_path
from shikiru.paths import engine_lock_path, engine_log_path, run_dir, state_db_path
from shikiru.process import SubprocessRunner
from shikiru.store.database import Database
from shikiru.store.events import EventLog
from shikiru.store.tasks import TaskStore
from tests.support.cli_project import prepare_project, run_cli, tmux_environment
from tests.support.factories import CPU_UNCAPPED_PCT
from tests.support.waiting import WaitTimeoutError, wait_until

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable
    from pathlib import Path

    from shikiru.process import ProcessIdentity
    from tests.support.tmux import TmuxServer

pytestmark = pytest.mark.asyncio

TASK_ID = "T-1"
WAIT_SECONDS = 120.0
LOG_TAIL_CHARS = 2000
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
        path = turn_pid_path(run_dir(self.root, TASK_ID), Role.IMPLEMENTER)
        return read_turn_pid(path) if path.exists() else None

    def describe(self) -> str:
        log = engine_log_path(self.root)
        tail = log.read_text()[-LOG_TAIL_CHARS:] if log.exists() else ""
        kinds = [kind.value for kind in self.event_kinds()]
        return (
            f"engine pid {self.engine_pid()}, implementer turn {self.turn_identity()}, "
            f"task {self.task_status()}, events {kinds}\n{tail}"
        )

    async def eventually(self, predicate: Callable[[], bool | Awaitable[bool]]) -> None:
        try:
            await wait_until(predicate, timeout=WAIT_SECONDS)
        except WaitTimeoutError as error:
            message = f"{error}: {self.describe()}"
            raise AssertionError(message) from error

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

    await project.eventually(turn_running)
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
        await project.eventually(project.engine_down)

        assert not RUNNER.group_is_running(identity.pid)
        assert EventKind.ENGINE_STOPPED in project.event_kinds()
        assert project.task_status() is Status.RUNNING

        await project.cli("start")
        await project.eventually(lambda: project.turn_identity() not in {None, identity})
        await project.eventually(project.open_gate)
        await project.eventually(lambda: project.task_status() is Status.DONE)


async def test_stop_returns_only_after_the_engine_released_its_lock(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    async with engine_project(tmp_path, tmux_server) as project:
        engine_pid, _ = await _start_with_running_turn(project)

        await project.cli("stop")
        assert project.engine_pid() is None

        await project.cli("start")
        assert project.engine_pid() not in {None, engine_pid}


async def test_a_second_signal_ends_the_engine_by_force_and_a_restart_resumes(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    async with engine_project(tmp_path, tmux_server) as project:
        engine = await RUNNER.spawn(
            [sys.executable, "-m", "shikiru", "--project", str(project.root), *SETTINGS, "engine"],
            cwd=project.root,
            env=project.environment,
        )
        try:
            await project.eventually(lambda: project.engine_pid() == engine.pid)
            await project.cli("submit", "Implement task A1 from tasks/plan.md")
            await project.eventually(lambda: project.turn_identity() is not None)
            identity = project.turn_identity()
            assert identity is not None

            os.kill(engine.pid, signal.SIGSTOP)
            os.kill(engine.pid, signal.SIGTERM)
            os.kill(engine.pid, signal.SIGINT)
            os.kill(engine.pid, signal.SIGCONT)
            returncode = await asyncio.wait_for(engine.wait(), timeout=WAIT_SECONDS)
        finally:
            await engine.stop(grace=0)

        assert returncode != 0
        assert project.engine_pid() is None
        assert not RUNNER.group_is_running(identity.pid)
        assert project.task_status() is Status.RUNNING

        await project.cli("start")
        await project.eventually(lambda: project.turn_identity() not in {None, identity})
        await project.eventually(project.open_gate)
        await project.eventually(lambda: project.task_status() is Status.DONE)
