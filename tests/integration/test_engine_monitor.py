"""`SessionMonitor`: stall detection, launch timeout and intervention scoping (03 §4.2, §9)."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from shikiru.agents.contracts import Activity, SessionEnded, SessionStarted
from shikiru.domain.runs import NO_USAGE
from shikiru.domain.tasks import Hold, Role
from shikiru.engine.monitor import Stalled
from tests.support.clock import FakeClock
from tests.support.engine import FAKE_RUNG, fake_agent_settings, make_monitor, set_script
from tests.support.factories import TASK_CREATED_AT, make_settings
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Mapping

    from shikiru.agents.contracts import AgentSignal, Usage
    from shikiru.engine.monitor import MonitorSignal
    from shikiru.store.database import Database
    from tests.support.engine import EngineEnv
    from tests.support.tmux import TmuxServer

_SIGNAL_TIMEOUT = 15.0


async def _next_stalled(queue: asyncio.Queue[MonitorSignal]) -> Stalled:
    while True:
        signal = await asyncio.wait_for(queue.get(), timeout=_SIGNAL_TIMEOUT)
        if isinstance(signal, Stalled):
            return signal


class _SilentSession:
    """An `AgentSession` double that never emits a signal (04 §1)."""

    async def signals(self) -> AsyncGenerator[AgentSignal]:
        while True:
            await asyncio.Event().wait()
            yield SessionEnded()

    async def before_turn(self) -> list[str]:
        return []

    async def usage(self) -> Usage:
        return NO_USAGE

    def snapshot(self) -> Mapping[str, Any]:
        return {}


class _AlwaysAlivePane:
    """A `PaneHandle` double whose process never dies (04 §1)."""

    async def respawn(self, argv: object, env: object, cwd: object) -> None:
        del argv, env, cwd

    async def capture_visible(self) -> str:
        return "stub screen"

    async def is_alive(self) -> bool:
        return True


class _ScriptedSession:
    """An `AgentSession` double fed one signal at a time, with mutable usage/snapshot (04 §1)."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[AgentSignal] = asyncio.Queue()
        self.usage_value: Usage = NO_USAGE
        self.snapshot_value: dict[str, Any] = {}

    def push(self, signal: AgentSignal) -> None:
        self._queue.put_nowait(signal)

    def idle(self) -> bool:
        """Return whether every pushed signal has been picked up by the monitor's consumer."""
        return self._queue.empty()

    async def signals(self) -> AsyncGenerator[AgentSignal]:
        while True:
            yield await self._queue.get()

    async def before_turn(self) -> list[str]:
        return []

    async def usage(self) -> Usage:
        return self.usage_value

    def snapshot(self) -> Mapping[str, Any]:
        return self.snapshot_value


