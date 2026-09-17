"""Integration tests for `QuotaStore.agent_blocks` (docs/design/05-data.md §2; 03 §7, §9)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from shikiru.store.database import Database
from shikiru.store.quotas import QuotaStore
from tests.support.clock import FakeClock

if TYPE_CHECKING:
    from pathlib import Path

START = datetime(2026, 1, 1, tzinfo=UTC)


def _make_store(tmp_path: Path) -> QuotaStore:
    connection = Database(tmp_path / "state.db").open_writer()
    return QuotaStore(connection, FakeClock(START))


def test_active_blocks_excludes_expired_blocks_and_other_agents(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.block("claude", START + timedelta(minutes=10), "quota")
    store.block("codex", START - timedelta(minutes=1), "quota")

    active = store.active_blocks(START)

    assert [block.agent for block in active] == ["claude"]


def test_clear_block_removes_it(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.block("claude", START + timedelta(minutes=10), "quota")

    store.clear_block("claude")

    assert store.active_blocks(START) == []


def test_block_replaces_an_earlier_block_for_the_same_agent(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.block("claude", START + timedelta(minutes=5), "first")

    store.block("claude", START + timedelta(minutes=30), "second")

    [active] = store.active_blocks(START)
    assert active.reason == "second"
    assert active.until == START + timedelta(minutes=30)
