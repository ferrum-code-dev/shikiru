"""A hung remote never stops the engine from processing commands (03 §10)."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import TYPE_CHECKING

from shikiru.bootstrap import build_engine
from shikiru.config.settings import PULL_REQUEST_INTEGRATION
from shikiru.domain.commands import Prioritize, Submit
from shikiru.paths import state_db_path
from shikiru.process import SubprocessRunner
from shikiru.store.commands import CommandQueue, CommandStatus
from shikiru.store.database import Database
from shikiru.store.tasks import TaskStore
from shikiru.tmux.session import SessionView
from tests.support.clock import FakeClock
from tests.support.engine import (
    build_registry,
    build_tmux,
    engine_settings,
    wait_for_command,
)
from tests.support.factories import CPU_UNCAPPED_PCT, TASK_CREATED_AT
from tests.support.flow import init_project_repo
from tests.support.github import add_bare_remote
from tests.support.permissions import EXECUTABLE_MODE
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from tests.support.tmux import TmuxServer

_HUNG_UPLOAD_PACK = """#!/bin/sh
echo started >> '{calls}'
while [ ! -e '{release}' ]; do sleep 0.05; done
exec git upload-pack "$@"
"""
_WAIT_SECONDS = 10.0
_NEW_PRIORITY = 7


async def test_commands_are_processed_while_a_task_root_build_hangs_on_the_remote(
    tmp_path: Path, tmux_server: TmuxServer, tmp_path_factory: pytest.TempPathFactory
) -> None:
    await init_project_repo(tmp_path)
    runner = SubprocessRunner()
    remote_dir = tmp_path_factory.mktemp("remote") / "origin.git"
    settings = engine_settings(max_cpu_pct=CPU_UNCAPPED_PCT)
    orchestration = settings.orchestration.model_copy(
        update={"integration": PULL_REQUEST_INTEGRATION}
    )
    settings = settings.model_copy(update={"orchestration": orchestration})
    await add_bare_remote(tmp_path, remote_dir, settings.repo.remote)
    hang_dir = tmp_path_factory.mktemp("hang")
    calls, release, hang = hang_dir / "calls", hang_dir / "go", hang_dir / "up"
    hang.write_text(_HUNG_UPLOAD_PACK.format(calls=calls, release=release))
    hang.chmod(EXECUTABLE_MODE)
    uploadpack = ["git", "config", f"remote.{settings.repo.remote}.uploadpack", str(hang)]
    await runner.run(uploadpack, timeout=_WAIT_SECONDS, cwd=tmp_path, check=True)
    clock = FakeClock(TASK_CREATED_AT)
    tmux = build_tmux(tmux_server)
    await SessionView(tmux).ensure("test", tmp_path, home_command=("/bin/sh",))
    engine = build_engine(tmp_path, settings, clock=clock, tmux=tmux, registry=build_registry())
    connection = Database(state_db_path(tmp_path)).connect()
    queue = CommandQueue(connection, clock)
    running = asyncio.create_task(engine.run())
    try:
        submitted = await wait_for_command(
            queue.append(Submit(title="hung", request="hung")), queue, clock
        )
        assert submitted.result is not None
        task_id = str(submitted.result["task_id"])
        await wait_until(calls.exists, _WAIT_SECONDS)

        outcome = await wait_for_command(
            queue.append(Prioritize(task_id=task_id, priority=_NEW_PRIORITY)), queue, clock
        )

        assert outcome.status is CommandStatus.DONE
        assert not release.exists()
        task = TaskStore(connection, clock).get(task_id)
        assert task is not None
        assert task.priority == _NEW_PRIORITY
    finally:
        release.touch()
        running.cancel()
        with suppress(asyncio.CancelledError):
            await running
        connection.close()