async def test_stall_is_published_after_the_configured_timeout(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    settings = make_settings(implementer_ladder=[FAKE_RUNG], agents=fake_agent_settings())
    manager = engine_env.build_manager(settings, clock=clock)
    task = await engine_env.build_task()
    await set_script(tmux_server, [{"kind": "stall"}])

    await manager.ensure(task, Role.IMPLEMENTER)
    queue = manager.subscribe(task.id, Role.IMPLEMENTER)

    pane_id = task.panes[Role.IMPLEMENTER]
    await engine_env.pane(pane_id).paste_line("go")

    await wait_until(clock.has_pending_sleep, timeout=_SIGNAL_TIMEOUT)
    clock.advance(settings.orchestration.timeouts.stall + 1)

    async def _stall_seen() -> bool:
        signal = await asyncio.wait_for(queue.get(), timeout=_SIGNAL_TIMEOUT)
        return isinstance(signal, Stalled)

    await wait_until(_stall_seen, timeout=_SIGNAL_TIMEOUT)


async def test_launch_timeout_publishes_stalled(engine_env: EngineEnv) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    task = await engine_env.build_task()
    run = engine_env.runs.start(task.id, Role.IMPLEMENTER, FAKE_RUNG)
    settings = make_settings(implementer_ladder=[FAKE_RUNG], agents=fake_agent_settings())
    monitor = make_monitor(
        _SilentSession(),
        _AlwaysAlivePane(),
        task=task,
        run=run,
        run_dir=engine_env.project_root / "run",
        tasks=engine_env.tasks,
        runs=engine_env.runs,
        clock=clock,
        settings=settings,
        expects_started=True,
    )
    queue = monitor.subscribe()
    run_task = asyncio.ensure_future(monitor.run())
    try:
        await wait_until(clock.has_pending_sleep, timeout=_SIGNAL_TIMEOUT)
        clock.advance(settings.orchestration.timeouts.launch + 1)
        signal = await asyncio.wait_for(queue.get(), timeout=_SIGNAL_TIMEOUT)
        assert isinstance(signal, Stalled)
    finally:
        run_task.cancel()
        with suppress(asyncio.CancelledError):
            await run_task


async def test_stall_is_published_once_per_episode(engine_env: EngineEnv) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    task = await engine_env.build_task()
    run = engine_env.runs.start(task.id, Role.IMPLEMENTER, FAKE_RUNG)
    settings = make_settings(implementer_ladder=[FAKE_RUNG], agents=fake_agent_settings())
    session = _ScriptedSession()
    monitor = make_monitor(
        session,
        _AlwaysAlivePane(),
        task=task,
        run=run,
        run_dir=engine_env.project_root / "run",
        tasks=engine_env.tasks,
        runs=engine_env.runs,
        clock=clock,
        settings=settings,
        expects_started=True,
    )
    queue = monitor.subscribe()
    run_task = asyncio.ensure_future(monitor.run())
    try:
        session.push(SessionStarted(session_id="s1"))

        await wait_until(clock.has_pending_sleep, timeout=_SIGNAL_TIMEOUT)
        clock.advance(settings.orchestration.timeouts.stall + 1)
        first = await _next_stalled(queue)
        assert isinstance(first, Stalled)

        await wait_until(clock.has_pending_sleep, timeout=_SIGNAL_TIMEOUT)
        clock.advance(settings.orchestration.poll_seconds)
        await wait_until(clock.has_pending_sleep, timeout=_SIGNAL_TIMEOUT)
        assert queue.empty()

        session.push(Activity(tool_running=False))
        await wait_until(session.idle, timeout=_SIGNAL_TIMEOUT)
        await wait_until(clock.has_pending_sleep, timeout=_SIGNAL_TIMEOUT)
        clock.advance(settings.orchestration.timeouts.stall + 1)
        second = await _next_stalled(queue)
        assert isinstance(second, Stalled)
    finally:
        run_task.cancel()
        with suppress(asyncio.CancelledError):
            await run_task


async def test_tick_persists_facts_only_on_change(
    engine_env: EngineEnv, database: Database
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    task = await engine_env.build_task()
    run = engine_env.runs.start(task.id, Role.IMPLEMENTER, FAKE_RUNG)
    settings = make_settings(implementer_ladder=[FAKE_RUNG], agents=fake_agent_settings())
    session = _ScriptedSession()
    monitor = make_monitor(
        session,
        _AlwaysAlivePane(),
        task=task,
        run=run,
        run_dir=engine_env.project_root / "run",
        tasks=engine_env.tasks,
        runs=engine_env.runs,
        clock=clock,
        settings=settings,
        expects_started=True,
    )
    reader_connection = database.connect_readonly()
    run_task = asyncio.ensure_future(monitor.run())
    try:

        def data_version() -> int:
            row = reader_connection.execute("PRAGMA data_version").fetchone()
            return row[0]

        session.push(SessionStarted(session_id="s1"))

        version_before_first_tick = data_version()
        await wait_until(clock.has_pending_sleep, timeout=_SIGNAL_TIMEOUT)
        clock.advance(settings.orchestration.poll_seconds)
        await wait_until(
            lambda: data_version() != version_before_first_tick, timeout=_SIGNAL_TIMEOUT
        )
        version_after_first_tick = data_version()

        await wait_until(clock.has_pending_sleep, timeout=_SIGNAL_TIMEOUT)
        clock.advance(settings.orchestration.poll_seconds)
        await wait_until(clock.has_pending_sleep, timeout=_SIGNAL_TIMEOUT)
        assert data_version() == version_after_first_tick

        session.snapshot_value = {"offset": 42}
        await wait_until(clock.has_pending_sleep, timeout=_SIGNAL_TIMEOUT)
        clock.advance(settings.orchestration.poll_seconds)
        await wait_until(
            lambda: data_version() != version_after_first_tick, timeout=_SIGNAL_TIMEOUT
        )
    finally:
        run_task.cancel()
        with suppress(asyncio.CancelledError):
            await run_task
        reader_connection.close()


async def test_intervention_hold_from_a_typed_prompt_is_scoped_to_its_own_task(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    task_a = await engine_env.build_task(title="a")
    task_b = await engine_env.build_task(title="b")

    await set_script(tmux_server, [{"kind": "no_report"}])
    await engine_env.manager.ensure(task_a, Role.IMPLEMENTER)
    await engine_env.manager.ensure(task_b, Role.IMPLEMENTER)

    pane_a = task_a.panes[Role.IMPLEMENTER]
    await engine_env.pane(pane_a).paste_line("hello from the operator")

    def _has_intervention(task_id: str) -> bool:
        task = engine_env.tasks.get(task_id)
        return task is not None and Hold.INTERVENTION in task.holds

    await wait_until(lambda: _has_intervention(task_a.id), timeout=_SIGNAL_TIMEOUT)
    assert not _has_intervention(task_b.id)
