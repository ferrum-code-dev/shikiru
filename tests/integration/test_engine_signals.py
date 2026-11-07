"""A real engine process shuts down gracefully on SIGTERM and SIGINT."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from typing import TYPE_CHECKING

import pytest

from shikiru.domain.events import EventKind
from shikiru.domain.tasks import Status
from tests.support.engine_project import (
    RUNNER,
    SETTINGS,
    WAIT_SECONDS,
    engine_project,
    start_with_running_turn,
)

if TYPE_CHECKING:
    from pathlib import Path

    from tests.support.tmux import TmuxServer

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
async def test_a_signal_ends_the_turn_releases_the_lock_and_a_restart_resumes(
    tmp_path: Path, tmux_server: TmuxServer, signum: signal.Signals
) -> None:
    async with engine_project(tmp_path, tmux_server) as project:
        engine_pid, identity = await start_with_running_turn(project)

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
        engine_pid, _ = await start_with_running_turn(project)

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
