"""Row retention: the finished history that can be pruned from the state DB."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.domain.commands import CommandStatus
from shikiru.domain.escalation import EscalationStatus
from shikiru.domain.events import EventKind
from shikiru.domain.tasks import Status
from shikiru.store.database import format_timestamp

if TYPE_CHECKING:
    import sqlite3  # noqa: TID251
    from collections.abc import Sequence
    from datetime import datetime

_FINISHED = (Status.DONE.value, Status.CANCELLED.value)
_FAULT_KINDS = (EventKind.ENGINE_FAULT.value, EventKind.ENGINE_RECOVERED.value)
_NEWEST_EVENT = "id < (SELECT MAX(id) FROM events)"


class StateRetention:
    """Selects and deletes rows past the retention horizon, never the ones still needed.

    The newest event and command row always stay, so ids are never reused: a reused command id
    would match the messages and events filed under the old one. Events not yet covered by the
    notification cursor stay, so an urgent event is never pruned before it is delivered.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Use `connection`, the engine's writer."""
        self._connection = connection

    def settled_task_ids(self, finished_before: datetime) -> list[str]:
        """Return finished tasks older than `finished_before` that still have events.

        A task with an open escalation is not settled: its packet and evidence are still read.
        """
        rows = self._connection.execute(
            f"""
            SELECT id FROM tasks
            WHERE status IN ({_marks(_FINISHED)})
              AND COALESCE(finished_at, created_at) < ?
              AND EXISTS (SELECT 1 FROM events WHERE events.task_id = tasks.id)
              AND NOT EXISTS (
                SELECT 1 FROM escalations
                WHERE escalations.task_id = tasks.id AND escalations.status = ?
              )
            ORDER BY seq
            """,
            (*_FINISHED, format_timestamp(finished_before), EscalationStatus.OPEN.value),
        ).fetchall()
        return [row["id"] for row in rows]

    def delete_task_events(self, task_ids: Sequence[str], *, delivered_up_to: int) -> int:
        """Delete the events of `task_ids` that were delivered; return how many went."""
        deleted = 0
        for task_id in task_ids:
            deleted += self._connection.execute(
                f"DELETE FROM events WHERE task_id = ? AND id <= ? AND {_NEWEST_EVENT}",
                (task_id, delivered_up_to),
            ).rowcount
        return deleted

    def delete_engine_events(self, before: datetime, *, delivered_up_to: int) -> int:
        """Delete delivered engine-level events older than `before`, but not fault records.

        A fault or its recovery stays: `status` derives the open fault streaks from them.
        """
        return self._connection.execute(
            f"""
            DELETE FROM events
            WHERE task_id IS NULL AND ts < ? AND id <= ? AND {_NEWEST_EVENT}
              AND kind NOT IN ({_marks(_FAULT_KINDS)})
            """,
            (format_timestamp(before), delivered_up_to, *_FAULT_KINDS),
        ).rowcount

    def delete_finished_commands(self, before: datetime) -> int:
        """Delete answered commands older than `before`; a pending command is never deleted."""
        return self._connection.execute(
            """
            DELETE FROM commands
            WHERE status != ? AND ts < ? AND id < (SELECT MAX(id) FROM commands)
            """,
            (CommandStatus.PENDING.value, format_timestamp(before)),
        ).rowcount


def _marks(values: Sequence[str]) -> str:
    return ", ".join("?" for _ in values)
