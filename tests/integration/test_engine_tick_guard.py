"""The tick guard's retry pacing and its split between recoverable and unexpected faults."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest

from shikiru.config.settings import FaultsConfig
from shikiru.domain.events import EventKind
from shikiru.engine.tick_guard import EngineFaultLimitError, TickGuard
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


def _ticks(connection: Connection, error: Exception) -> tuple[_Ticks, EventLog]:
    clock = FakeClock(TASK_CREATED_AT)
    events = EventLog(connection, clock)
    guard = TickGuard(
        events=events, clock=clock, settings=_FAULTS, logger=logging.getLogger("tick-guard-test")
    )
    return _Ticks(guard, clock, error), events


async def test_recoverable_faults_are_retried_with_bounded_backoff_and_never_stop_the_engine(
    connection: Connection,
) -> None:
    ticks, events = _ticks(connection, _RECOVERABLE)

    await ticks.run(20)

    assert ticks.attempts == [0, 1, 3, 7, 11, 15, 19]
    (fault,) = events.since(0, kinds=[EventKind.ENGINE_FAULT])
    assert fault.data == {"error": "CommandFailed", "recoverable": True}


async def test_unexpected_faults_are_surfaced_and_end_the_engine_at_the_limit(
    connection: Connection, caplog: pytest.LogCaptureFixture
) -> None:
    ticks, events = _ticks(connection, ValueError("bug"))

    with caplog.at_level(logging.ERROR), pytest.raises(EngineFaultLimitError, match="bug"):
        await ticks.run(20)

    assert ticks.attempts == [0, 1, 3]
    (fault,) = events.since(0, kinds=[EventKind.ENGINE_FAULT])
    assert fault.data == {"error": "ValueError", "recoverable": False}
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
