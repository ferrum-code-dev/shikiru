"""Integration tests for `QuotaStore.agent_blocks` (docs/design/05-data.md §2; 03 §7, §9)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from shikiru.agents.contracts import QuotaWindow
from shikiru.store.quotas import QuotaStore

if TYPE_CHECKING:
    from tests.support.store import Connection

START = datetime(2026, 1, 1, tzinfo=UTC)


def _make_store(connection: Connection) -> QuotaStore:
    return QuotaStore(connection)


def test_active_blocks_excludes_expired_blocks_and_other_agents(connection: Connection) -> None:
    store = _make_store(connection)
    store.block("claude", START + timedelta(minutes=10), "quota")
    store.block("codex", START - timedelta(minutes=1), "quota")

    active = store.active_blocks(START)

    assert [block.agent for block in active] == ["claude"]


def test_clear_block_removes_it(connection: Connection) -> None:
    store = _make_store(connection)
    store.block("claude", START + timedelta(minutes=10), "quota")

    store.clear_block("claude")

    assert store.active_blocks(START) == []


def test_block_extends_an_earlier_block_for_the_same_agent(connection: Connection) -> None:
    store = _make_store(connection)
    store.block("claude", START + timedelta(minutes=5), "first")

    store.block("claude", START + timedelta(minutes=30), "second")

    [active] = store.active_blocks(START)
    assert active.reason == "second"
    assert active.until == START + timedelta(minutes=30)


def test_block_does_not_shorten_an_existing_cross_task_block(connection: Connection) -> None:
    store = _make_store(connection)
    deadline = START + timedelta(minutes=30)
    store.block("claude", deadline, "first")

    store.block("claude", START + timedelta(minutes=5), "second")

    [active] = store.active_blocks(START)
    assert active.reason == "first"
    assert active.until == deadline


def test_active_blocks_orders_correctly_at_sub_second_precision(connection: Connection) -> None:
    store = _make_store(connection)
    until = START + timedelta(microseconds=500)
    store.block("claude", until, "quota")

    assert [block.agent for block in store.active_blocks(START)] == ["claude"]
    assert store.active_blocks(until) == []


def test_record_keeps_the_plugin_supplied_observed_at(connection: Connection) -> None:
    store = _make_store(connection)
    observed_at = START + timedelta(minutes=3)
    window = QuotaWindow(name="5h", used_pct=42.0, resets_at=None, observed_at=observed_at)

    store.record("claude", window)

    [reloaded] = store.quotas_for("claude")
    assert reloaded == window
