"""Integration tests for `QuotaStore.agent_blocks`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from shikiru.agents.contracts import QuotaWindow
from shikiru.store.database import Database
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
