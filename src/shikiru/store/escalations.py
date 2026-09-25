"""Escalation rows: open, list and resolve (docs/design/05-data.md §2; 03 §8)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from shikiru.domain.commands import ResolutionKind
from shikiru.domain.escalation import Escalation, EscalationOption, EscalationStatus
from shikiru.domain.tasks import EscalationReason, Step
from shikiru.store.database import (
    dump_json,
    format_timestamp,
    load_json,
    next_sequence,
    parse_required_timestamp,
    parse_timestamp,
    require_after_insert,
    transaction,
)

if TYPE_CHECKING:
    import sqlite3

    from shikiru.clock import Clock

ESCALATION_ID_PREFIX = "E-"
ESCALATION_SEQUENCE = "escalation"
ANSWER_RESOLUTION_PREFIX = f"{ResolutionKind.ANSWER.value} "

_OPTIONS_ADAPTER: TypeAdapter[tuple[EscalationOption, ...]] = TypeAdapter(
    tuple[EscalationOption, ...]
)

_ESCALATION_COLUMNS = (
    "id, task_id, step, reason, headline, packet_path, options, status, resolution, "
    "created_at, resolved_at"
)


def _row_to_escalation(row: sqlite3.Row) -> Escalation:
    return Escalation(
        id=row["id"],
        task_id=row["task_id"],
        step=Step(row["step"]) if row["step"] is not None else None,
        reason=EscalationReason(row["reason"]),
        headline=row["headline"],
        packet_path=row["packet_path"],
        options=load_json(_OPTIONS_ADAPTER, row["options"]),
        status=EscalationStatus(row["status"]),
        resolution=row["resolution"],
        created_at=parse_required_timestamp(row["created_at"]),
        resolved_at=parse_timestamp(row["resolved_at"]),
    )


def fetch_escalation(connection: sqlite3.Connection, escalation_id: str) -> Escalation | None:
    """Read one escalation by id, or `None` if it doesn't exist."""
    row = connection.execute(
        f"SELECT {_ESCALATION_COLUMNS} FROM escalations WHERE id = ?", (escalation_id,)
    ).fetchone()
    return _row_to_escalation(row) if row is not None else None


def fetch_open_escalations(connection: sqlite3.Connection) -> list[Escalation]:
    """Read every open escalation, oldest first."""
    rows = connection.execute(
        f"SELECT {_ESCALATION_COLUMNS} FROM escalations WHERE status = ? ORDER BY id",
        (EscalationStatus.OPEN.value,),
    ).fetchall()
    return [_row_to_escalation(row) for row in rows]


class EscalationStore:
    """Opens and resolves escalations, `E-n` ids allocated from `sequences` (03 §8)."""

    def __init__(self, connection: sqlite3.Connection, clock: Clock) -> None:
        """Use `connection` (the engine's writer) and `clock` for every write below."""
        self._connection = connection
        self._clock = clock

    def open(
        self,
        task_id: str,
        *,
        escalation_id: str | None = None,
        step: Step | None,
        reason: EscalationReason,
        headline: str,
        packet_path: str,
        options: tuple[EscalationOption, ...],
    ) -> Escalation:
        """Record a new escalation, allocating an id unless one was reserved for its packet."""
        with transaction(self._connection):
            if escalation_id is None:
                escalation_id = self._next_id()
            self._insert(
                escalation_id,
                task_id=task_id,
                step=step,
                reason=reason,
                headline=headline,
                packet_path=packet_path,
                options=options,
            )
        return self._require_opened(escalation_id)

    def reserve_id(self) -> str:
        """Reserve the next escalation id before creating its packet file."""
        with transaction(self._connection):
            return self._next_id()

    def _next_id(self) -> str:
        seq = next_sequence(self._connection, ESCALATION_SEQUENCE)
        return f"{ESCALATION_ID_PREFIX}{seq}"

    def _insert(
        self,
        escalation_id: str,
        *,
        task_id: str,
        step: Step | None,
        reason: EscalationReason,
        headline: str,
        packet_path: str,
        options: tuple[EscalationOption, ...],
    ) -> None:
        if not packet_path:
            raise ValueError
        self._connection.execute(
            """
            INSERT INTO escalations (
              id, task_id, step, reason, headline, packet_path, options, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                escalation_id,
                task_id,
                step.value if step is not None else None,
                reason.value,
                headline,
                packet_path,
                dump_json(_OPTIONS_ADAPTER, options),
                EscalationStatus.OPEN.value,
                format_timestamp(self._clock.now()),
            ),
        )

    def _require_opened(self, escalation_id: str) -> Escalation:
        escalation = fetch_escalation(self._connection, escalation_id)
        return require_after_insert(
            escalation, f"escalation {escalation_id} vanished immediately after insert"
        )

    def get(self, escalation_id: str) -> Escalation | None:
        """Read one escalation by id, or `None` if it doesn't exist."""
        return fetch_escalation(self._connection, escalation_id)

    def list_open(self) -> list[Escalation]:
        """Read every open escalation, oldest first."""
        return fetch_open_escalations(self._connection)

    def latest_answer(self, task_id: str) -> str | None:
        """Return the latest resolved Operator answer for `task_id`, if it was an answer."""
        row = self._connection.execute(
            """
            SELECT resolution FROM escalations
            WHERE task_id = ? AND status = ?
            ORDER BY resolved_at DESC, CAST(substr(id, 3) AS INTEGER) DESC
            LIMIT 1
            """,
            (
                task_id,
                EscalationStatus.RESOLVED.value,
            ),
        ).fetchone()
        if row is None:
            return None
        resolution = row["resolution"]
        if resolution is None or not resolution.startswith(ANSWER_RESOLUTION_PREFIX):
            return None
        return resolution[len(ANSWER_RESOLUTION_PREFIX) :]

    def resolve(self, escalation_id: str, resolution: str) -> None:
        """Mark `escalation_id` resolved, recording `resolution`'s summary text."""
        self._connection.execute(
            "UPDATE escalations SET status = ?, resolution = ?, resolved_at = ? WHERE id = ?",
            (
                EscalationStatus.RESOLVED.value,
                resolution,
                format_timestamp(self._clock.now()),
                escalation_id,
            ),
        )
