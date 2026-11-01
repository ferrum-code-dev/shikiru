"""Integration tests for `QuotaStore.agent_blocks`."""

from __future__ import annotations

import sqlite3  # noqa: TID251
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from shikiru.agents.contracts import QuotaWindow
from shikiru.store.database import Database, format_timestamp, transaction
from shikiru.store.quotas import QuotaStore, fetch_quotas
from tests.support.clock import LONG_AGO

if TYPE_CHECKING:
    from pathlib import Path

    from tests.support.store import Connection

START = datetime(2026, 1, 1, tzinfo=UTC)


def _make_store(connection: Connection) -> QuotaStore:
    return QuotaStore(connection)


def test_active_blocks_excludes_expired_blocks_and_other_agents(connection: Connection) -> None:
    store = _make_store(connection)
    store.block("claude", START + timedelta(minutes=10), "quota", LONG_AGO)
    store.block("codex", START - timedelta(minutes=1), "quota", LONG_AGO)

    active = store.active_blocks(START)

    assert [block.agent for block in active] == ["claude"]


def test_clear_block_removes_it(connection: Connection) -> None:
    store = _make_store(connection)
    store.block("claude", START + timedelta(minutes=10), "quota", LONG_AGO)

    store.clear_block("claude")

    assert store.active_blocks(START) == []


def test_block_extends_an_earlier_block_for_the_same_agent(connection: Connection) -> None:
    store = _make_store(connection)
    store.block("claude", START + timedelta(minutes=5), "first", LONG_AGO)

    store.block("claude", START + timedelta(minutes=30), "second", LONG_AGO)

    [active] = store.active_blocks(START)
    assert active.reason == "second"
    assert active.until == START + timedelta(minutes=30)


def test_block_does_not_shorten_an_existing_cross_task_block(connection: Connection) -> None:
    store = _make_store(connection)
    deadline = START + timedelta(minutes=30)
    store.block("claude", deadline, "first", LONG_AGO)

    store.block("claude", START + timedelta(minutes=5), "second", LONG_AGO)

    [active] = store.active_blocks(START)
    assert active.reason == "first"
    assert active.until == deadline


def test_active_blocks_orders_correctly_at_sub_second_precision(connection: Connection) -> None:
    store = _make_store(connection)
    until = START + timedelta(microseconds=500)
    store.block("claude", until, "quota", LONG_AGO)

    assert [block.agent for block in store.active_blocks(START)] == ["claude"]
    assert store.active_blocks(until) == []


def test_record_keeps_the_plugin_supplied_observed_at(connection: Connection) -> None:
    store = _make_store(connection)
    observed_at = START + timedelta(minutes=3)
    window = QuotaWindow(name="5h", used_pct=42.0, resets_at=None, observed_at=observed_at)

    store.record("claude", window)

    [reloaded] = fetch_quotas(connection, "claude")
    assert reloaded == window


_PRE_CANONICAL_SCHEMA_VERSION = 9


def _insert_legacy(connection: Connection, agent: str, window: str, used_pct: float) -> None:
    connection.execute(
        "INSERT INTO quotas (agent, window, used_pct, resets_at, observed_at) "
        "VALUES (?, ?, ?, NULL, ?)",
        (agent, window, used_pct, START.isoformat()),
    )


def test_legacy_builtin_window_aliases_are_dropped_and_never_shown_beside_canonical_ones(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "state.db")
    connection = database.connect()
    _insert_legacy(connection, "claude", "five_hour", 36.0)
    _insert_legacy(connection, "codex", "primary", 20.0)
    _insert_legacy(connection, "other", "primary", 7.0)
    connection.execute(f"PRAGMA user_version = {_PRE_CANONICAL_SCHEMA_VERSION}")
    connection.commit()
    connection.close()

    connection = database.connect()
    try:
        assert fetch_quotas(connection, "claude") == []
        assert fetch_quotas(connection, "codex") == []
        assert [w.name for w in fetch_quotas(connection, "other")] == ["primary"]

        QuotaStore(connection).record("claude", QuotaWindow("5h", 36.0, None, START))

        assert [w.name for w in fetch_quotas(connection, "claude")] == ["5h"]
    finally:
        connection.close()


