"""Integration tests for `Database` migrations."""

from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from importlib import resources
from typing import TYPE_CHECKING

import pytest

from shikiru.agents.contracts import BILLING_MODE_UNKNOWN, BILLING_SOURCE_UNKNOWN, Usage
from shikiru.domain.runs import starting_usage
from shikiru.errors import DatabaseError
from shikiru.store.database import SCHEMA_BACKUPS_KEPT, Database, format_timestamp
from shikiru.store.runs import RunStore, fetch_run
from shikiru.store.tasks import TaskStore
from tests.support.clock import FakeClock
from tests.support.schema import database_at_schema, migration_number, migration_scripts

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


def _database_one_migration_behind(path: Path) -> int:
    *earlier, (latest_name, _latest_sql) = migration_scripts()
    legacy = sqlite3.connect(path)
    try:
        legacy.execute("PRAGMA journal_mode = WAL")
        for _name, sql in earlier:
            legacy.executescript(sql)
        legacy.execute(f"PRAGMA user_version = {migration_number(earlier[-1][0])}")
        legacy.commit()
    finally:
        legacy.close()
    return migration_number(latest_name)


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
        for name, sql in migration_scripts():
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


KNOWN_SCHEMA_VERSION = migration_number(migration_scripts()[-1][0])


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


def _set_user_version(path: Path, version: int) -> None:
    legacy = sqlite3.connect(path)
    try:
        legacy.execute(f"PRAGMA user_version = {version}")
        legacy.commit()
    finally:
        legacy.close()


_OWNER_ONLY_DIR_MODE = 0o700
_READ_ONLY_DIR_MODE = 0o500


