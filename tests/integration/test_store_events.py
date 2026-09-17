"""Integration tests for `EventLog.since` filtering (docs/design/05-data.md §2)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from shikiru.domain.events import EventKind
from shikiru.store.database import Database
from shikiru.store.events import EventLog
from tests.support.clock import FakeClock

if TYPE_CHECKING:
    from pathlib import Path

START = datetime(2026, 1, 1, tzinfo=UTC)


def _make_log(tmp_path: Path) -> EventLog:
    connection = Database(tmp_path / "state.db").connect()
    return EventLog(connection, FakeClock(START))


def test_since_returns_everything_after_the_given_id_in_order(tmp_path: Path) -> None:
    log = _make_log(tmp_path)
    first = log.append(EventKind.TASK_QUEUED, message="queued", task_id="T-1")
    log.append(EventKind.TASK_STARTED, message="started", task_id="T-1")
    log.append(EventKind.TASK_QUEUED, message="queued", task_id="T-2")

    assert [event.message for event in log.since(0)] == ["queued", "started", "queued"]
    assert [event.message for event in log.since(first.id)] == ["started", "queued"]


def test_since_filters_by_kinds(tmp_path: Path) -> None:
    log = _make_log(tmp_path)
    log.append(EventKind.TASK_QUEUED, message="queued", task_id="T-1")
    log.append(EventKind.TASK_STARTED, message="started", task_id="T-1")

    only_started = log.since(0, kinds=[EventKind.TASK_STARTED])

    assert [event.kind for event in only_started] == [EventKind.TASK_STARTED]


def test_since_filters_by_task_id(tmp_path: Path) -> None:
    log = _make_log(tmp_path)
    log.append(EventKind.TASK_QUEUED, message="one", task_id="T-1")
    log.append(EventKind.TASK_QUEUED, message="two", task_id="T-2")

    only_task_one = log.since(0, task_id="T-1")

    assert [event.message for event in only_task_one] == ["one"]
