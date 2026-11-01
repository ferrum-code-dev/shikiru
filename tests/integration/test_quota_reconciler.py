"""Quota block reconciliation against real SQLite and the fake agent."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from shikiru.agents.contracts import QuotaWindow
from shikiru.engine.agents import AgentPool
from shikiru.process import SubprocessRunner
from shikiru.store.events import EventLog
from shikiru.store.quotas import QuotaStore
from tests.support.clock import FakeClock
from tests.support.engine import build_reconciler, build_registry, fake_engine_settings
from tests.support.factories import TASK_CREATED_AT

if TYPE_CHECKING:
    from shikiru.engine.quota_reconciler import QuotaReconciler
    from tests.support.store import Connection

_AGENT = "fake"
_BLOCK_LENGTH = timedelta(days=3)


def _reconciler(connection: Connection, clock: FakeClock) -> QuotaReconciler:
    settings = fake_engine_settings()
    return build_reconciler(
        quotas=QuotaStore(connection),
        agents=AgentPool(build_registry().create, SubprocessRunner(), settings, clock),
        events=EventLog(connection, clock),
        clock=clock,
        settings=settings,
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