def test_migrating_an_outdated_database_backs_up_its_old_contents_first(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    path = project / "state.db"
    database_at_schema(path, 1)
    legacy = sqlite3.connect(path)
    try:
        legacy.execute(
            "INSERT INTO tasks (id, seq, origin, title, request, status, created_at) "
            "VALUES ('T-1', 1, 'operator', 'legacy', 'legacy', 'running', ?)",
            (format_timestamp(START),),
        )
        legacy.commit()
    finally:
        legacy.close()

    Database(path).connect().close()

    backup = project / "backups" / "state-schema1.db"
    backed_up = sqlite3.connect(backup)
    try:
        tasks = backed_up.execute("SELECT id FROM tasks").fetchall()
    finally:
        backed_up.close()
    assert tasks == [("T-1",)]
    assert _user_version(backup) == 1
    assert _user_version(path) == KNOWN_SCHEMA_VERSION


def test_the_fourth_backup_removes_the_oldest(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    for schema in range(1, SCHEMA_BACKUPS_KEPT + 2):
        path = project / f"state-{schema}.db"
        database_at_schema(path, schema)
        Database(path).connect().close()

    kept = {entry.name for entry in (project / "backups").iterdir()}

    assert kept == {f"state-schema{schema}.db" for schema in range(2, SCHEMA_BACKUPS_KEPT + 2)}


def test_a_fresh_database_is_not_backed_up(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()

    Database(project / "state.db").connect().close()

    assert not (project / "backups").exists()


def test_a_failed_backup_leaves_the_database_unmigrated(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    path = project / "state.db"
    database_at_schema(path, 1)
    backups = project / "backups"
    backups.mkdir()
    backups.chmod(_READ_ONLY_DIR_MODE)
    try:
        with pytest.raises(DatabaseError, match="could not back up state database"):
            Database(path).connect()
        assert _user_version(path) == 1
    finally:
        backups.chmod(_OWNER_ONLY_DIR_MODE)


def test_the_too_new_error_names_the_newest_backup(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    path = project / "state.db"
    database_at_schema(path, 1)
    Database(path).connect().close()
    backup = project / "backups" / "state-schema1.db"
    _set_user_version(path, KNOWN_SCHEMA_VERSION + 1)

    with pytest.raises(DatabaseError) as refused:
        Database(path).connect()

    message = str(refused.value)
    assert str(backup) in message
    assert "copy it back" in message
    assert _user_version(path) == KNOWN_SCHEMA_VERSION + 1


def _insert_legacy_run(
    path: Path, agent: str, reported: int | None, tokens: tuple[int, int, int, int]
) -> None:
    input_tokens, output_tokens, cache_read_tokens, cache_write_tokens = tokens
    legacy = sqlite3.connect(path)
    try:
        legacy.execute(
            "INSERT INTO tasks (id, seq, origin, title, request, status, step, repo, created_at) "
            "VALUES ('T-1', 1, 'operator', 'old', 'old', 'running', 'implement', ?, ?)",
            (
                '{"base": "main", "base_sha": "abc123", "branch": "shikiru/T-1-old"}',
                format_timestamp(START),
            ),
        )
        legacy.execute(
            "INSERT INTO runs (task_id, role, agent, model, status, input_tokens, output_tokens, "
            "cache_read_tokens, cache_write_tokens, cost_usd, cost_basis, started_at, "
            "usage_reported) "
            "VALUES ('T-1', 'implementer', ?, 'sol', 'closed', ?, ?, ?, ?, 0.5, 'billed', ?, ?)",
            (
                agent,
                input_tokens,
                output_tokens,
                cache_read_tokens,
                cache_write_tokens,
                format_timestamp(START),
                reported,
            ),
        )
        legacy.commit()
    finally:
        legacy.close()


def _token_categories(path: Path) -> tuple[object, ...]:
    connection = Database(path).connect()
    try:
        return tuple(
            connection.execute(
                "SELECT uncached_input_tokens, output_tokens, cache_read_tokens, "
                "cache_write_tokens, usage_accounting FROM runs"
            ).fetchone()
        )
    finally:
        connection.close()


def _legacy_usage(path: Path, run_id: int = 1) -> tuple[object, ...]:
    connection = Database(path).connect()
    try:
        return tuple(
            connection.execute(
                "SELECT source, usage_reported, input_tokens, uncached_input_tokens, "
                "output_tokens, cache_read_tokens, cache_write_tokens FROM legacy_run_usage "
                "WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        )
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("agent", "reported", "tokens", "expected"),
    [
        pytest.param(
            "claude",
            1,
            (100, 5, 40, 10),
            (None, None, None, None),
            id="claude-legacy-subtotal-stays-unknown",
        ),
        pytest.param(
            "claude",
            1,
            (100, 0, 0, 0),
            (None, None, None, None),
            id="claude-stored-zero-categories-stay-unknown",
        ),
        pytest.param(
            "codex",
            1,
            (100, 5, 40, 0),
            (None, None, None, None),
            id="codex-uncached-input-and-zero-writes-stay-unknown",
        ),
        pytest.param(
            "codex",
            1,
            (100, 5, 40, 10),
            (None, None, None, None),
            id="codex-cache-writes-leave-uncached-input-unknown",
        ),
        pytest.param(
            "muse",
            1,
            (100, 5, 40, 0),
            (None, None, None, None),
            id="muse-legacy-counters-stay-unknown",
        ),
        pytest.param(
            "codex",
            0,
            (100, 5, 40, 0),
            (None, None, None, None),
            id="never-reported-usage-stays-unknown",
        ),
        pytest.param(
            "codex",
            None,
            (0, 0, 0, 0),
            (None, None, None, None),
            id="unrecorded-zero-tokens-stay-unknown",
        ),
    ],
)
def test_migrating_legacy_runs_converts_each_provider_once(
    tmp_path: Path,
    agent: str,
    reported: int | None,
    tokens: tuple[int, int, int, int],
    expected: tuple[int | None, int | None, int | None, int | None],
) -> None:
    path = tmp_path / "state.db"
    database_at_schema(path, migration_number("008_reported_usage.sql"))
    _insert_legacy_run(path, agent, reported, tokens)

    assert _token_categories(path) == (*expected, "legacy")
    assert _legacy_usage(path)[1:] == (reported, tokens[0], None, *tokens[1:])


def test_reopening_a_migrated_database_does_not_convert_its_usage_again(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    database_at_schema(path, migration_number("008_reported_usage.sql"))
    _insert_legacy_run(path, "codex", 1, (100, 5, 40, 0))

    first = _token_categories(path)
    second = _token_categories(path)

    assert first == second == (None, None, None, None, "legacy")
    first_legacy = _legacy_usage(path)
    second_legacy = _legacy_usage(path)
    assert (
        first_legacy
        == second_legacy
        == (
            "pre_category_columns",
            1,
            100,
            None,
            5,
            40,
            0,
        )
    )


def test_migration_preserves_ambiguous_counters_and_clears_normalized_totals(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    database_at_schema(path, migration_number("018_token_categories.sql"))
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "INSERT INTO tasks (id, seq, origin, title, request, status, created_at) "
            "VALUES ('T-1', 1, 'operator', 'old', 'old', 'running', ?)",
            (format_timestamp(START),),
        )
        for run_id, agent, accounting in (
            (1, "claude", "legacy"),
            (2, "muse", "categories"),
        ):
            connection.execute(
                "INSERT INTO runs (id, task_id, role, agent, model, status, "
                "uncached_input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, "
                "cost_usd, cost_basis, context_used_pct, started_at, usage_reported, "
                "usage_accounting) "
                "VALUES (?, 'T-1', 'implementer', ?, 'model', 'closed', 100, 5, 40, 10, "
                "0.5, 'billed', 30, ?, 1, ?)",
                (run_id, agent, format_timestamp(START), accounting),
            )
        connection.commit()
    finally:
        connection.close()

    connection = Database(path).connect()
    try:
        rows = connection.execute(
            "SELECT agent, uncached_input_tokens, output_tokens, cache_read_tokens, "
            "cache_write_tokens, usage_accounting, cost_usd, cost_basis, context_used_pct, "
            "usage_reported FROM runs ORDER BY id"
        ).fetchall()
        legacy_rows = connection.execute(
            "SELECT source, usage_reported, input_tokens, uncached_input_tokens, output_tokens, "
            "cache_read_tokens, cache_write_tokens FROM legacy_run_usage ORDER BY run_id"
        ).fetchall()
    finally:
        connection.close()

    assert [tuple(row) for row in rows] == [
        ("claude", None, None, None, None, "legacy", 0.5, "billed", 30.0, 1),
        ("muse", None, None, None, None, "legacy", 0.5, "billed", 30.0, 1),
    ]
    assert [tuple(row) for row in legacy_rows] == [
        ("previous_category_fields", 1, None, 100, 5, 40, 10),
        ("previous_category_fields", 1, None, 100, 5, 40, 10),
    ]


def test_migration_from_previous_schema_archives_codex_partial_values_once(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    database_at_schema(path, migration_number("018_token_categories.sql"))
    legacy = sqlite3.connect(path)
    try:
        legacy.execute(
            "INSERT INTO tasks (id, seq, origin, title, request, status, created_at) "
            "VALUES ('T-1', 1, 'operator', 'old', 'old', 'running', ?)",
            (format_timestamp(START),),
        )
        legacy.execute(
            "INSERT INTO runs (id, task_id, role, agent, model, status, output_tokens, "
            "cache_read_tokens, cache_write_tokens, started_at, usage_reported, usage_accounting) "
            "VALUES (1, 'T-1', 'implementer', 'codex', 'model', 'closed', 70, "
            "40, 10, ?, 1, 'legacy')",
            (format_timestamp(START),),
        )
        legacy.execute(
            "INSERT INTO runs (id, task_id, role, agent, model, status, started_at, "
            "usage_reported, usage_accounting) VALUES (2, 'T-1', 'leader', 'claude', 'model', "
            "'closed', ?, 1, 'legacy')",
            (format_timestamp(START),),
        )
        legacy.execute("DROP TABLE legacy_run_usage")
        legacy.execute(
            f"PRAGMA user_version = {migration_number('019_unverified_legacy_usage.sql')}"
        )
        legacy.commit()
    finally:
        legacy.close()

    assert _token_categories(path) == (None, None, None, None, "legacy")
    first = _legacy_usage(path)
    assert first == (
        "previous_category_fields",
        1,
        None,
        None,
        70,
        40,
        10,
    )
    assert _legacy_usage(path) == first
    assert _legacy_usage(path, 2) == (
        "previously_cleared_fields",
        1,
        None,
        None,
        None,
        None,
        None,
    )


def test_resuming_a_legacy_run_keeps_its_archive_and_starts_unknown(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    database_at_schema(path, migration_number("008_reported_usage.sql"))
    _insert_legacy_run(path, "codex", 1, (100, 5, 40, 10))
    legacy = sqlite3.connect(path)
    try:
        legacy.execute("UPDATE runs SET session_id = 'codex-session' WHERE id = 1")
        legacy.commit()
    finally:
        legacy.close()

    connection = Database(path).connect()
    try:
        run = fetch_run(connection, 1)
        assert run is not None
        assert starting_usage(run) == Usage(None, None, None, None, 0.5, "billed", None)

        updated = Usage(300, 20, 50, 0, None, "unknown", 30)
        RunStore(connection, FakeClock(START)).record_usage(run.id, updated)
        resumed = fetch_run(connection, run.id)
        assert resumed is not None
        assert resumed.usage == updated
        assert resumed.usage_accounting.value == "categories"
        assert resumed.legacy_usage is not None
        assert resumed.legacy_usage.input_tokens == 100
        assert resumed.legacy_usage.output_tokens == 5
    finally:
        connection.close()
