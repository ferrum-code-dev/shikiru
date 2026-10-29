"""Durable per-task quota evidence: monitored turns and the observations taken during them."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.agents.contracts import QuotaBoundary, QuotaObservationTimeSource
from shikiru.domain.quota_consumption import (
    QuotaSample,
    QuotaTurn,
    TaskQuotaEvidence,
    TurnOutcome,
)
from shikiru.store.database import (
    format_timestamp,
    parse_required_timestamp,
    parse_timestamp,
    require_after_insert,
)

if TYPE_CHECKING:
    import sqlite3  # noqa: TID251
    from datetime import datetime

    from shikiru.agents.contracts import QuotaWindow
    from shikiru.domain.tasks import Role

_TURN_COLUMNS = "id, task_id, agent, started_at, ended_at, outcome"
_SAMPLE_COLUMNS = (
    "samples.turn_id, turns.agent, samples.window, samples.used_pct, samples.resets_at, "
    "samples.observed_at, samples.boundary, samples.time_source"
)


def _row_to_turn(row: sqlite3.Row) -> QuotaTurn:
    return QuotaTurn(
        id=row["id"],
        task_id=row["task_id"],
        agent=row["agent"],
        started_at=parse_required_timestamp(row["started_at"]),
        ended_at=parse_timestamp(row["ended_at"]),
        outcome=TurnOutcome(row["outcome"]),
    )


def _row_to_sample(row: sqlite3.Row) -> QuotaSample:
    return QuotaSample(
        turn_id=row["turn_id"],
        agent=row["agent"],
        window=row["window"],
        used_pct=row["used_pct"],
        resets_at=parse_timestamp(row["resets_at"]),
        observed_at=parse_required_timestamp(row["observed_at"]),
        boundary=QuotaBoundary(row["boundary"]) if row["boundary"] is not None else None,
        time_source=QuotaObservationTimeSource(row["time_source"]),
    )


def fetch_task_quota_evidence(connection: sqlite3.Connection, task_id: str) -> TaskQuotaEvidence:
    """Read `task_id`'s quota evidence and the other tasks' turns on the same providers.

    Other tasks' turns that ended before this task's first turn began cannot overlap it.
    """
    turns = [
        _row_to_turn(row)
        for row in connection.execute(
            f"SELECT {_TURN_COLUMNS} FROM task_quota_turns WHERE task_id = ? ORDER BY id",
            (task_id,),
        )
    ]
    turns.extend(_fetch_legacy_runs(connection, "runs.task_id = ?", (task_id,)))
    turns.sort(key=lambda turn: turn.id)
    samples = _fetch_samples(connection, "turns.task_id = ?", (task_id,))
    if not turns:
        return TaskQuotaEvidence(task_id, turns, samples, [])
    agents = sorted({turn.agent for turn in turns})
    earliest = format_timestamp(min(turn.started_at for turn in turns))
    scope = (
        f"turns.task_id != ? AND turns.agent IN ({', '.join('?' for _ in agents)}) "
        "AND (turns.ended_at IS NULL OR turns.ended_at >= ?)"
    )
    parameters = (task_id, *agents, earliest)
    other_turns = [
        _row_to_turn(row)
        for row in connection.execute(
            f"SELECT {_TURN_COLUMNS} FROM task_quota_turns AS turns WHERE {scope} ORDER BY id",
            parameters,
        )
    ]
    legacy_scope = (
        f"runs.task_id != ? AND runs.agent IN ({', '.join('?' for _ in agents)}) "
        "AND (runs.ended_at IS NULL OR runs.ended_at >= ?)"
    )
    other_turns.extend(_fetch_legacy_runs(connection, legacy_scope, parameters))
    return TaskQuotaEvidence(task_id, turns, samples, other_turns)


def _fetch_legacy_runs(
    connection: sqlite3.Connection, condition: str, parameters: tuple[object, ...]
) -> list[QuotaTurn]:
    rows = connection.execute(
        f"""
        SELECT runs.id, runs.task_id, runs.agent, runs.started_at, runs.ended_at
        FROM runs
        LEFT JOIN task_quota_turns AS quota_turns ON quota_turns.run_id = runs.id
        WHERE quota_turns.id IS NULL AND {condition}
        ORDER BY runs.id
        """,
        parameters,
    )
    return [
        QuotaTurn(
            id=-row["id"],
            task_id=row["task_id"],
            agent=row["agent"],
            started_at=parse_required_timestamp(row["started_at"]),
            ended_at=parse_timestamp(row["ended_at"]),
            outcome=TurnOutcome.LEGACY,
        )
        for row in rows
    ]


def _fetch_samples(
    connection: sqlite3.Connection, condition: str, parameters: tuple[str, ...]
) -> list[QuotaSample]:
    rows = connection.execute(
        f"""
        SELECT {_SAMPLE_COLUMNS}
        FROM task_quota_samples AS samples
        JOIN task_quota_turns AS turns ON turns.id = samples.turn_id
        WHERE {condition}
        ORDER BY samples.id
        """,
        parameters,
    )
    return [_row_to_sample(row) for row in rows]


class TaskQuotaStore:
    """Persists the quota observations each task turn collected, for durable task accounting."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Use `connection` for every write below."""
        self._connection = connection

    def begin_turn(
        self,
        *,
        task_id: str,
        run_id: int,
        turn: str,
        role: Role,
        agent: str,
        started_at: datetime,
    ) -> int:
        """Record the start of one monitored turn attempt and return its id."""
        cursor = self._connection.execute(
            """
            INSERT INTO task_quota_turns (task_id, run_id, turn, role, agent, started_at, outcome)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                run_id,
                turn,
                role.value,
                agent,
                format_timestamp(started_at),
                TurnOutcome.RUNNING.value,
            ),
        )
        return require_after_insert(cursor.lastrowid, "sqlite did not allocate a quota turn id")

    def record_sample(self, turn_id: int, window: QuotaWindow) -> None:
        """Keep one observation taken during `turn_id`, exactly as the plugin reported it."""
        self._connection.execute(
            """
            INSERT INTO task_quota_samples
              (turn_id, window, used_pct, resets_at, observed_at, boundary, time_source)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                turn_id,
                window.name,
                window.used_pct,
                format_timestamp(window.resets_at) if window.resets_at is not None else None,
                format_timestamp(window.observed_at),
                window.boundary.value if window.boundary is not None else None,
                window.time_source.value,
            ),
        )

    def end_turn(self, turn_id: int, *, ended_at: datetime, outcome: TurnOutcome) -> None:
        """Close `turn_id` with how its collection ended."""
        self._connection.execute(
            "UPDATE task_quota_turns SET ended_at = ?, outcome = ? WHERE id = ?",
            (format_timestamp(ended_at), outcome.value, turn_id),
        )

    def abort_open_turns(self, ended_at: datetime) -> None:
        """Mark monitored turns left open by a dead engine as interrupted."""
        self._connection.execute(
            "UPDATE task_quota_turns SET ended_at = ?, outcome = ? WHERE outcome = ?",
            (format_timestamp(ended_at), TurnOutcome.ABORTED.value, TurnOutcome.RUNNING.value),
        )
