"""Integration tests for `EventLog.since` filtering."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from shikiru.domain.commands import COMMAND_ID_KEY
from shikiru.domain.events import EventKind
from shikiru.store.events import COMMAND_APPLIED_SQL, EventLog
from tests.support.clock import FakeClock

if TYPE_CHECKING:
    from tests.support.store import Connection

START = datetime(2026, 1, 1, tzinfo=UTC)


def _make_log(connection: Connection) -> EventLog:
    return EventLog(connection, FakeClock(START))


@pytest.mark.parametrize(
    ("after_id", "kinds", "task_id", "expected_messages"),
    [
        pytest.param(0, None, None, ["queued", "started", "other"], id="all-in-order"),
        pytest.param(1, None, None, ["started", "other"], id="after-id"),
        pytest.param(0, [EventKind.TASK_STARTED], None, ["started"], id="kind-filter"),
        pytest.param(0, None, "T-1", ["queued", "started"], id="task-filter"),
    ],
)
def test_since_filters_and_orders_events(
    connection: Connection,
    after_id: int,
    kinds: list[EventKind] | None,
    task_id: str | None,
    expected_messages: list[str],
) -> None:
    log = _make_log(connection)
    log.append(EventKind.TASK_QUEUED, message="queued", task_id="T-1")
    log.append(EventKind.TASK_STARTED, message="started", task_id="T-1")
    log.append(EventKind.TASK_QUEUED, message="other", task_id="T-2")

    events = log.since(after_id, kinds=kinds, task_id=task_id)

    assert [event.message for event in events] == expected_messages


def test_data_round_trips_through_the_json_column(connection: Connection) -> None:
    log = _make_log(connection)
    payload = {"rung": {"agent": "codex", "model": "sol"}, "attempt": 2}

    appended = log.append(EventKind.TASK_QUEUED, message="queued", task_id="T-1", data=payload)

    assert appended.data == payload
    [reloaded] = log.since(0)
    assert reloaded.data == payload


def test_command_applied_lookup_never_scans_the_whole_event_log(connection: Connection) -> None:
    plan = connection.execute(
        f"EXPLAIN QUERY PLAN {COMMAND_APPLIED_SQL}",
        (EventKind.TAKEN_OVER.value, f"$.{COMMAND_ID_KEY}", 1),
    ).fetchall()

    details = " ".join(row["detail"] for row in plan)
    assert "SEARCH" in details
    assert "SCAN" not in details
