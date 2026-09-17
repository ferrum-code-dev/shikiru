"""Integration tests for the `CommandQueue` lifecycle (docs/design/05-data.md §2)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from shikiru.domain.commands import CommandStatus, Pause
from shikiru.store.commands import CommandQueue
from shikiru.store.database import Database
from tests.support.clock import FakeClock

if TYPE_CHECKING:
    from pathlib import Path

START = datetime(2026, 1, 1, tzinfo=UTC)


def _make_queue(tmp_path: Path) -> CommandQueue:
    connection = Database(tmp_path / "state.db").open_writer()
    return CommandQueue(connection, FakeClock(START))


def test_pending_returns_typed_commands_and_shrinks_once_done(tmp_path: Path) -> None:
    queue = _make_queue(tmp_path)
    queued = queue.append(Pause(task_id="T-1", now=True))

    assert queue.pending() == [queued]

    queue.mark_done(queued.id, {"ok": True})

    assert queue.pending() == []
    outcome = queue.status(queued.id)
    assert outcome is not None
    assert outcome.status is CommandStatus.DONE
    assert outcome.result == {"ok": True}


def test_mark_rejected_removes_it_from_pending_and_records_the_result(tmp_path: Path) -> None:
    queue = _make_queue(tmp_path)
    queued = queue.append(Pause(task_id="T-1"))

    queue.mark_rejected(queued.id, {"error": "no such task"})

    assert queue.pending() == []
    outcome = queue.status(queued.id)
    assert outcome is not None
    assert outcome.status is CommandStatus.REJECTED
    assert outcome.result == {"error": "no such task"}


def test_status_of_an_unknown_command_is_none(tmp_path: Path) -> None:
    queue = _make_queue(tmp_path)

    assert queue.status(999) is None
