"""The durable notification cursor (docs/design/06-operator.md §5.7)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import sqlite3  # noqa: TID251


class NotificationCursor:
    """Stores the id up to which every urgent event has been delivered."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Use `connection`, the engine's writer."""
        self._connection = connection

    def read(self) -> int | None:
        """Return the stored event id, or `None` before the first engine start."""
        row = self._connection.execute("SELECT event_id FROM notification_cursor").fetchone()
        return None if row is None else int(row[0])

    def write(self, event_id: int) -> None:
        """Store `event_id` as the delivered-up-to id."""
        self._connection.execute(
            "INSERT INTO notification_cursor (id, event_id) VALUES (1, ?) "
            "ON CONFLICT (id) DO UPDATE SET event_id = excluded.event_id",
            (event_id,),
        )
