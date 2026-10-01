"""`status` shows the engine's open fault streaks and the age of the oldest escalation."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from shikiru.cli.render import status_renderable
from shikiru.config.settings import FaultsConfig
from shikiru.domain.tasks import EscalationReason
from shikiru.engine.fault_reporter import FaultReporter
from shikiru.engine.tick_guard import TickGuard
from shikiru.errors import CommandFailed
from shikiru.operator.queries import OperatorQueries
from shikiru.store.escalations import EscalationStore
from shikiru.store.events import EventLog
from tests.support.clock import FakeClock
from tests.support.factories import TASK_CREATED_AT, make_settings
from tests.support.render import RENDER_WIDTH, plain_text
from tests.support.store import seed_task

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.store.reader import StateReader
    from tests.support.store import Connection

pytestmark = pytest.mark.asyncio

_FAULTS = FaultsConfig(
    backoff_initial_seconds=1,
    backoff_max_seconds=1,
    unexpected_fault_limit=3,
    stop_notify_seconds=1,
    repeat_notify_seconds=30,
)
_SCOPE = "engine tick"
_TICK_SECONDS = 2


class _Failing:
    def __init__(self) -> None:
        self.error: Exception | None = CommandFailed(("tmux",), 1, "no server")

    async def tick(self) -> None:
        if self.error is not None:
            raise self.error


def _queries(reader: StateReader, tmp_path: Path, clock: FakeClock) -> OperatorQueries:
    return OperatorQueries(
        reader,
        project="demo",
        project_root=tmp_path,
        engine_up=lambda: True,
        max_parallel=2,
        clock=clock,
    )


async def test_status_shows_an_open_fault_streak_until_the_engine_recovers(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    logger = logging.getLogger("fault-status-test")
    reporter = FaultReporter(
        events=EventLog(connection, clock), clock=clock, settings=_FAULTS, logger=logger
    )
    guard = TickGuard(reporter=reporter, clock=clock, settings=_FAULTS, logger=logger, scope=_SCOPE)
    ticks = _Failing()
    queries = _queries(reader, tmp_path, clock)

    for _ in range(40):
        await guard.attempt(ticks.tick)
        clock.advance(_TICK_SECONDS)

    view = queries.status()
    (streak,) = view.faults
    assert (streak.scope, streak.error, streak.task_id) == (_SCOPE, "CommandFailed", None)
    assert streak.recoverable
    assert streak.first_seen == TASK_CREATED_AT
    assert streak.repeat > 1
    assert streak.last_seen > streak.first_seen
    human = plain_text(status_renderable(view, settings=make_settings(), width=RENDER_WIDTH))
    assert _SCOPE in human
    assert "CommandFailed" in human
    assert view.model_dump(mode="json")["faults"][0]["scope"] == _SCOPE

    ticks.error = None
    await guard.attempt(ticks.tick)
    clock.advance(_TICK_SECONDS)

    recovered = queries.status()
    assert recovered.faults == []
    assert _SCOPE not in plain_text(
        status_renderable(recovered, settings=make_settings(), width=RENDER_WIDTH)
    )


async def test_a_task_fault_streak_closes_when_its_isolated_block_succeeds(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    reporter = FaultReporter(
        events=EventLog(connection, clock),
        clock=clock,
        settings=_FAULTS,
        logger=logging.getLogger("fault-status-test"),
    )
    queries = _queries(reader, tmp_path, clock)

    async with reporter.isolate("layout restore", task_id="T-1"):
        raise CommandFailed(("tmux",), 1, "gone")
    assert [fault.task_id for fault in queries.status().faults] == ["T-1"]

    async with reporter.isolate("layout restore", task_id="T-1"):
        pass
    assert queries.status().faults == []


async def test_status_shows_the_age_of_the_oldest_unresolved_escalation(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    escalations = EscalationStore(connection, clock)
    queries = _queries(reader, tmp_path, clock)
    assert queries.status().oldest_escalation_age_seconds is None

    for title in ("older", "newer"):
        task = seed_task(connection, clock, title=title)
        packet = tmp_path / f"{title}.md"
        packet.write_text(title)
        escalations.open(
            task.id,
            step=None,
            reason=EscalationReason.DECISION_REQUIRED,
            headline=title,
            packet_path=str(packet),
            options=(),
        )
        clock.advance(3600)
    clock.advance(60)

    view = queries.status()

    assert view.oldest_escalation_age_seconds == timedelta(hours=2, minutes=1).total_seconds()
    human = plain_text(status_renderable(view, settings=make_settings(), width=RENDER_WIDTH))
    assert "2:01:00" in human
