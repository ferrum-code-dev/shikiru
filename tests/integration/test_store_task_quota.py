"""Durable task quota observations survive database reopen and account overlap checks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from shikiru.agents.contracts import QuotaBoundary, QuotaWindow, Rung
from shikiru.domain.quota_consumption import (
    Coverage,
    CoverageGap,
    TurnOutcome,
    task_quota_consumption,
)
from shikiru.domain.tasks import Role
from shikiru.store.runs import RunStore
from shikiru.store.task_quota import TaskQuotaStore, fetch_task_quota_evidence
from tests.support.clock import FakeClock
from tests.support.store import seed_task

if TYPE_CHECKING:
    from shikiru.store.database import Database
    from tests.support.store import Connection

_START = datetime(2026, 1, 1, tzinfo=UTC)
_RESET = _START + timedelta(hours=5)
_RUNG = Rung(agent="codex", model="sol", effort="high")


def test_failed_turn_quota_evidence_retains_run_and_turn_identity_after_reopen(
    database: Database, connection: Connection
) -> None:
    clock = FakeClock(_START)
    task = seed_task(connection, clock, title="Quota persistence")
    runs = RunStore(connection, clock)
    run = runs.start(task.id, Role.IMPLEMENTER, _RUNG)
    runs.set_session(run.id, "session-1", "codex resume session-1")
    evidence_store = TaskQuotaStore(connection)
    turn_id = evidence_store.begin_turn(
        task_id=task.id,
        run_id=run.id,
        turn="02-implement-i1",
        role=Role.IMPLEMENTER,
        agent="codex",
        started_at=_START,
    )
    evidence_store.record_sample(
        turn_id,
        QuotaWindow("5h", 20, _RESET, _START + timedelta(seconds=1), QuotaBoundary.START),
    )
    evidence_store.record_sample(
        turn_id,
        QuotaWindow("5h", 23.5, _RESET, _START + timedelta(seconds=2), QuotaBoundary.END),
    )
    evidence_store.end_turn(
        turn_id, ended_at=_START + timedelta(seconds=3), outcome=TurnOutcome.FAILED
    )

    reopened = database.connect()
    try:
        evidence = fetch_task_quota_evidence(reopened, task.id)
        [turn] = evidence.turns
        [usage] = task_quota_consumption(evidence)
        [stored_identity] = reopened.execute(
            "SELECT run_id, turn, role FROM task_quota_turns WHERE id = ?", (turn_id,)
        ).fetchall()

        assert turn.id == turn_id
        assert turn.task_id == task.id
        assert turn.outcome is TurnOutcome.FAILED
        assert usage.consumed_pct == 3.5
        assert usage.coverage is Coverage.COMPLETE
        assert tuple(stored_identity) == (run.id, "02-implement-i1", Role.IMPLEMENTER.value)
        [stored_run] = RunStore(reopened, clock).for_task(task.id)
        assert stored_run.session_id == "session-1"
    finally:
        reopened.close()


def test_overlapping_tasks_on_one_provider_have_unavailable_shared_coverage(
    connection: Connection,
) -> None:
    clock = FakeClock(_START)
    first = seed_task(connection, clock, title="First task")
    second = seed_task(connection, clock, title="Second task")
    runs = RunStore(connection, clock)
    first_run = runs.start(first.id, Role.IMPLEMENTER, _RUNG)
    second_run = runs.start(second.id, Role.IMPLEMENTER, _RUNG)
    evidence_store = TaskQuotaStore(connection)
    first_turn = evidence_store.begin_turn(
        task_id=first.id,
        run_id=first_run.id,
        turn="first-turn",
        role=Role.IMPLEMENTER,
        agent="codex",
        started_at=_START,
    )
    evidence_store.record_sample(
        first_turn,
        QuotaWindow("5h", 20, _RESET, _START + timedelta(seconds=1), QuotaBoundary.START),
    )
    evidence_store.record_sample(
        first_turn,
        QuotaWindow("5h", 24, _RESET, _START + timedelta(seconds=4), QuotaBoundary.END),
    )
    evidence_store.end_turn(
        first_turn, ended_at=_START + timedelta(seconds=5), outcome=TurnOutcome.ENDED
    )
    second_turn = evidence_store.begin_turn(
        task_id=second.id,
        run_id=second_run.id,
        turn="second-turn",
        role=Role.IMPLEMENTER,
        agent="codex",
        started_at=_START + timedelta(seconds=2),
    )
    evidence_store.end_turn(
        second_turn,
        ended_at=_START + timedelta(seconds=3),
        outcome=TurnOutcome.ENDED,
    )

    [usage] = task_quota_consumption(fetch_task_quota_evidence(connection, first.id))

    assert usage.consumed_pct is None
    assert usage.coverage is Coverage.UNAVAILABLE
    assert CoverageGap.SHARED_ACCOUNT in usage.gaps


def test_legacy_sessions_and_resumed_runs_keep_missing_history_after_migration(
    database: Database, connection: Connection
) -> None:
    clock = FakeClock(_START)
    task = seed_task(connection, clock, title="Legacy quota history")
    runs = RunStore(connection, clock)
    old_run = runs.start(task.id, Role.IMPLEMENTER, _RUNG)
    runs.set_session(old_run.id, "old-session", "codex resume old-session")
    clock.advance(5)
    runs.close(old_run.id)
    clock.advance(5)
    resumed_run = runs.start(task.id, Role.IMPLEMENTER, _RUNG)
    runs.set_session(resumed_run.id, "resumed-session", "codex resume resumed-session")
    connection.execute("PRAGMA user_version = 10")

    reopened = database.connect()
    try:
        legacy_turns = reopened.execute(
            "SELECT run_id, outcome FROM task_quota_turns WHERE task_id = ? ORDER BY run_id",
            (task.id,),
        ).fetchall()
        assert [(row["run_id"], row["outcome"]) for row in legacy_turns] == [
            (old_run.id, TurnOutcome.LEGACY.value),
            (resumed_run.id, TurnOutcome.LEGACY.value),
        ]

        evidence_store = TaskQuotaStore(reopened)
        turn_id = evidence_store.begin_turn(
            task_id=task.id,
            run_id=resumed_run.id,
            turn="resumed-turn",
            role=Role.IMPLEMENTER,
            agent="codex",
            started_at=clock.now(),
        )
        evidence_store.record_sample(
            turn_id,
            QuotaWindow("5h", 20, _RESET, clock.now() + timedelta(seconds=1), QuotaBoundary.START),
        )
        evidence_store.record_sample(
            turn_id,
            QuotaWindow("5h", 23.5, _RESET, clock.now() + timedelta(seconds=2), QuotaBoundary.END),
        )
        evidence_store.end_turn(
            turn_id, ended_at=clock.now() + timedelta(seconds=3), outcome=TurnOutcome.ENDED
        )

        [usage] = task_quota_consumption(fetch_task_quota_evidence(reopened, task.id))

        assert usage.consumed_pct == 3.5
        assert usage.coverage is Coverage.PARTIAL
        assert CoverageGap.LEGACY_HISTORY in usage.gaps
    finally:
        reopened.close()


def test_run_without_a_quota_turn_row_keeps_new_intervals_partial(
    connection: Connection,
) -> None:
    clock = FakeClock(_START)
    task = seed_task(connection, clock, title="Run without quota ledger")
    runs = RunStore(connection, clock)
    legacy_run = runs.start(task.id, Role.IMPLEMENTER, _RUNG)
    runs.set_session(legacy_run.id, "legacy-session", "codex resume legacy-session")
    clock.advance(5)
    current_run = runs.start(task.id, Role.IMPLEMENTER, _RUNG)
    evidence_store = TaskQuotaStore(connection)
    turn_id = evidence_store.begin_turn(
        task_id=task.id,
        run_id=current_run.id,
        turn="current-turn",
        role=Role.IMPLEMENTER,
        agent="codex",
        started_at=clock.now(),
    )
    evidence_store.record_sample(
        turn_id,
        QuotaWindow("5h", 20, _RESET, clock.now() + timedelta(seconds=1), QuotaBoundary.START),
    )
    evidence_store.record_sample(
        turn_id,
        QuotaWindow("5h", 23.5, _RESET, clock.now() + timedelta(seconds=2), QuotaBoundary.END),
    )
    evidence_store.end_turn(
        turn_id, ended_at=clock.now() + timedelta(seconds=3), outcome=TurnOutcome.ENDED
    )

    [usage] = task_quota_consumption(fetch_task_quota_evidence(connection, task.id))

    assert usage.consumed_pct == 3.5
    assert usage.coverage is Coverage.PARTIAL
    assert CoverageGap.LEGACY_HISTORY in usage.gaps
