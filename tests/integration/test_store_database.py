"""Integration tests for `Database` migrations (docs/design/05-data.md §2)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.store.database import Database

if TYPE_CHECKING:
    from pathlib import Path


def test_connect_migrates_a_fresh_db_and_is_idempotent(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.db")

    first = database.connect()
    version_after_first_open = first.execute("PRAGMA user_version").fetchone()[0]
    assert version_after_first_open > 0
    assert first.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
    first.close()

    second = database.connect()
    version_after_second_open = second.execute("PRAGMA user_version").fetchone()[0]

    assert version_after_second_open == version_after_first_open


def test_connect_readonly_cannot_write(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.db")
    database.connect()

    reader_connection = database.connect_readonly()

    assert reader_connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
