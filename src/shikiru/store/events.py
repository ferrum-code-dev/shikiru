"""Event log: append typed events, read them back since an id (docs/design/05-data.md §2)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import TypeAdapter

from shikiru.domain.events import Event, EventKind
from shikiru.domain.tasks import Role
from shikiru.store.database import (
    dump_json,
    format_timestamp,
    load_json,
    parse_required_timestamp,
    require_after_insert,
)

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Sequence

    from shikiru.clock import Clock

_EVENT_ROLE_JSON_PATH = "$.role"

_DATA_ADAPTER: TypeAdapter[dict[str, Any]] = TypeAdapter(dict[str, Any])


def _row_to_event(row: sqlite3.Row) -> Event:
    return Event(
        id=row["id"],
        ts=parse_required_timestamp(row["ts"]),
        task_id=row["task_id"],
        kind=EventKind(row["kind"]),
        message=row["message"],
        data=load_json(_DATA_ADAPTER, row["data"]),
    )


def fetch_events_since(
    connection: sqlite3.Connection,
    since_id: int,
    *,
    kinds: Sequence[EventKind] | None = None,
    task_id: str | None = None,
) -> list[Event]:
    """Read every event with `id > since_id`, optionally filtered by `kinds` and `task_id`."""
    clauses = ["id > ?"]
    params: list[object] = [since_id]
    if kinds:
        placeholders = ", ".join("?" for _ in kinds)
        clauses.append(f"kind IN ({placeholders})")
        params.extend(kind.value for kind in kinds)
    if task_id is not None:
        clauses.append("task_id = ?")
        params.append(task_id)
    where = " AND ".join(clauses)
    rows = connection.execute(
        f"SELECT id, ts, task_id, kind, message, data FROM events WHERE {where} ORDER BY id",
        params,
    ).fetchall()
    return [_row_to_event(row) for row in rows]


def fetch_latest_events(
    connection: sqlite3.Connection,
    limit: int,
    *,
    kinds: Sequence[EventKind] | None = None,
    implementer_turns_only: bool = False,
    task_id: str | None = None,
) -> list[Event]:
    """Read the latest matching events in chronological order."""
    if limit < 1:
        message = "event limit must be positive"
        raise ValueError(message)
    clauses: list[str] = []
    params: list[object] = []
    if kinds:
        placeholders = ", ".join("?" for _ in kinds)
        clauses.append(f"kind IN ({placeholders})")
        params.extend(kind.value for kind in kinds)
    if task_id is not None:
        clauses.append("task_id = ?")
        params.append(task_id)
    if implementer_turns_only:
        clauses.append("(kind != ? OR json_extract(data, ?) = ?)")
        params.extend(
            (EventKind.TURN_COMPLETED.value, _EVENT_ROLE_JSON_PATH, Role.IMPLEMENTER.value)
        )
    where = f"WHERE {' AND '.join(clauses)} " if clauses else ""
    rows = connection.execute(
        f"SELECT id, ts, task_id, kind, message, data FROM events {where}ORDER BY id DESC LIMIT ?",
        [*params, limit],
    ).fetchall()
    return [_row_to_event(row) for row in reversed(rows)]


class EventLog:
    """Appends typed events and reads them back since an id (05 §2; `domain.events`)."""

    def __init__(self, connection: sqlite3.Connection, clock: Clock) -> None:
        """Use `connection` (the engine's writer) and `clock` for every append below."""
        self._connection = connection
        self._clock = clock

    def append(
        self,
        kind: EventKind,
        *,
        message: str,
        task_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> Event:
        """Append one event and return it with its allocated id and timestamp."""
        ts = self._clock.now()
        event_data = data if data is not None else {}
        cursor = self._connection.execute(
            "INSERT INTO events (ts, task_id, kind, message, data) VALUES (?, ?, ?, ?, ?)",
            (
                format_timestamp(ts),
                task_id,
                kind.value,
                message,
                dump_json(_DATA_ADAPTER, event_data),
            ),
        )
        event_id = require_after_insert(
            cursor.lastrowid, "sqlite did not allocate a rowid for the new event"
        )
        return Event(
            id=event_id, ts=ts, task_id=task_id, kind=kind, message=message, data=event_data
        )

    def since(
        self,
        since_id: int,
        *,
        kinds: Sequence[EventKind] | None = None,
        task_id: str | None = None,
    ) -> list[Event]:
        """Read every event since `since_id`, optionally filtered by `kinds` and `task_id`."""
        return fetch_events_since(self._connection, since_id, kinds=kinds, task_id=task_id)

    def latest_id(self) -> int:
        """Return the current event cursor, or zero when the log is empty."""
        row = self._connection.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()
        return int(row[0])
