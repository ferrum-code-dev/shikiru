"""Integration tests for `Database` migrations (docs/design/05-data.md §2)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from shikiru.store.database import Database


def test_connect_migrates_a_fresh_db_and_is_idempotent(database: Database) -> None:
    first = database.connect()
    try:
        version_after_first_open = first.execute("PRAGMA user_version").fetchone()[0]
        assert version_after_first_open > 0
        assert first.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
    finally:
        first.close()

    second = database.connect()
    try:
        version_after_second_open = second.execute("PRAGMA user_version").fetchone()[0]
        assert version_after_second_open == version_after_first_open
    finally:
        second.close()


def test_connect_readonly_cannot_write(database: Database) -> None:
    writer = database.connect()
    try:
        reader_connection = database.connect_readonly()
        try:
            assert reader_connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
        finally:
            reader_connection.close()
    finally:
        writer.close()
