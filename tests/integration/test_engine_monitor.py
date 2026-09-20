"""`SessionMonitor`: stall detection, launch timeout and intervention scoping (03 §4.2, §9)."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from shikiru.agents.contracts import Activity, PromptSubmitted, SessionEnded, SessionStarted
from shikiru.domain.events import EventKind
from shikiru.domain.recovery import PaneDied
from shikiru.domain.runs import NO_USAGE
from shikiru.domain.tasks import Hold, Role
from shikiru.engine.monitor import Stalled
from shikiru.errors import CommandFailed
from shikiru.store.events import EventLog
from tests.support.clock import FakeClock
from tests.support.engine import FAKE_RUNG, fake_engine_settings, make_monitor, set_script
from tests.support.factories import TASK_CREATED_AT
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Mapping

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


class _MissingPane(_AlwaysAlivePane):
    """A `PaneHandle` whose liveness command reports a missing pane."""

    async def is_alive(self) -> bool:
        raise CommandFailed(("tmux", "is-pane-dead"), 1, "missing pane")


class _FailingSession:
    """An `AgentSession` whose signal stream fails unexpectedly."""

    def signals(self) -> AsyncIterator[AgentSignal]:
        return _FailingSignals()

    async def before_turn(self) -> list[str]:
        return []

    async def usage(self) -> Usage:
        return NO_USAGE

    def snapshot(self) -> Mapping[str, Any]:
        return {}


class _FailingSignals:
    def __aiter__(self) -> _FailingSignals:
        return self

    async def __anext__(self) -> AgentSignal:
        message = "signal stream failed"
        raise RuntimeError(message)


class _ClosedSession:
    """An `AgentSession` whose signal stream ends without a session-ended signal."""

    def signals(self) -> AsyncIterator[AgentSignal]:
        return _ClosedSignals()

    async def before_turn(self) -> list[str]:
        return []

    async def usage(self) -> Usage:
        return NO_USAGE

    def snapshot(self) -> Mapping[str, Any]:
        return {}


class _ClosedSignals:
    def __aiter__(self) -> _ClosedSignals:
        return self

    async def __anext__(self) -> AgentSignal:
        raise StopAsyncIteration


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
    settings = fake_engine_settings()
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
    settings = fake_engine_settings()
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


async def test_missing_pane_publishes_pane_died(engine_env: EngineEnv) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    task = await engine_env.build_task()
    run = engine_env.runs.start(task.id, Role.IMPLEMENTER, FAKE_RUNG)
    settings = fake_engine_settings()
    monitor = make_monitor(
        _SilentSession(),
        _MissingPane(),
        task=task,
        run=run,
        run_dir=engine_env.project_root / "run",
        tasks=engine_env.tasks,
        runs=engine_env.runs,
        clock=clock,
        settings=settings,
        expects_started=False,
    )
    queue = monitor.subscribe()
    run_task = asyncio.ensure_future(monitor.run())
    try:
        await wait_until(clock.has_pending_sleep, timeout=_SIGNAL_TIMEOUT)
        clock.advance(settings.orchestration.poll_seconds)
        signal = await asyncio.wait_for(queue.get(), timeout=_SIGNAL_TIMEOUT)
        assert isinstance(signal, PaneDied)
        await asyncio.wait_for(run_task, timeout=_SIGNAL_TIMEOUT)
    finally:
        if not run_task.done():
            run_task.cancel()
        with suppress(asyncio.CancelledError):
            await run_task


async def test_monitor_exception_publishes_pane_died(engine_env: EngineEnv) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    task = await engine_env.build_task()
    run = engine_env.runs.start(task.id, Role.IMPLEMENTER, FAKE_RUNG)
    settings = fake_engine_settings()
    monitor = make_monitor(
        _FailingSession(),
        _AlwaysAlivePane(),
        task=task,
        run=run,
        run_dir=engine_env.project_root / "run",
        tasks=engine_env.tasks,
        runs=engine_env.runs,
        clock=clock,
        settings=settings,
        expects_started=False,
    )
    queue = monitor.subscribe()
    run_task = asyncio.ensure_future(monitor.run())
    try:
        signal = await asyncio.wait_for(queue.get(), timeout=_SIGNAL_TIMEOUT)
        assert isinstance(signal, PaneDied)
        await asyncio.wait_for(run_task, timeout=_SIGNAL_TIMEOUT)
    finally:
        if not run_task.done():
            run_task.cancel()
        with suppress(asyncio.CancelledError):
            await run_task


async def test_closed_monitor_stream_publishes_pane_died(engine_env: EngineEnv) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    task = await engine_env.build_task()
    run = engine_env.runs.start(task.id, Role.IMPLEMENTER, FAKE_RUNG)
    settings = fake_engine_settings()
    monitor = make_monitor(
        _ClosedSession(),
        _AlwaysAlivePane(),
        task=task,
        run=run,
        run_dir=engine_env.project_root / "run",
        tasks=engine_env.tasks,
        runs=engine_env.runs,
        clock=clock,
        settings=settings,
        expects_started=False,
    )
    queue = monitor.subscribe()
    run_task = asyncio.ensure_future(monitor.run())
    try:
        signal = await asyncio.wait_for(queue.get(), timeout=_SIGNAL_TIMEOUT)
        assert isinstance(signal, PaneDied)
        await asyncio.wait_for(run_task, timeout=_SIGNAL_TIMEOUT)
    finally:
        if not run_task.done():
            run_task.cancel()
        with suppress(asyncio.CancelledError):
            await run_task


async def test_stall_is_published_once_per_episode(engine_env: EngineEnv) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    task = await engine_env.build_task()
    run = engine_env.runs.start(task.id, Role.IMPLEMENTER, FAKE_RUNG)
    settings = fake_engine_settings()
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
    settings = fake_engine_settings()
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


async def test_intervention_prompt_emits_an_event(
    engine_env: EngineEnv,
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    task = await engine_env.build_task()
    run = engine_env.runs.start(task.id, Role.IMPLEMENTER, FAKE_RUNG)
    session = _ScriptedSession()
    events = EventLog(engine_env.connection, clock)
    monitor = make_monitor(
        session,
        _AlwaysAlivePane(),
        task=task,
        run=run,
        run_dir=engine_env.project_root / "run",
        tasks=engine_env.tasks,
        runs=engine_env.runs,
        clock=clock,
        settings=fake_engine_settings(),
        expects_started=False,
        events=events,
    )
    queue = monitor.subscribe()
    run_task = asyncio.ensure_future(monitor.run())

    def _has_intervention(task_id: str) -> bool:
        current = engine_env.tasks.get(task_id)
        return current is not None and Hold.INTERVENTION in current.holds

    try:
        session.push(PromptSubmitted(text="operator input"))
        await wait_until(lambda: _has_intervention(task.id), timeout=5)
        event = events.since(0, kinds=[EventKind.INTERVENTION], task_id=task.id)[0]
        assert event.data["text"] == "operator input"
        assert isinstance(await queue.get(), PromptSubmitted)
    finally:
        run_task.cancel()
        with suppress(asyncio.CancelledError):
            await run_task
