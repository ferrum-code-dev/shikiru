"""Quota block reconciliation against real SQLite and the fake agent."""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import timedelta
from typing import TYPE_CHECKING

from fake_agent.agent import create as create_fake_agent

from shikiru.agents.contracts import QuotaReading, QuotaWindow
from shikiru.config.settings import AgentConfig
from shikiru.domain.events import EventKind
from shikiru.domain.faults import open_fault_streaks
from shikiru.domain.tasks import Role
from shikiru.engine.agents import AgentPool
from shikiru.engine.quota_reconciler import QUOTA_REFRESH_SCOPE
from shikiru.engine.recovery import TurnRecovery
from shikiru.engine.transitions import TransitionWriter
from shikiru.errors import CommandFailed
from shikiru.process import SubprocessRunner
from shikiru.store.events import EventLog
from shikiru.store.quotas import QuotaStore
from shikiru.store.tasks import TaskStore
from shikiru.tmux.decorator import PaneDecorator
from tests.support.clock import FakeClock
from tests.support.engine import (
    build_fault_reporter,
    build_reconciler,
    build_registry,
    build_titles,
    build_tmux,
    fake_engine_settings,
)
from tests.support.factories import TASK_CREATED_AT
from tests.support.store import seed_task
from tests.support.waiting import SHORT_WAIT_SECONDS, wait_until

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import datetime
    from pathlib import Path

    from shikiru.agents.contracts import (
        Agent,
        AgentContext,
        CheckResult,
        HeadlessAgent,
        Rung,
        Turn,
        TurnSpec,
    )
    from shikiru.config.settings import Settings
    from shikiru.engine.quota_reconciler import QuotaReconciler
    from tests.support.store import Connection
    from tests.support.tmux import TmuxServer

_AGENT = "fake"
_BLOCK_LENGTH = timedelta(days=3)


class _DelegatingAgent:
    """The fake plugin without its optional quota capability; subclasses add one."""

    name = _AGENT

    def __init__(self, delegate: Agent) -> None:
        self._delegate = delegate

    async def start_turn(self, spec: TurnSpec) -> Turn:
        return await self._delegate.start_turn(spec)

    def interactive_command(self, session_id: str, run_dir: Path, rung: Rung) -> Sequence[str]:
        return self._delegate.interactive_command(session_id, run_dir, rung)

    async def doctor(self, models: Sequence[str]) -> list[CheckResult]:
        return await self._delegate.doctor(models)

    def headless(self) -> HeadlessAgent | None:
        return self._delegate.headless()


class _SlowQuotaAgent(_DelegatingAgent):
    def __init__(
        self, delegate: Agent, release: threading.Event, reading: QuotaReading | None
    ) -> None:
        super().__init__(delegate)
        self._release = release
        self._reading = reading

    def read_quota(self, _since: datetime) -> QuotaReading | None:
        self._release.wait()
        return self._reading


class _SlowFirstQuotaAgent(_DelegatingAgent):
    def __init__(
        self,
        delegate: Agent,
        release: threading.Event,
        readings: tuple[QuotaReading, QuotaReading],
    ) -> None:
        super().__init__(delegate)
        self._release = release
        self._readings = readings
        self.calls = 0

    def read_quota(self, _since: datetime) -> QuotaReading:
        self.calls += 1
        if self.calls == 1:
            self._release.wait()
        return self._readings[self.calls - 1]


def _without_quota_capability(_name: str, context: AgentContext) -> Agent:
    return _DelegatingAgent(create_fake_agent(context))


def _reconciler(
    connection: Connection,
    clock: FakeClock,
    *,
    settings: Settings | None = None,
    create: Callable[[str, AgentContext], Agent] | None = None,
) -> QuotaReconciler:
    resolved = settings if settings is not None else fake_engine_settings()
    return build_reconciler(
        quotas=QuotaStore(connection),
        agents=AgentPool(
            create if create is not None else build_registry().create,
            SubprocessRunner(),
            resolved,
            clock,
        ),
        events=EventLog(connection, clock),
        clock=clock,
        settings=resolved,
    )