def _deny_writes_to(connection: Connection, table: str) -> None:
    def authorizer(action: int, name: str | None, *_unused: str | None) -> int:
        denied = action in {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_DELETE} and name == table
        return sqlite3.SQLITE_DENY if denied else sqlite3.SQLITE_OK

    connection.set_authorizer(authorizer)


def test_a_failed_block_write_leaves_no_half_persisted_block(connection: Connection) -> None:
    store = _make_store(connection)
    _deny_writes_to(connection, "agent_block_times")

    with pytest.raises(sqlite3.DatabaseError):
        store.block("claude", START + timedelta(minutes=10), "quota", START)
    connection.set_authorizer(None)

    assert connection.execute("SELECT COUNT(*) FROM agent_blocks").fetchone()[0] == 0
    assert store.block_for("claude") is None


def test_a_failed_block_update_keeps_the_previous_block_intact(connection: Connection) -> None:
    store = _make_store(connection)
    store.block("claude", START + timedelta(minutes=10), "first", START)
    _deny_writes_to(connection, "agent_block_times")

    with pytest.raises(sqlite3.DatabaseError):
        store.block("claude", START + timedelta(minutes=30), "second", START + timedelta(minutes=1))
    connection.set_authorizer(None)

    [active] = store.active_blocks(START)
    assert (active.reason, active.until, active.blocked_at) == (
        "first",
        START + timedelta(minutes=10),
        START,
    )


def test_a_failed_clear_keeps_the_block_visible(connection: Connection) -> None:
    store = _make_store(connection)
    store.block("claude", START + timedelta(minutes=10), "quota", START)
    _deny_writes_to(connection, "agent_block_times")

    with pytest.raises(sqlite3.DatabaseError):
        store.clear_block("claude")
    connection.set_authorizer(None)

    assert [block.agent for block in store.active_blocks(START)] == ["claude"]


def test_a_block_written_inside_a_larger_transaction_rolls_back_with_it(
    connection: Connection,
) -> None:
    store = _make_store(connection)

    with suppress(RuntimeError), transaction(connection):
        store.block("claude", START + timedelta(minutes=10), "quota", START)
        message = "abort"
        raise RuntimeError(message)

    assert store.block_for("claude") is None


def test_a_block_survives_reopening_the_database(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.db")
    first = database.connect()
    _make_store(first).block("claude", START + timedelta(minutes=10), "quota", START)
    first.close()

    reopened = database.connect()
    try:
        [active] = _make_store(reopened).active_blocks(START)
        assert (active.agent, active.blocked_at) == ("claude", START)
    finally:
        reopened.close()


_ORPHAN_REPAIR_BEFORE_VERSION = 14


def test_upgrade_repairs_block_rows_left_half_written_by_the_previous_schema(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "state.db")
    connection = database.connect()
    until = START + timedelta(hours=1)
    connection.execute(
        "INSERT INTO agent_blocks (agent, until, reason) VALUES ('claude', ?, 'quota')",
        (format_timestamp(until),),
    )
    connection.execute(
        "INSERT INTO agent_block_times (agent, blocked_at) VALUES ('codex', ?)",
        (format_timestamp(START),),
    )
    connection.execute(f"PRAGMA user_version = {_ORPHAN_REPAIR_BEFORE_VERSION}")
    connection.close()

    connection = database.connect()
    try:
        store = _make_store(connection)
        [active] = store.active_blocks(START)
        assert active.agent == "claude"
        assert active.blocked_at > START
        assert store.block_for("codex") is None
        store.block("codex", until, "quota", START)
        assert store.block_for("codex") is not None
    finally:
        connection.close()
