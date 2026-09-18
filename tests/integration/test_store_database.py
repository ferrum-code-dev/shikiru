"""Integration tests for `Database` migrations (docs/design/05-data.md §2)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from shikiru.store.database import format_timestamp

if TYPE_CHECKING:
    from shikiru.store.database import Database

START = datetime(2026, 1, 1, tzinfo=UTC)


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
            with pytest.raises(sqlite3.OperationalError):
                reader_connection.execute("INSERT INTO sequences (name, value) VALUES ('task', 1)")
        finally:
            reader_connection.close()
    finally:
        writer.close()


def test_foreign_keys_are_enforced_on_task_deps_and_runs(database: Database) -> None:
    writer = database.connect()
    try:
        writer.execute(
            "INSERT INTO tasks (id, seq, origin, title, request, status, created_at) "
            "VALUES ('T-1', 1, 'operator', 't', 't', 'queued', ?)",
            (format_timestamp(START),),
        )

        with pytest.raises(sqlite3.IntegrityError):
            writer.execute(
                "INSERT INTO task_deps (task_id, depends_on) VALUES ('T-1', 'T-missing')"
            )

        with pytest.raises(sqlite3.IntegrityError):
            writer.execute(
                "INSERT INTO runs (id, task_id, role, agent, model, status, started_at) "
                "VALUES (1, 'T-missing', 'implementer', 'codex', 'sol', 'live', ?)",
                (format_timestamp(START),),
            )
    finally:
        writer.close()
