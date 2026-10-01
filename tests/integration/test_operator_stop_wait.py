"""`shikiru stop` acknowledges only once the engine has released its lock (06 §5.8)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from shikiru.bootstrap import build_operator
from shikiru.config.loader import load_settings
from shikiru.engine.daemon import EngineLock, engine_is_running
from shikiru.errors import ShikiruError
from shikiru.operator.commands import AckStatus
from shikiru.operator.lifecycle import ProjectLifecycle
from shikiru.paths import engine_lock_path, project_state_dir, state_db_path
from shikiru.process import SubprocessRunner
from shikiru.store.commands import CommandQueue
from shikiru.store.database import Database
from shikiru.tmux.client import Tmux
from shikiru.tmux.session import SessionView
from tests.support.clock import FakeClock
from tests.support.factories import TASK_CREATED_AT
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.config.settings import Settings

pytestmark = pytest.mark.asyncio

ADVANCE_SECONDS = 0.25
OVERRIDES = [
    'project.name="demo"',
    "agents.claude={}",
    'roles.leader.ladder=[{agent="claude",model="opus"}]',
    'roles.implementer.ladder=[{agent="claude",model="opus"}]',
    'roles.helper.ladder=[{agent="claude",model="haiku"}]',
]


class SlowEngine:
    def __init__(self, root: Path, clock: FakeClock) -> None:
        self._connection = Database(state_db_path(root)).connect()
        self._queue = CommandQueue(self._connection, clock)
        self.lock = EngineLock(engine_lock_path(root))
        self.lock.acquire()

    def acknowledge_stop(self) -> None:
        for queued in self._queue.pending():
            self._queue.mark_done(queued.id, None)

    def exit(self) -> None:
        self.lock.release()
        self._connection.close()

    def stop_queued(self) -> bool:
        return bool(self._queue.pending())


async def _setup(tmp_path: Path) -> tuple[ProjectLifecycle, FakeClock, SlowEngine, Settings]:
    project_state_dir(tmp_path).mkdir()
    settings = load_settings(tmp_path, OVERRIDES)
    clock = FakeClock(TASK_CREATED_AT)
    runner = SubprocessRunner()
    lifecycle = ProjectLifecycle(
        runner=runner,
        tmux=Tmux(runner),
        session=SessionView(Tmux(runner)),
        clock=clock,
        operator_factory=lambda root, loaded: build_operator(root, loaded, clock=clock),
        plugin_validator=_no_validation,
        engine_up=lambda root: engine_is_running(engine_lock_path(root)),
    )
    return lifecycle, clock, SlowEngine(tmp_path, clock), settings


async def _no_validation(_settings: Settings) -> None:
    return None


async def _advance_until_done(clock: FakeClock, work: asyncio.Task[object]) -> None:
    async def finished() -> bool:
        if clock.has_pending_sleep():
            clock.advance(ADVANCE_SECONDS)
        return work.done()

    await wait_until(finished, timeout=10)


async def test_stop_waits_for_the_engine_to_release_its_lock(tmp_path: Path) -> None:
    lifecycle, clock, engine, _ = await _setup(tmp_path)
    stopping = asyncio.create_task(lifecycle.stop(tmp_path, OVERRIDES, kill_session=False))

    await wait_until(engine.stop_queued, timeout=10)
    engine.acknowledge_stop()
    for _ in range(3):
        await wait_until(clock.has_pending_sleep, timeout=10)
        clock.advance(ADVANCE_SECONDS)
    assert not stopping.done()

    engine.exit()
    await _advance_until_done(clock, stopping)
    assert stopping.result().status is AckStatus.DONE


async def test_stop_fails_when_the_engine_outlives_its_shutdown_bound(tmp_path: Path) -> None:
    lifecycle, clock, engine, _ = await _setup(tmp_path)
    stopping = asyncio.create_task(lifecycle.stop(tmp_path, OVERRIDES, kill_session=False))

    await wait_until(engine.stop_queued, timeout=10)
    engine.acknowledge_stop()
    await _advance_until_done(clock, stopping)

    with pytest.raises(ShikiruError):
        await stopping
    engine.exit()
