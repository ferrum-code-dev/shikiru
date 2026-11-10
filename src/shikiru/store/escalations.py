"""Escalation rows: open, list and resolve."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from shikiru.domain.commands import ResolutionKind
from shikiru.domain.escalation import Escalation, EscalationOption, EscalationStatus
from shikiru.domain.events import EventKind
from shikiru.domain.reports import ReportKind
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
    import sqlite3  # noqa: TID251

    from shikiru.clock import Clock

ESCALATION_ID_PREFIX = "E-"
ESCALATION_SEQUENCE = "escalation"
ANSWER_RESOLUTION_PREFIX = f"{ResolutionKind.ANSWER.value} "
ANSWER_ESCALATION_ID_JSON_PATH = "$.escalation_id"
TURN_STEP_JSON_PATH = "$.step"
REPORT_KIND_JSON_PATH = "$.report_kind"
FEEDBACK_REASON = EscalationReason.FEEDBACK_NEEDS_OPERATOR.value

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
    """Opens and resolves escalations, `E-n` ids allocated from `sequences`."""

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

    def list_open(self) -> list[Escalation]:
        """Read every open escalation, oldest first."""
        return fetch_open_escalations(self._connection)

    def get(self, escalation_id: str) -> Escalation | None:
        """Read one escalation by id, or `None` if it doesn't exist."""
        return fetch_escalation(self._connection, escalation_id)

    def latest_answer(self, task_id: str) -> str | None:
        """Return the Operator answer that the task's unfinished consult still acts on.

        Retries of a consult that the answer started are skipped, so the answer survives
        them; any other escalation, including a feedback question, ends the search.
        """
        rows = self._connection.execute(
            """
            SELECT id, step, reason, resolution FROM escalations
            WHERE task_id = ? AND status = ?
            ORDER BY resolved_at DESC, CAST(substr(id, 3) AS INTEGER) DESC
            """,
            (
                task_id,
                EscalationStatus.RESOLVED.value,
            ),
        )
        for row in rows:
            if row["reason"] == FEEDBACK_REASON:
                return None
            resolution = row["resolution"]
            if resolution is not None and resolution.startswith(ANSWER_RESOLUTION_PREFIX):
                if self._answer_was_consumed(
                    task_id, row["id"], turn_path=TURN_STEP_JSON_PATH, turn_value=Step.CONSULT.value
                ):
                    return None
                return resolution[len(ANSWER_RESOLUTION_PREFIX) :]
            if row["step"] != Step.CONSULT.value:
                return None
        return None

    def latest_feedback_answer(self, task_id: str) -> str | None:
        """Return the Operator answer to the latest feedback question until its round reads it."""
        row = self._connection.execute(
            """
            SELECT id, resolution FROM escalations
            WHERE task_id = ? AND reason = ? AND status = ?
            ORDER BY resolved_at DESC, CAST(substr(id, 3) AS INTEGER) DESC
            LIMIT 1
            """,
            (task_id, FEEDBACK_REASON, EscalationStatus.RESOLVED.value),
        ).fetchone()
        if row is None:
            return None
        resolution = row["resolution"]
        if resolution is None or not resolution.startswith(ANSWER_RESOLUTION_PREFIX):
            return None
        if self._feedback_answer_spent(task_id, row["id"]):
            return None
        return resolution[len(ANSWER_RESOLUTION_PREFIX) :]

    def _feedback_answer_spent(self, task_id: str, escalation_id: str) -> bool:
        """Return whether an analysis read the answer, or a newer round started after it."""
        resolved_at = self._resolution_event_id(task_id, escalation_id)
        if resolved_at is None:
            return False
        analysed = self._event_after(
            task_id,
            resolved_at,
            EventKind.TURN_COMPLETED,
            path=REPORT_KIND_JSON_PATH,
            value=ReportKind.FEEDBACK_ANALYSIS.value,
        )
        restarted = self._event_after(task_id, resolved_at, EventKind.FEEDBACK_STARTED)
        return analysed or restarted

    def _answer_was_consumed(
        self, task_id: str, escalation_id: str, *, turn_path: str, turn_value: str
    ) -> bool:
        resolved_at = self._resolution_event_id(task_id, escalation_id)
        if resolved_at is None:
            return False
        return self._event_after(
            task_id,
            resolved_at,
            EventKind.TURN_COMPLETED,
            path=turn_path,
            value=turn_value,
        )

    def _resolution_event_id(self, task_id: str, escalation_id: str) -> int | None:
        row = self._connection.execute(
            """
            SELECT id FROM events
            WHERE task_id = ? AND kind = ? AND json_extract(data, ?) = ?
            ORDER BY id DESC LIMIT 1
            """,
            (
                task_id,
                EventKind.ESCALATION_RESOLVED.value,
                ANSWER_ESCALATION_ID_JSON_PATH,
                escalation_id,
            ),
        ).fetchone()
        return row["id"] if row is not None else None

    def _event_after(
        self,
        task_id: str,
        after_id: int,
        kind: EventKind,
        *,
        path: str | None = None,
        value: str | None = None,
    ) -> bool:
        row = self._connection.execute(
            """
            SELECT 1 FROM events
            WHERE task_id = ? AND kind = ? AND id > ? AND (? IS NULL OR json_extract(data, ?) = ?)
            LIMIT 1
            """,
            (task_id, kind.value, after_id, path, path, value),
        ).fetchone()
        return row is not None

    def settle_open(self, task_id: str, resolution: str) -> None:
        """Resolve every open escalation of `task_id`, so none can act on its later state."""
        for escalation in fetch_open_escalations(self._connection):
            if escalation.task_id == task_id:
                self.resolve(escalation.id, resolution)

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
