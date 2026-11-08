"""A real engine project in a private tmux server, driven through the CLI, for engine tests."""

from __future__ import annotations

import errno
import json
import os
import signal
from contextlib import asynccontextmanager, suppress
from typing import TYPE_CHECKING

from fake_agent.scripted import SCRIPT_ENV_VAR, role_script_env_var

from shikiru.agents.contracts import shikiru_argv
from shikiru.domain.tasks import Role, Status
from shikiru.engine.daemon import EngineLock
from shikiru.engine.turn_processes import read_turn_pid, turn_pid_path
from shikiru.paths import engine_lock_path, engine_log_path, run_dir, state_db_path
from shikiru.process import SubprocessRunner
from shikiru.store.database import Database
from shikiru.store.events import EventLog
from shikiru.store.tasks import TaskStore
from tests.support.cli_project import prepare_project, run_cli, tmux_environment
from tests.support.clock import LONG_AGO, FakeClock
from tests.support.factories import CPU_UNCAPPED_PCT
from tests.support.waiting import WaitTimeoutError, wait_until

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable
    from pathlib import Path

    from shikiru.domain.events import EventKind
    from shikiru.process import ProcessIdentity, SpawnedProcess
    from tests.support.tmux import TmuxServer

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
        self.engines: list[SpawnedProcess] = []

    async def cli(self, *arguments: str) -> str:
        code, stdout, stderr = await self.run(*arguments)
        assert code == 0, stderr
        return stdout

    async def run(self, *arguments: str) -> tuple[int, str, str]:
        return await run_cli(self.root, self.environment, *SETTINGS, *arguments)

    async def run_engine(self) -> SpawnedProcess:
        engine = await RUNNER.spawn(
            shikiru_argv("--project", str(self.root), *SETTINGS, "engine"),
            cwd=self.root,
            env=self.environment,
        )
        self.engines.append(engine)
        await self.eventually(lambda: self.engine_pid() == engine.pid)
        return engine

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
            task = TaskStore(connection, FakeClock(LONG_AGO)).get(TASK_ID)
            assert task is not None
            return task.status
        finally:
            connection.close()

    def event_kinds(self) -> list[EventKind]:
        connection = Database(state_db_path(self.root)).connect()
        try:
            return [event.kind for event in EventLog(connection, FakeClock(LONG_AGO)).since(0)]
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
        for engine in project.engines:
            await engine.stop(grace=0)


async def start_with_running_turn(project: EngineProject) -> tuple[int, ProcessIdentity]:
    engine = await project.run_engine()
    await project.cli("submit", "Implement task A1 from tasks/plan.md")

    async def turn_running() -> bool:
        return project.turn_identity() is not None

    await project.eventually(turn_running)
    identity = project.turn_identity()
    assert identity is not None
    return engine.pid, identity
