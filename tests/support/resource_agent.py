"""A real fake-agent process tree recorded for one task, and dashboard services over it."""

from __future__ import annotations

import os
import shlex
import sys
from typing import TYPE_CHECKING

import psutil
from fake_agent.turns import FAKE_COMPACT_AT_PCT_ENV_VAR

from shikiru.agents.contracts import (
    PROJECT_ENV_VAR,
    ROLE_ENV_VAR,
    RUN_DIR_ENV_VAR,
    SLOT_ENV_VAR,
    TASK_ENV_VAR,
    Rung,
)
from shikiru.dashboard.services import DashboardServices, GitRepoStatusService
from shikiru.domain.tasks import Role
from shikiru.engine.turn_processes import TurnProcesses, read_turn_pid, turn_pid_path
from shikiru.paths import run_dir
from shikiru.process import START_TIME_TOLERANCE_SECONDS, SubprocessRunner
from shikiru.store.runs import RunStore
from shikiru.telemetry.disk import DiskSampler
from shikiru.telemetry.docker import DockerSampler
from shikiru.tmux.client import Tmux
from shikiru.workspace.git import Git
from tests.support.git_repo import GIT_COMMAND_TIMEOUT
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.config.settings import Settings
    from shikiru.domain.tasks import Task
    from shikiru.process import ProcessIdentity, SpawnedProcess
    from shikiru.store.reader import StateReader
    from shikiru.telemetry.resources import ResourceSampler
    from tests.support.clock import FakeClock
    from tests.support.store import Connection
    from tests.support.tmux import TmuxServer

RESOURCE_AGENT_SESSION_ID = "resource-agent-session"
RESOURCE_AGENT_PROMPT = "wait for the next instruction"
RESOURCE_AGENT_MODEL = "resource-test-model"
RESOURCE_AGENT_MODULE = "fake_agent.turn_cli"
RESOURCE_AGENT_COMMAND = "turn"
RESOURCE_AGENT_SESSION_FLAG = "--session-id"
RESOURCE_AGENT_PROMPT_FLAG = "--prompt"
RESOURCE_AGENT_MODEL_FLAG = "--model"
RESOURCE_AGENT_SHELL = "/bin/sh"
RESOURCE_AGENT_SHELL_COMMAND_FLAG = "-c"
RESOURCE_AGENT_SLOT = "0"
RESOURCE_AGENT_COMPACT_AT_PCT = "80"
RESOURCE_AGENT_WAIT_SECONDS = 10.0
RESOURCE_AGENT_RUN_TARGET = "=resource-agent"
RESOURCE_PID_REUSE_SHIFT_SECONDS = START_TIME_TOLERANCE_SECONDS + 1.0
RESOURCE_BYTES_PER_GIB = 1024**3
RESOURCE_RSS_TOLERANCE_BYTES = 4 * 1024**2
RESOURCE_AGENT_RUNG = Rung(agent="fake", model=RESOURCE_AGENT_MODEL, effort=None)


async def start_resource_agent(
    connection: Connection,
    clock: FakeClock,
    project_dir: Path,
    task: Task,
) -> tuple[SubprocessRunner, SpawnedProcess, TurnProcesses, Path, ProcessIdentity]:
    RunStore(connection, clock).start(task.id, Role.IMPLEMENTER, RESOURCE_AGENT_RUNG)
    task_run_dir = run_dir(project_dir, task.id)
    runner = SubprocessRunner()
    turn_processes = TurnProcesses(runner=runner, clock=clock, project_root=project_dir)
    environment = dict(os.environ)
    environment.update(
        {
            RUN_DIR_ENV_VAR: str(task_run_dir),
            ROLE_ENV_VAR: Role.IMPLEMENTER.value,
            PROJECT_ENV_VAR: str(project_dir),
            TASK_ENV_VAR: task.id,
            SLOT_ENV_VAR: RESOURCE_AGENT_SLOT,
            FAKE_COMPACT_AT_PCT_ENV_VAR: RESOURCE_AGENT_COMPACT_AT_PCT,
        }
    )
    agent_argv = (
        sys.executable,
        "-m",
        RESOURCE_AGENT_MODULE,
        RESOURCE_AGENT_COMMAND,
        RESOURCE_AGENT_SESSION_FLAG,
        RESOURCE_AGENT_SESSION_ID,
        RESOURCE_AGENT_PROMPT_FLAG,
        RESOURCE_AGENT_PROMPT,
        RESOURCE_AGENT_MODEL_FLAG,
        RESOURCE_AGENT_MODEL,
    )
    shell_command = f"{shlex.join(agent_argv)} & wait"
    process = await runner.spawn(
        [RESOURCE_AGENT_SHELL, RESOURCE_AGENT_SHELL_COMMAND_FLAG, shell_command],
        cwd=project_dir,
        env=environment,
    )
    started = False
    try:
        process_identity = runner.identify(process.pid)
        assert process_identity is not None
        turn_processes.record(task_run_dir, Role.IMPLEMENTER, process.pid)
        recorded_identity = read_turn_pid(turn_pid_path(task_run_dir, Role.IMPLEMENTER))
        assert recorded_identity.matches(process_identity)
        await wait_until(
            lambda: bool(psutil.Process(process.pid).children(recursive=True)),
            timeout=RESOURCE_AGENT_WAIT_SECONDS,
        )
        started = True
        return runner, process, turn_processes, task_run_dir, recorded_identity
    finally:
        if not started:
            await process.stop()
            turn_processes.clear(task_run_dir, Role.IMPLEMENTER)


def resource_services(
    reader: StateReader,
    project_dir: Path,
    clock: FakeClock,
    *,
    settings: Settings,
    tmux_server: TmuxServer,
    sampler: ResourceSampler,
    runner: SubprocessRunner,
) -> DashboardServices:
    return DashboardServices(
        GitRepoStatusService(
            Git(runner, command_timeout=GIT_COMMAND_TIMEOUT),
            settings.repo,
            project_dir,
            clock,
            cache_ttl_seconds=settings.dashboard.repo_status_cache_seconds,
        ),
        clock=clock,
        resources_refresh_seconds=settings.orchestration.resources.sample_seconds,
        engine_up=lambda: False,
        engine_identity=lambda: None,
        project_root=project_dir,
        state=reader,
        tmux=Tmux(tmux_server.runner, socket=tmux_server.socket_name, env=tmux_server.env),
        session_target=RESOURCE_AGENT_RUN_TARGET,
        resources=sampler,
        disk=DiskSampler(
            clock, project_dir, refresh_seconds=settings.dashboard.disk_refresh_seconds
        ),
        docker=DockerSampler(
            runner,
            clock,
            refresh_seconds=settings.dashboard.docker_refresh_seconds,
            timeout_seconds=settings.dashboard.docker_timeout_seconds,
            project_roots=None,
        ),
    )
