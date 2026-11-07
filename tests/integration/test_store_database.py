"""Integration tests for `Database` migrations."""

from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from importlib import resources
from typing import TYPE_CHECKING

import pytest

from shikiru.agents.contracts import BILLING_MODE_UNKNOWN, BILLING_SOURCE_UNKNOWN
from shikiru.errors import DatabaseError
from shikiru.store.database import Database, format_timestamp
from shikiru.store.tasks import TaskStore
from tests.support.clock import FakeClock

if TYPE_CHECKING:
    from pathlib import Path

START = datetime(2026, 1, 1, tzinfo=UTC)
_MIGRATION_RACE_ROUNDS = 20
_MIGRATION_RACERS = 4


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
        assert run["billing_mode"] == BILLING_MODE_UNKNOWN
        assert run["billing_source"] == BILLING_SOURCE_UNKNOWN
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


def _migration_scripts() -> list[tuple[str, str]]:
    return sorted(
        (entry.name, entry.read_text(encoding="utf-8"))
        for entry in resources.files("shikiru.store.migrations").iterdir()
        if entry.name.endswith(".sql")
    )


def _migration_number(name: str) -> int:
    return int(name.split("_", 1)[0])


def _database_one_migration_behind(path: Path) -> int:
    *earlier, (latest_name, _latest_sql) = _migration_scripts()
    legacy = sqlite3.connect(path)
    try:
        legacy.execute("PRAGMA journal_mode = WAL")
        for _name, sql in earlier:
            legacy.executescript(sql)
        legacy.execute(f"PRAGMA user_version = {_migration_number(earlier[-1][0])}")
        legacy.commit()
    finally:
        legacy.close()
    return _migration_number(latest_name)


def test_concurrent_opens_of_an_outdated_database_migrate_it_exactly_once(
    tmp_path: Path,
) -> None:
    for round_number in range(_MIGRATION_RACE_ROUNDS):
        path = tmp_path / f"state-{round_number}.db"
        latest = _database_one_migration_behind(path)
        start = threading.Barrier(_MIGRATION_RACERS)

        def open_after_barrier(path: Path = path, start: threading.Barrier = start) -> int:
            start.wait()
            connection = Database(path).connect()
            try:
                return connection.execute("PRAGMA user_version").fetchone()[0]
            finally:
                connection.close()

        with ThreadPoolExecutor(_MIGRATION_RACERS) as pool:
            versions = [
                future.result()
                for future in [pool.submit(open_after_barrier) for _ in range(_MIGRATION_RACERS)]
            ]

        assert versions == [latest] * _MIGRATION_RACERS


_TASK_QUOTA_DROP_MIGRATION = "012_drop_task_quota_evidence.sql"
_TASK_QUOTA_EVIDENCE_VERSION = 11
_LEGACY_TASK_QUOTA_EVIDENCE = """
CREATE TABLE task_quota_turns (
  id INTEGER PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
  run_id INTEGER NOT NULL REFERENCES runs(id), turn TEXT NOT NULL, role TEXT NOT NULL,
  agent TEXT NOT NULL, started_at TEXT NOT NULL, ended_at TEXT, outcome TEXT NOT NULL
);
CREATE INDEX idx_task_quota_turns_task_id ON task_quota_turns(task_id);
CREATE TABLE task_quota_samples (
  id INTEGER PRIMARY KEY, turn_id INTEGER NOT NULL REFERENCES task_quota_turns(id),
  window TEXT NOT NULL, used_pct REAL NOT NULL, resets_at TEXT, observed_at TEXT NOT NULL,
  boundary TEXT, time_source TEXT NOT NULL DEFAULT 'collected'
);
CREATE INDEX idx_task_quota_samples_turn_id ON task_quota_samples(turn_id);
"""


def _schema(connection: sqlite3.Connection) -> set[tuple[str, str, str]]:
    return {
        (kind, name, " ".join(sql.split()))
        for kind, name, sql in connection.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
        )
    }


def test_upgrade_drops_task_quota_evidence_and_matches_a_fresh_schema(tmp_path: Path) -> None:
    legacy_path = tmp_path / "legacy.db"
    legacy = sqlite3.connect(legacy_path)
    try:
        for name, sql in _migration_scripts():
            if name < _TASK_QUOTA_DROP_MIGRATION:
                legacy.executescript(sql)
        legacy.executescript(_LEGACY_TASK_QUOTA_EVIDENCE)
        legacy.execute(
            "INSERT INTO task_quota_turns "
            "(task_id, run_id, turn, role, agent, started_at, outcome) "
            "VALUES ('T-1', 1, '01-implement-i1', 'implementer', 'codex', ?, 'ended')",
            (format_timestamp(START),),
        )
        legacy.execute(
            "INSERT INTO task_quota_samples (turn_id, window, used_pct, observed_at) "
            "VALUES (1, '5h', 20, ?)",
            (format_timestamp(START),),
        )
        legacy.execute(f"PRAGMA user_version = {_TASK_QUOTA_EVIDENCE_VERSION}")
        legacy.commit()
    finally:
        legacy.close()

    migrated = Database(legacy_path).connect()
    fresh = Database(tmp_path / "fresh.db").connect()
    try:
        assert _schema(migrated) == _schema(fresh)
    finally:
        migrated.close()
        fresh.close()


KNOWN_SCHEMA_VERSION = _migration_number(_migration_scripts()[-1][0])


def _user_version(path: Path) -> int:
    connection = sqlite3.connect(path)
    try:
        return connection.execute("PRAGMA user_version").fetchone()[0]
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("stored", "refused"),
    [
        pytest.param(0, False, id="older"),
        pytest.param(KNOWN_SCHEMA_VERSION, False, id="equal"),
        pytest.param(KNOWN_SCHEMA_VERSION + 1, True, id="newer"),
    ],
)
def test_a_schema_newer_than_the_code_is_refused_without_writing(
    tmp_path: Path, stored: int, refused: bool
) -> None:
    path = tmp_path / "state.db"
    if stored == 0:
        sqlite3.connect(path).close()
    else:
        Database(path).connect().close()
        legacy = sqlite3.connect(path)
        try:
            legacy.execute(f"PRAGMA user_version = {stored}")
            legacy.commit()
        finally:
            legacy.close()
    database = Database(path)

    if not refused:
        database.connect().close()
        assert _user_version(path) == KNOWN_SCHEMA_VERSION
        return
    with pytest.raises(DatabaseError, match=f"version {stored}, newer than the version"):
        database.connect()
    with pytest.raises(DatabaseError, match=f"version {stored}, newer than the version"):
        database.connect_readonly()
    assert _user_version(path) == stored
