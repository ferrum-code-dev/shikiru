"""The tick guard's retry pacing and its split between recoverable and unexpected faults."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest

from shikiru.config.settings import FaultsConfig
from shikiru.domain.events import EventKind
from shikiru.engine.fault_reporter import FaultReporter
from shikiru.engine.tick_guard import EngineFaultLimitError, TickGuard
from shikiru.errors import CommandFailed, InvariantError
from shikiru.store.database import OperationalDatabaseError
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
_RECOVERABLE = CommandFailed(("tmux",), 1, "no server running")


class _Ticks:
    def __init__(self, guard: TickGuard, clock: FakeClock, error: Exception | None) -> None:
        self.guard = guard
        self.clock = clock
        self.error = error
        self.attempts: list[float] = []

    async def fail(self) -> None:
        self.attempts.append((self.clock.now() - TASK_CREATED_AT).total_seconds())
        if self.error is not None:
            raise self.error

    async def run(self, seconds: int) -> None:
        for _ in range(seconds):
            await self.guard.attempt(self.fail)
            self.clock.advance(1)


def _ticks(
    connection: Connection, error: Exception, faults: FaultsConfig = _FAULTS
) -> tuple[_Ticks, EventLog]:
    clock = FakeClock(TASK_CREATED_AT)
    events = EventLog(connection, clock)
    logger = logging.getLogger("tick-guard-test")
    reporter = FaultReporter(events=events, clock=clock, settings=faults, logger=logger)
    guard = TickGuard(
        reporter=reporter, clock=clock, settings=faults, logger=logger, scope="engine tick"
    )
    return _Ticks(guard, clock, error), events


async def test_recoverable_faults_are_retried_with_bounded_backoff_and_never_stop_the_engine(
    connection: Connection,
) -> None:
    ticks, events = _ticks(connection, _RECOVERABLE)

    await ticks.run(20)

    assert ticks.attempts == [0, 1, 3, 7, 11, 15, 19]
    (fault,) = events.since(0, kinds=[EventKind.ENGINE_FAULT])
    assert fault.data == {
        "scope": "engine tick",
        "error": "CommandFailed",
        "recoverable": True,
        "repeat": 1,
    }


async def test_unexpected_faults_are_surfaced_and_end_the_engine_at_the_limit(
    connection: Connection, caplog: pytest.LogCaptureFixture
) -> None:
    ticks, events = _ticks(connection, ValueError("bug"))

    with caplog.at_level(logging.ERROR), pytest.raises(EngineFaultLimitError):
        await ticks.run(20)

    assert ticks.attempts == [0, 1, 3]
    (fault,) = events.since(0, kinds=[EventKind.ENGINE_FAULT])
    assert fault.data == {
        "scope": "engine tick",
        "error": "ValueError",
        "recoverable": False,
        "repeat": 1,
    }
    assert any(record.exc_info for record in caplog.records)


async def test_a_successful_tick_resets_the_unexpected_fault_count(connection: Connection) -> None:
    ticks, _ = _ticks(connection, ValueError("bug"))

    await ticks.run(3)
    ticks.error = None
    await ticks.run(1)
    ticks.error = ValueError("bug")
    with pytest.raises(EngineFaultLimitError):
        await ticks.run(20)

    assert ticks.attempts == [0, 1, 3, 4, 5, 7]


async def test_a_flapping_fault_is_recorded_at_most_once_per_interval_and_a_new_class_at_once(
    connection: Connection,
) -> None:
    ticks, events = _ticks(connection, _RECOVERABLE)

    for second in range(100):
        ticks.error = _RECOVERABLE if second % 2 == 0 else None
        if second == 5:
            ticks.error = OSError("disk")
        await ticks.run(1)

    faults = events.since(0, kinds=[EventKind.ENGINE_FAULT])
    assert [fault.data["error"] for fault in faults] == [
        "CommandFailed",
        "OSError",
        "CommandFailed",
        "CommandFailed",
        "CommandFailed",
    ]


@pytest.mark.parametrize(
    ("error", "recoverable"),
    [
        (CommandFailed(("git",), 1, "boom"), True),
        (OSError("disk full"), True),
        (OperationalDatabaseError("database is locked"), True),
        (InvariantError("T-1 vanished while refreshing its task window"), False),
        (ValueError("bug"), False),
    ],
    ids=["tool failure", "filesystem", "database", "invariant", "programming error"],
)
async def test_only_environmental_faults_are_recoverable(
    connection: Connection, error: Exception, recoverable: bool
) -> None:
    ticks, events = _ticks(connection, error)

    await ticks.run(1)

    (fault,) = events.since(0, kinds=[EventKind.ENGINE_FAULT])
    assert fault.data["recoverable"] is recoverable
