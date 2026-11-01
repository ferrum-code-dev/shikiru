"""The durable record of locally integrated base branches awaiting a push."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.domain.pushes import PushTarget, pending_deadline
from shikiru.store.database import (
    format_timestamp,
    parse_timestamp,
    transaction,
)

if TYPE_CHECKING:
    import sqlite3  # noqa: TID251
    from datetime import datetime

_COLUMNS = "base, registered, pushed, due_at, last_pushed_at"


class PushTargets:
    """Stores, per base branch, the integrations registered and the ones a push acknowledged."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Use `connection`, the engine's writer."""
        self._connection = connection

    def register(self, base: str, *, now: datetime, interval: int) -> None:
        """Record one more integration of `base` awaiting a push.

        The first pending integration sets the deadline; later ones never move it.
        """
        with transaction(self._connection):
            current = self.get(base)
            if current is None:
                due_at = pending_deadline(last_pushed_at=None, now=now, interval=interval)
                self._connection.execute(
                    f"INSERT INTO push_targets ({_COLUMNS}) VALUES (?, 1, 0, ?, NULL)",
                    (base, format_timestamp(due_at)),
                )
                return
            due_at = (
                current.due_at
                if current.pending and current.due_at is not None
                else pending_deadline(
                    last_pushed_at=current.last_pushed_at, now=now, interval=interval
                )
            )
            self._connection.execute(
                "UPDATE push_targets SET registered = registered + 1, due_at = ? WHERE base = ?",
                (format_timestamp(due_at), base),
            )

    def get(self, base: str) -> PushTarget | None:
        """Return `base`'s record, or `None` when no integration of it was ever registered."""
        return fetch_push_target(self._connection, base)

    def pending(self) -> list[PushTarget]:
        """Return every base with integrations no push has acknowledged, oldest deadline first."""
        rows = self._connection.execute(
            f"SELECT {_COLUMNS} FROM push_targets WHERE registered > pushed ORDER BY due_at, base"
        ).fetchall()
        return [_target(row) for row in rows]

    def acknowledge(self, base: str, *, registered: int, now: datetime, interval: int) -> None:
        """Record a push at `now` that sent the integrations registered up to `registered`.

        Integrations registered meanwhile stay pending, due an interval after this push.
        """
        with transaction(self._connection):
            current = self.get(base)
            if current is None:
                return
            due_at = (
                format_timestamp(pending_deadline(last_pushed_at=now, now=now, interval=interval))
                if current.registered > registered
                else None
            )
            self._connection.execute(
                "UPDATE push_targets SET pushed = ?, due_at = ?, last_pushed_at = ? WHERE base = ?",
                (max(current.pushed, registered), due_at, format_timestamp(now), base),
            )


def fetch_push_target(connection: sqlite3.Connection, base: str) -> PushTarget | None:
    """Return `base`'s record, or `None` when no integration of it was ever registered."""
    row = connection.execute(
        f"SELECT {_COLUMNS} FROM push_targets WHERE base = ?", (base,)
    ).fetchone()
    return None if row is None else _target(row)


def _target(row: sqlite3.Row) -> PushTarget:
    return PushTarget(
        base=row["base"],
        registered=row["registered"],
        pushed=row["pushed"],
        due_at=parse_timestamp(row["due_at"]),
        last_pushed_at=parse_timestamp(row["last_pushed_at"]),
    )
