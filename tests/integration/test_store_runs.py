"""Integration tests for `RunStore`."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from shikiru.agents.contracts import (
    BILLING_MODE_UNKNOWN,
    BILLING_SOURCE_UNKNOWN,
    Rung,
    Usage,
)
from shikiru.domain.runs import RunStatus
from shikiru.domain.tasks import Role
from shikiru.store.runs import RunStore, fetch_runs_for_task
from tests.support.clock import FakeClock
from tests.support.store import seed_task

if TYPE_CHECKING:
    from shikiru.store.database import Database
    from tests.support.store import Connection

START = datetime(2026, 1, 1, tzinfo=UTC)
RUNG = Rung(agent="codex", model="sol", effort="high")


def _make_store(connection: Connection) -> tuple[RunStore, str]:
    clock = FakeClock(START)
    task = seed_task(connection, clock)
    return RunStore(connection, clock), task.id


def test_get_live_returns_none_until_a_run_is_started(connection: Connection) -> None:
    store, task_id = _make_store(connection)

    assert store.get_live(task_id, Role.IMPLEMENTER) is None

    run = store.start(task_id, Role.IMPLEMENTER, RUNG)

    assert store.get_live(task_id, Role.IMPLEMENTER) == run


def test_a_fresh_run_reports_unknown_cost_basis_before_any_usage_is_recorded(
    connection: Connection,
) -> None:
    store, task_id = _make_store(connection)

    run = store.start(task_id, Role.IMPLEMENTER, RUNG)

    assert run.usage.cost_basis == "unknown"
    assert run.usage_reported is False
    assert run.billing_mode == BILLING_MODE_UNKNOWN
    assert run.billing_source == BILLING_SOURCE_UNKNOWN


def test_close_removes_a_run_from_get_live_and_marks_it_closed(connection: Connection) -> None:
    store, task_id = _make_store(connection)
    run = store.start(task_id, Role.IMPLEMENTER, RUNG)

    store.close(run.id)

    assert store.get_live(task_id, Role.IMPLEMENTER) is None
    [only_run] = store.for_task(task_id)
    assert only_run.status is RunStatus.CLOSED
    assert only_run.ended_at is not None


def test_set_session_accepts_a_session_that_cannot_be_resumed(connection: Connection) -> None:
    store, task_id = _make_store(connection)
    run = store.start(task_id, Role.IMPLEMENTER, RUNG)

    store.set_session(run.id, None, None)

    reloaded = store.get_live(task_id, Role.IMPLEMENTER)
    assert reloaded is not None
    assert reloaded.session_id is None
    assert reloaded.resume_command is None


def test_session_message_and_usage_round_trip(connection: Connection) -> None:
    store, task_id = _make_store(connection)
    run = store.start(task_id, Role.IMPLEMENTER, RUNG)

    store.set_session(run.id, "session-1", "codex resume session-1")
    store.set_last_message(run.id, "Working on it")
    store.record_usage(
        run.id,
        Usage(
            input_tokens=100,
            output_tokens=50,
            cache_read_tokens=0,
            cache_write_tokens=0,
            cost_usd=0.42,
            cost_basis="billed",
            context_used_pct=None,
        ),
    )

    reloaded = store.get_live(task_id, Role.IMPLEMENTER)
    assert reloaded is not None
    assert reloaded.session_id == "session-1"
    assert reloaded.resume_command == "codex resume session-1"
    assert reloaded.last_message == "Working on it"
    assert reloaded.usage.input_tokens == 100
    assert reloaded.usage.output_tokens == 50
    assert reloaded.usage.cost_usd == 0.42
    assert reloaded.usage_reported is True


def test_migrating_legacy_runs_preserves_unknown_usage_provenance(database: Database) -> None:
    connection = database.connect()
    clock = FakeClock(START)
    task = seed_task(connection, clock)
    store = RunStore(connection, clock)
    run = store.start(task.id, Role.IMPLEMENTER, RUNG)
    store.record_usage(run.id, Usage(0, 0, 0, 0, 0.0, "billed", None))
    connection.execute("ALTER TABLE runs DROP COLUMN usage_reported")
    connection.execute("ALTER TABLE runs DROP COLUMN billing_mode")
    connection.execute("ALTER TABLE runs DROP COLUMN billing_source")
    connection.execute("PRAGMA user_version = 7")
    connection.close()

    migrated = database.connect()
    try:
        [legacy_run] = fetch_runs_for_task(migrated, task.id)
        assert legacy_run.usage_reported is None
        assert legacy_run.usage.input_tokens == 0
        assert legacy_run.usage.cost_usd == 0.0
    finally:
        migrated.close()
