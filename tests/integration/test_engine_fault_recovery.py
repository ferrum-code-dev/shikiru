"""A fault recorded before an engine restart is closed by the restarted engine's recovery."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from shikiru.config.settings import FaultsConfig
from shikiru.domain.events import EventKind
from shikiru.domain.faults import open_fault_streaks
from shikiru.engine.fault_reporter import FaultReporter
from shikiru.errors import CommandFailed
from shikiru.store.events import EventLog
from tests.support.clock import FakeClock
from tests.support.factories import TASK_CREATED_AT

if TYPE_CHECKING:
    from tests.support.store import Connection

_FAULTS = FaultsConfig(
    backoff_initial_seconds=1,
    backoff_max_seconds=4,
    unexpected_fault_limit=3,
    stop_notify_seconds=1,
    repeat_notify_seconds=30,
)
_SCOPE = "pull request poll"
_TASK = "T-1"


def _engine(connection: Connection) -> tuple[FaultReporter, EventLog]:
    clock = FakeClock(TASK_CREATED_AT)
    events = EventLog(connection, clock)
    reporter = FaultReporter(
        events=events, clock=clock, settings=_FAULTS, logger=logging.getLogger("fault-recovery")
    )
    return reporter, events


def _open(events: EventLog) -> list[tuple[str, str | None]]:
    recorded = events.since(0, kinds=[EventKind.ENGINE_FAULT, EventKind.ENGINE_RECOVERED])
    return [(streak.scope, streak.task_id) for streak in open_fault_streaks(recorded)]


def test_a_restarted_engine_closes_the_fault_recorded_before_the_restart(
    connection: Connection,
) -> None:
    before_restart, events = _engine(connection)
    before_restart.report(
        CommandFailed(("gh",), 1, "no route to host"), scope=_SCOPE, task_id=_TASK
    )
    after_restart, _ = _engine(connection)

    after_restart.resolve(_SCOPE, task_id=_TASK)

    assert _open(events) == []


def test_a_recovery_without_an_open_fault_records_nothing(connection: Connection) -> None:
    engine, events = _engine(connection)
    engine.report(CommandFailed(("gh",), 1, "no route"), scope=_SCOPE, task_id=_TASK)

    engine.resolve(_SCOPE, task_id="T-2")
    engine.resolve(_SCOPE, task_id=_TASK)
    engine.resolve(_SCOPE, task_id=_TASK)

    assert len(events.since(0, kinds=[EventKind.ENGINE_RECOVERED])) == 1
