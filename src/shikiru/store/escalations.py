"""Escalation rows: open, list and resolve (docs/design/05-data.md §2; 03 §8)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import TypeAdapter

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
        step: Step | None,
        reason: EscalationReason,
        headline: str,
        packet_path: str,
        options: tuple[EscalationOption, ...],
    ) -> Escalation:
        """Allocate an `E-n` id and record a new open escalation."""
        with transaction(self._connection):
            seq = next_sequence(self._connection, ESCALATION_SEQUENCE)
            escalation_id = f"{ESCALATION_ID_PREFIX}{seq}"
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
        escalation = fetch_escalation(self._connection, escalation_id)
        return require_after_insert(
            escalation, f"escalation {escalation_id} vanished immediately after insert"
        )

    def set_packet_path(self, escalation_id: str, packet_path: str) -> None:
        """Record `escalation_id`'s packet path once its file has been written (03 §8)."""
        self._connection.execute(
            "UPDATE escalations SET packet_path = ? WHERE id = ?", (packet_path, escalation_id)
        )

    def get(self, escalation_id: str) -> Escalation | None:
        """Read one escalation by id, or `None` if it doesn't exist."""
        return fetch_escalation(self._connection, escalation_id)

    def list_open(self) -> list[Escalation]:
        """Read every open escalation, oldest first."""
        return fetch_open_escalations(self._connection)

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
