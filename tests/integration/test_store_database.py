"""Integration tests for `Database` migrations (docs/design/05-data.md §2)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from importlib import resources
from typing import TYPE_CHECKING

import pytest

from shikiru.store.database import Database, format_timestamp
from shikiru.store.tasks import TaskStore
from tests.support.clock import FakeClock

if TYPE_CHECKING:
    from pathlib import Path

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


def test_command_migration_removes_unused_timestamp_and_preserves_commands(
    tmp_path: Path,
) -> None:
    path = tmp_path / "commands.db"
    legacy = sqlite3.connect(path)
    try:
        initial = resources.files("shikiru.store.migrations").joinpath("001_initial.sql")
        legacy.executescript(initial.read_text(encoding="utf-8"))
        legacy.execute(
            "INSERT INTO commands (ts, kind, payload, status, result, handled_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (format_timestamp(START), "stop", "{}", "done", "null", format_timestamp(START)),
        )
        legacy.execute("PRAGMA user_version = 2")
        legacy.commit()
    finally:
        legacy.close()

    migrated = Database(path).connect()
    try:
        columns = {row[1] for row in migrated.execute("PRAGMA table_info(commands)")}
        [command] = migrated.execute("SELECT id, kind, payload, status, result FROM commands")

        assert "handled_at" not in columns
        assert tuple(command) == (1, "stop", "{}", "done", "null")
    finally:
        migrated.close()


def test_headless_migration_keeps_sessions_and_removes_agent_state(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy.db"
    legacy = sqlite3.connect(path)
    try:
        initial = resources.files("shikiru.store.migrations").joinpath("001_initial.sql")
        legacy.executescript(initial.read_text(encoding="utf-8"))
        legacy.execute(
            "INSERT INTO tasks (id, seq, origin, title, request, status, created_at) "
            "VALUES ('T-1', 1, 'operator', 'legacy', 'legacy', 'running', ?)",
            (format_timestamp(START),),
        )
        legacy.execute(
            "INSERT INTO runs (task_id, role, agent, model, session_id, resume_command, "
            "agent_state, status, cost_basis, started_at) "
            "VALUES ('T-1', 'implementer', 'codex', 'sol', 'session-1', 'codex resume session-1', "
            "'{\"turns\":2}', 'exited', NULL, ?)",
            (format_timestamp(START),),
        )
        legacy.execute("PRAGMA user_version = 1")
        legacy.commit()
    finally:
        legacy.close()

    migrated = Database(path).connect()
    try:
        columns = {row[1] for row in migrated.execute("PRAGMA table_info(runs)")}
        run = migrated.execute("SELECT * FROM runs WHERE id = 1").fetchone()

        assert "agent_state" not in columns
        assert run["session_id"] == "session-1"
        assert run["resume_command"] == "codex resume session-1"
        assert run["status"] == "closed"
        assert run["cost_basis"] == "unknown"
        with pytest.raises(sqlite3.IntegrityError):
            migrated.execute("UPDATE runs SET cost_basis = NULL WHERE id = 1")
    finally:
        migrated.close()


def test_tasks_stored_before_base_ref_existed_load_with_their_base_as_the_ref(
    tmp_path: Path,
) -> None:
    path = tmp_path / "before-base-ref.db"
    legacy = sqlite3.connect(path)
    try:
        for name in (
            "001_initial.sql",
            "002_headless_turns.sql",
            "003_drop_command_handled_at.sql",
        ):
            legacy.executescript(
                resources.files("shikiru.store.migrations").joinpath(name).read_text("utf-8")
            )
        legacy.execute(
            "INSERT INTO tasks (id, seq, origin, title, request, status, step, repo, created_at) "
            "VALUES ('T-1', 1, 'operator', 'old', 'old', 'running', 'implement', ?, ?)",
            (
                '{"base": "main", "base_sha": "abc123", "branch": "shikiru/T-1-old"}',
                format_timestamp(START),
            ),
        )
        legacy.execute("PRAGMA user_version = 3")
        legacy.commit()
    finally:
        legacy.close()

    connection = Database(path).connect()
    try:
        [task] = TaskStore(connection, FakeClock(START)).list_active()
    finally:
        connection.close()

    assert task.repo is not None
    assert (task.repo.base, task.repo.base_ref, task.repo.base_sha) == ("main", "main", "abc123")


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


@pytest.mark.parametrize(
    "directory",
    ["plain", "with space", "hash#name", "query?mode=memory", "percent%41", "slash%2Fname", "%"],
)
def test_database_opens_exactly_the_requested_file(tmp_path: Path, directory: str) -> None:
    project = tmp_path / directory
    project.mkdir()
    requested = project / "state.db"
    database = Database(requested)

    for open_connection in (database.connect, database.connect_readonly):
        connection = open_connection()
        try:
            opened = connection.execute("PRAGMA database_list").fetchone()["file"]
        finally:
            connection.close()
        assert requested.samefile(opened)

    assert [entry.name for entry in tmp_path.iterdir()] == [directory]
    assert {entry.name for entry in project.iterdir()} <= {
        "state.db",
        "state.db-wal",
        "state.db-shm",
    }
