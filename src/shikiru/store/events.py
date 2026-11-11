"""Event log: append typed events, read them back since an id."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import TypeAdapter

from shikiru.domain.commands import COMMAND_ID_KEY
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
    import sqlite3  # noqa: TID251
    from collections.abc import Sequence

    from shikiru.clock import Clock

_EVENT_ROLE_JSON_PATH = "$.role"
_EVENT_TURN_JSON_PATH = "$.turn"
_EVENT_COMMAND_JSON_PATH = f"$.{COMMAND_ID_KEY}"
MAX_TIMELINE_EVENT_LIMIT = 50

COMMAND_APPLIED_SQL = "SELECT 1 FROM events WHERE kind = ? AND json_extract(data, ?) = ? LIMIT 1"

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
    hide_verdict_duplicates: bool = False,
    task_id: str | None = None,
) -> list[Event]:
    """Read the latest matching events in chronological order.

    With `hide_verdict_duplicates`, a Leader `turn_completed` is left out when a `verdict` of
    the same task and turn records it, so the verdict stands for that turn.
    """
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
    if hide_verdict_duplicates:
        clauses.append(
            "NOT (kind = ? AND json_extract(data, ?) IS ? AND EXISTS ("
            "SELECT 1 FROM events AS verdict WHERE verdict.task_id = events.task_id"
            " AND verdict.kind = ?"
            " AND json_extract(verdict.data, ?) = json_extract(events.data, ?)))"
        )
        params.extend(
            (
                EventKind.TURN_COMPLETED.value,
                _EVENT_ROLE_JSON_PATH,
                Role.LEADER.value,
                EventKind.VERDICT.value,
                _EVENT_TURN_JSON_PATH,
                _EVENT_TURN_JSON_PATH,
            )
        )
    where = f"WHERE {' AND '.join(clauses)} " if clauses else ""
    rows = connection.execute(
        f"SELECT id, ts, task_id, kind, message, data FROM events {where}ORDER BY id DESC LIMIT ?",
        [*params, limit],
    ).fetchall()
    return [_row_to_event(row) for row in reversed(rows)]


def fetch_timeline_events(
    connection: sqlite3.Connection,
    task_id: str,
    *,
    limit: int,
    before: tuple[str, int] | None = None,
    kinds: Sequence[EventKind] | None = None,
    role: str | None = None,
    turn: str | None = None,
) -> list[Event]:
    """Read one newest-first, keyset-paginated task event page with SQL filters."""
    if not 0 < limit <= MAX_TIMELINE_EVENT_LIMIT:
        message = f"task timeline event limit must be between 1 and {MAX_TIMELINE_EVENT_LIMIT}"
        raise ValueError(message)
    if kinds is not None and not kinds:
        return []
    clauses = ["task_id = ?"]
    params: list[object] = [task_id]
    if before is not None:
        clauses.append("(ts < ? OR (ts = ? AND id < ?))")
        params.extend((before[0], before[0], before[1]))
    if kinds is not None:
        placeholders = ", ".join("?" for _ in kinds)
        clauses.append(f"kind IN ({placeholders})")
        params.extend(kind.value for kind in kinds)
    if role is not None:
        clauses.append("json_extract(data, ?) = ?")
        params.extend((_EVENT_ROLE_JSON_PATH, role))
    if turn is not None:
        clauses.append("json_extract(data, ?) = ?")
        params.extend((_EVENT_TURN_JSON_PATH, turn))
    where = " AND ".join(clauses)
    rows = connection.execute(
        f"SELECT id, ts, task_id, kind, message, data FROM events WHERE {where} "
        "ORDER BY ts DESC, id DESC LIMIT ?",
        [*params, limit],
    ).fetchall()
    return [_row_to_event(row) for row in rows]


class EventLog:
    """Appends typed events and reads them back since an id (`domain.events`)."""

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

    def latest(
        self,
        limit: int,
        *,
        kinds: Sequence[EventKind] | None = None,
        task_id: str | None = None,
    ) -> list[Event]:
        """Read the latest matching events in chronological order."""
        return fetch_latest_events(self._connection, limit, kinds=kinds, task_id=task_id)

    def command_applied(self, kind: EventKind, command_id: int) -> bool:
        """Report whether a `kind` event already records the effect of command `command_id`."""
        row = self._connection.execute(
            COMMAND_APPLIED_SQL,
            (kind.value, _EVENT_COMMAND_JSON_PATH, command_id),
        ).fetchone()
        return row is not None

    def latest_id(self) -> int:
        """Return the current event cursor, or zero when the log is empty."""
        row = self._connection.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()
        return int(row[0])
