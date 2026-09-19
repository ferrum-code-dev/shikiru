"""Message sequence allocation for operator messages in task run files."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.store.database import next_sequence, transaction

if TYPE_CHECKING:
    import sqlite3

MESSAGE_SEQUENCE = "message"


class MessageStore:
    """Allocates monotonic message numbers from the state database."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Use `connection` for sequence allocation."""
        self._connection = connection

    def next_number(self) -> int:
        """Allocate and return the next operator-message number."""
        with transaction(self._connection):
            return next_sequence(self._connection, MESSAGE_SEQUENCE)