def test_an_older_available_reading_does_not_clear_a_block_over_newer_exhaustion(
    connection: Connection,
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    quotas = QuotaStore(connection)
    reset = clock.now() + _BLOCK_LENGTH
    quotas.block(_AGENT, reset, "quota", clock.now())
    older = clock.now() + timedelta(seconds=60)
    clock.advance(120)
    quotas.record(_AGENT, QuotaWindow("7d", 100.0, reset, clock.now()))
    clock.advance(60)

    _reconciler(connection, clock).observe(_AGENT, [QuotaWindow("7d", 0.0, reset, older)])

    assert quotas.block_for(_AGENT) is not None
    assert [window.used_pct for window in quotas.windows_for(_AGENT)] == [100.0]


def test_a_reading_without_the_exhausted_window_keeps_a_block_whose_reset_is_unknown(
    connection: Connection,
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    quotas = QuotaStore(connection)
    quotas.record(_AGENT, QuotaWindow("7d", 100.0, None, clock.now()))
    quotas.block(_AGENT, clock.now() + timedelta(hours=1), "quota", clock.now())
    clock.advance(60)
    five_hours = QuotaWindow("5h", 2.0, clock.now() + timedelta(hours=4), clock.now())

    reconciler = _reconciler(connection, clock)
    reconciler.observe(_AGENT, [five_hours])

    assert quotas.block_for(_AGENT) is not None

    reconciler.observe(_AGENT, [five_hours, QuotaWindow("7d", 0.0, None, clock.now())])

    assert quotas.block_for(_AGENT) is None


def test_a_turn_reading_that_releases_the_block_keeps_the_refresh_fault(
    connection: Connection,
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    quotas = QuotaStore(connection)
    events = EventLog(connection, clock)
    reset = clock.now() + _BLOCK_LENGTH
    quotas.record(_AGENT, QuotaWindow("7d", 100.0, reset, clock.now()))
    quotas.block(_AGENT, reset, "quota", clock.now())
    reporter = build_fault_reporter(events, clock, fake_engine_settings())
    reporter.report(
        CommandFailed(("fake",), 1, "unreadable"), scope=f"{QUOTA_REFRESH_SCOPE} {_AGENT}"
    )
    clock.advance(60)

    _reconciler(connection, clock).observe(_AGENT, [QuotaWindow("7d", 0.0, reset, clock.now())])

    assert quotas.block_for(_AGENT) is None
    assert events.since(0, kinds=[EventKind.ENGINE_RECOVERED]) == []
    recorded = events.since(0, kinds=[EventKind.ENGINE_FAULT, EventKind.ENGINE_RECOVERED])
    (streak,) = open_fault_streaks(recorded)
    assert streak.scope == f"{QUOTA_REFRESH_SCOPE} {_AGENT}"


def _block_with_refresh_fault(
    connection: Connection, clock: FakeClock, until: datetime
) -> EventLog:
    events = EventLog(connection, clock)
    QuotaStore(connection).block(_AGENT, until, "quota", clock.now())
    reporter = build_fault_reporter(events, clock, fake_engine_settings())
    reporter.report(
        CommandFailed(("fake",), 1, "unreadable"), scope=f"{QUOTA_REFRESH_SCOPE} {_AGENT}"
    )
    return events


async def test_verify_open_faults_records_recovery_when_the_probe_works(
    connection: Connection,
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    events = _block_with_refresh_fault(connection, clock, clock.now() + _BLOCK_LENGTH)
    reconciler = _reconciler(connection, clock)

    reconciler.verify_open_faults()
    await wait_until(lambda: not reconciler.probing, SHORT_WAIT_SECONDS)

    assert len(events.since(0, kinds=[EventKind.ENGINE_RECOVERED])) == 1
    recorded = events.since(0, kinds=[EventKind.ENGINE_FAULT, EventKind.ENGINE_RECOVERED])
    assert open_fault_streaks(recorded) == []


async def test_verify_open_faults_keeps_the_refresh_fault_when_the_probe_is_broken(
    connection: Connection, tmp_path: Path
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    events = _block_with_refresh_fault(connection, clock, clock.now() + _BLOCK_LENGTH)
    settings = fake_engine_settings().model_copy(
        update={"agents": {_AGENT: AgentConfig(options={"quota_file": str(tmp_path / "gone")})}}
    )
    reconciler = _reconciler(connection, clock, settings=settings)

    reconciler.verify_open_faults()
    await wait_until(lambda: not reconciler.probing, SHORT_WAIT_SECONDS)

    assert events.since(0, kinds=[EventKind.ENGINE_RECOVERED]) == []
    recorded = events.since(0, kinds=[EventKind.ENGINE_FAULT, EventKind.ENGINE_RECOVERED])
    (streak,) = open_fault_streaks(recorded)
    assert streak.scope == f"{QUOTA_REFRESH_SCOPE} {_AGENT}"


async def test_verify_open_faults_does_not_wait_for_a_slow_source(
    connection: Connection,
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    events = _block_with_refresh_fault(connection, clock, clock.now() + _BLOCK_LENGTH)
    release = threading.Event()

    def slow_agent(_name: str, context: AgentContext) -> Agent:
        return _SlowQuotaAgent(create_fake_agent(context), release, reading=None)

    reconciler = _reconciler(connection, clock, create=slow_agent)
    try:
        reconciler.verify_open_faults()

        assert reconciler.probing
        assert events.since(0, kinds=[EventKind.ENGINE_RECOVERED]) == []
    finally:
        release.set()
    await wait_until(lambda: not reconciler.probing, SHORT_WAIT_SECONDS)

    assert len(events.since(0, kinds=[EventKind.ENGINE_RECOVERED])) == 1


def _slow_active_block_setup(
    connection: Connection, clock: FakeClock
) -> tuple[EventLog, QuotaStore, threading.Event, QuotaReading]:
    events = EventLog(connection, clock)
    quotas = QuotaStore(connection)
    reset = clock.now() + _BLOCK_LENGTH
    quotas.record(_AGENT, QuotaWindow("7d", 100.0, reset, clock.now()))
    quotas.block(_AGENT, reset, "quota", clock.now())
    reporter = build_fault_reporter(events, clock, fake_engine_settings())
    reporter.report(
        CommandFailed(("fake",), 1, "unreadable"), scope=f"{QUOTA_REFRESH_SCOPE} {_AGENT}"
    )
    clock.advance(60)
    healthy = QuotaReading(
        windows=(QuotaWindow("7d", 0.0, reset, clock.now()),), observed_at=clock.now()
    )
    return events, quotas, threading.Event(), healthy


async def test_startup_verification_of_an_active_block_applies_its_reading_under_a_refresh(
    connection: Connection,
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    events, quotas, release, healthy = _slow_active_block_setup(connection, clock)

    def slow_agent(_name: str, context: AgentContext) -> Agent:
        return _SlowQuotaAgent(create_fake_agent(context), release, healthy)

    reconciler = _reconciler(connection, clock, create=slow_agent)
    try:
        reconciler.verify_open_faults()
        reconciler.request_refresh(_AGENT)

        assert reconciler.probing
        assert len(events.since(0, kinds=[EventKind.ENGINE_FAULT])) == 1
    finally:
        release.set()
    await wait_until(lambda: not reconciler.probing, SHORT_WAIT_SECONDS)

    assert quotas.block_for(_AGENT) is None
    assert len(events.since(0, kinds=[EventKind.ENGINE_FAULT])) == 1
    assert len(events.since(0, kinds=[EventKind.ENGINE_RECOVERED])) == 1
    assert (
        open_fault_streaks(
            events.since(0, kinds=[EventKind.ENGINE_FAULT, EventKind.ENGINE_RECOVERED])
        )
        == []
    )


async def test_a_requeue_during_a_startup_verification_of_an_active_block_is_demanded(
    connection: Connection,
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    _events, quotas, release, healthy = _slow_active_block_setup(connection, clock)

    def slow_agent(_name: str, context: AgentContext) -> Agent:
        return _SlowQuotaAgent(create_fake_agent(context), release, healthy)

    reconciler = _reconciler(connection, clock, create=slow_agent)
    block = quotas.block_for(_AGENT)
    assert block is not None
    try:
        reconciler.verify_open_faults()
        clock.advance(1)
        reconciler.request_refresh(_AGENT, demanded_at=clock.now())

        assert reconciler.awaiting_demanded_probe(block)
    finally:
        release.set()
    await wait_until(lambda: not reconciler.probing, SHORT_WAIT_SECONDS)

    assert not reconciler.awaiting_demanded_probe(block)


async def test_new_block_during_block_free_verification_gets_a_block_probe(
    connection: Connection,
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    events = _block_with_refresh_fault(connection, clock, clock.now() + _BLOCK_LENGTH)
    quotas = QuotaStore(connection)
    quotas.clear_block(_AGENT)
    stale_at = clock.now()
    clock.advance(60)
    reset = clock.now() + _BLOCK_LENGTH
    exhausted = QuotaWindow("7d", 100.0, reset, clock.now())
    quotas.record(_AGENT, exhausted)
    stale = QuotaReading(windows=(QuotaWindow("7d", 100.0, reset, stale_at),), observed_at=stale_at)
    healthy = QuotaReading(
        windows=(QuotaWindow("7d", 0.0, reset, clock.now() + timedelta(seconds=1)),),
        observed_at=clock.now() + timedelta(seconds=1),
    )
    release = threading.Event()
    agent_plugins: list[_SlowFirstQuotaAgent] = []

    def slow_agent(_name: str, context: AgentContext) -> Agent:
        plugin = _SlowFirstQuotaAgent(
            create_fake_agent(context),
            release,
            (stale, healthy),
        )
        agent_plugins.append(plugin)
        return plugin

    reconciler = _reconciler(connection, clock, create=slow_agent)
    try:
        reconciler.verify_open_faults()
        assert reconciler.probing

        quotas.block(_AGENT, reset, "quota", clock.now())
        block = quotas.block_for(_AGENT)
        assert block is not None
        clock.advance(1)
        task = reconciler.request_refresh(_AGENT, demanded_at=clock.now())

        assert task is not None
        assert reconciler.awaiting_demanded_probe(block)
        assert quotas.block_for(_AGENT) is not None
        assert agent_plugins[0].calls == 1
    finally:
        release.set()
    await wait_until(lambda: not reconciler.probing, SHORT_WAIT_SECONDS)

    assert agent_plugins[0].calls == 2
    assert quotas.block_for(_AGENT) is None
    assert not reconciler.awaiting_demanded_probe(block)
    assert len(events.since(0, kinds=[EventKind.ENGINE_FAULT])) == 1
    assert len(events.since(0, kinds=[EventKind.ENGINE_RECOVERED])) == 1


async def test_verify_open_faults_keeps_the_fault_when_the_plugin_cannot_read_quota(
    connection: Connection,
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    events = _block_with_refresh_fault(connection, clock, clock.now() + _BLOCK_LENGTH)
    reconciler = _reconciler(connection, clock, create=_without_quota_capability)

    reconciler.verify_open_faults()
    await wait_until(lambda: not reconciler.probing, SHORT_WAIT_SECONDS)

    assert events.since(0, kinds=[EventKind.ENGINE_RECOVERED]) == []
    recorded = events.since(0, kinds=[EventKind.ENGINE_FAULT, EventKind.ENGINE_RECOVERED])
    (streak,) = open_fault_streaks(recorded)
    assert streak.scope == f"{QUOTA_REFRESH_SCOPE} {_AGENT}"


def _expiring_settings(base: Settings, source: Path) -> Settings:
    return base.model_copy(
        update={
            "agents": {_AGENT: AgentConfig(options={"quota_file": str(source)})},
            "orchestration": base.orchestration.model_copy(
                update={
                    "quota_refresh": base.orchestration.quota_refresh.model_copy(
                        update={"interval_seconds": 3600}
                    )
                }
            ),
        }
    )


def _waiting_recovery(
    connection: Connection,
    clock: FakeClock,
    settings: Settings,
    *,
    tmux_server: TmuxServer,
    project: Path,
) -> tuple[TurnRecovery, QuotaReconciler]:
    quotas = QuotaStore(connection)
    events = EventLog(connection, clock)
    tasks = TaskStore(connection, clock)
    titles = build_titles(
        connection=connection,
        clock=clock,
        decorator=PaneDecorator(build_tmux(tmux_server)),
        settings=settings,
        project_root=project,
    )
    reconciler = build_reconciler(
        quotas=quotas,
        agents=AgentPool(build_registry().create, SubprocessRunner(), settings, clock),
        events=events,
        clock=clock,
        settings=settings,
    )
    recovery = TurnRecovery(
        tasks=tasks,
        events=events,
        quota=quotas,
        reconciler=reconciler,
        clock=clock,
        writer=TransitionWriter(tasks=tasks, events=events, titles=titles, settings=settings),
        settings=settings,
    )
    return recovery, reconciler


async def test_waiting_out_a_block_expiry_verifies_the_refresh_fault(
    connection: Connection, tmux_server: TmuxServer, tmp_path: Path
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    source = tmp_path / "quota-source.json"
    settings = _expiring_settings(fake_engine_settings(), source)
    events = _block_with_refresh_fault(connection, clock, clock.now() + timedelta(seconds=60))
    quotas = QuotaStore(connection)
    task = seed_task(connection, clock)
    recovery, reconciler = _waiting_recovery(
        connection, clock, settings, tmux_server=tmux_server, project=tmp_path
    )
    waiting = asyncio.create_task(
        recovery._wait_for_quota(task, Role.IMPLEMENTER)  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    )
    await wait_until(clock.has_pending_sleep, SHORT_WAIT_SECONDS)
    source.write_text(
        json.dumps({"observed_at": clock.now().isoformat(), "windows": []}), encoding="utf-8"
    )
    clock.advance(61)

    assert await waiting is None
    await wait_until(lambda: not reconciler.probing, SHORT_WAIT_SECONDS)
    assert quotas.block_for(_AGENT) is None
    assert [event.kind for event in events.since(0, kinds=[EventKind.QUOTA_CLEARED])]
    assert len(events.since(0, kinds=[EventKind.ENGINE_RECOVERED])) == 1
    recorded = events.since(0, kinds=[EventKind.ENGINE_FAULT, EventKind.ENGINE_RECOVERED])
    assert open_fault_streaks(recorded) == []


async def test_waiting_out_an_expiry_with_a_broken_probe_keeps_the_refresh_fault(
    connection: Connection, tmux_server: TmuxServer, tmp_path: Path
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    settings = _expiring_settings(fake_engine_settings(), tmp_path / "quota-source.json")
    events = _block_with_refresh_fault(connection, clock, clock.now() + timedelta(seconds=60))
    quotas = QuotaStore(connection)
    task = seed_task(connection, clock)
    recovery, reconciler = _waiting_recovery(
        connection, clock, settings, tmux_server=tmux_server, project=tmp_path
    )
    waiting = asyncio.create_task(
        recovery._wait_for_quota(task, Role.IMPLEMENTER)  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    )
    await wait_until(clock.has_pending_sleep, SHORT_WAIT_SECONDS)
    clock.advance(61)

    assert await waiting is None
    await wait_until(lambda: not reconciler.probing, SHORT_WAIT_SECONDS)
    assert quotas.block_for(_AGENT) is None
    assert events.since(0, kinds=[EventKind.ENGINE_RECOVERED]) == []
    recorded = events.since(0, kinds=[EventKind.ENGINE_FAULT, EventKind.ENGINE_RECOVERED])
    (streak,) = open_fault_streaks(recorded)
    assert streak.scope == f"{QUOTA_REFRESH_SCOPE} {_AGENT}"


async def test_waiting_out_an_expiry_without_a_refresh_fault_reads_nothing(
    connection: Connection, tmux_server: TmuxServer, tmp_path: Path
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    settings = _expiring_settings(fake_engine_settings(), tmp_path / "quota-source.json")
    events = EventLog(connection, clock)
    QuotaStore(connection).block(_AGENT, clock.now() + timedelta(seconds=60), "quota", clock.now())
    task = seed_task(connection, clock)
    recovery, reconciler = _waiting_recovery(
        connection, clock, settings, tmux_server=tmux_server, project=tmp_path
    )
    waiting = asyncio.create_task(
        recovery._wait_for_quota(task, Role.IMPLEMENTER)  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    )
    await wait_until(clock.has_pending_sleep, SHORT_WAIT_SECONDS)
    clock.advance(61)

    assert await waiting is None
    assert not reconciler.probing
    assert events.since(0, kinds=[EventKind.ENGINE_FAULT, EventKind.ENGINE_RECOVERED]) == []
