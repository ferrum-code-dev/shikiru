"""Integration tests for `EventLog.since` filtering (docs/design/05-data.md §2)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from shikiru.domain.events import EventKind
from shikiru.store.events import EventLog
from tests.support.clock import FakeClock

if TYPE_CHECKING:
    from tests.support.store import Connection

START = datetime(2026, 1, 1, tzinfo=UTC)


def _make_log(connection: Connection) -> EventLog:
    return EventLog(connection, FakeClock(START))


def test_since_returns_everything_after_the_given_id_in_order(connection: Connection) -> None:
    log = _make_log(connection)
    first = log.append(EventKind.TASK_QUEUED, message="queued", task_id="T-1")
    log.append(EventKind.TASK_STARTED, message="started", task_id="T-1")
    log.append(EventKind.TASK_QUEUED, message="queued", task_id="T-2")

    assert [event.message for event in log.since(0)] == ["queued", "started", "queued"]
    assert [event.message for event in log.since(first.id)] == ["started", "queued"]


def test_since_filters_by_kinds(connection: Connection) -> None:
    log = _make_log(connection)
    log.append(EventKind.TASK_QUEUED, message="queued", task_id="T-1")
    log.append(EventKind.TASK_STARTED, message="started", task_id="T-1")

    only_started = log.since(0, kinds=[EventKind.TASK_STARTED])

    assert [event.kind for event in only_started] == [EventKind.TASK_STARTED]


def test_since_filters_by_task_id(connection: Connection) -> None:
    log = _make_log(connection)
    log.append(EventKind.TASK_QUEUED, message="one", task_id="T-1")
    log.append(EventKind.TASK_QUEUED, message="two", task_id="T-2")

    only_task_one = log.since(0, task_id="T-1")

    assert [event.message for event in only_task_one] == ["one"]


def test_data_round_trips_through_the_json_column(connection: Connection) -> None:
    log = _make_log(connection)
    payload = {"rung": {"agent": "codex", "model": "sol"}, "attempt": 2}

    appended = log.append(EventKind.TASK_QUEUED, message="queued", task_id="T-1", data=payload)

    assert appended.data == payload
    [reloaded] = log.since(0)
    assert reloaded.data == payload
