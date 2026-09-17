"""Connections, migrations and JSON/timestamp column helpers (docs/design/05-data.md §2)."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib import resources
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

    from pydantic import TypeAdapter

BUSY_TIMEOUT_MS = 5000
_MIGRATIONS_PACKAGE = "shikiru.store.migrations"
_MIGRATION_SUFFIX = ".sql"
_MIGRATION_NUMBER_SEPARATOR = "_"


class Database:
    """Opens `.shikiru/state.db`, keeping its schema migrated to the packaged latest version."""

    def __init__(self, path: Path) -> None:
        """Point at the SQLite file `path`; no connection is opened until asked for one."""
        self._path = path

    def connect(self) -> sqlite3.Connection:
        """Open a read-write connection and migrate the schema up to date.

        The engine uses this for every state table; clients (CLI, MCP) use it only to append
        `commands` rows and, for `Submit`, to allocate the task id from `sequences` (01 §3).
        """
        connection = self._connect(mode="rwc")
        connection.execute("PRAGMA journal_mode = WAL")
        _migrate(connection)
        return connection

    def connect_readonly(self) -> sqlite3.Connection:
        """Open a connection SQLite itself refuses to let write."""
        return self._connect(mode="ro")

    def _connect(self, *, mode: str) -> sqlite3.Connection:
        connection = sqlite3.connect(
            f"file:{self._path}?mode={mode}",
            uri=True,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


@contextmanager
def transaction(connection: sqlite3.Connection) -> Generator[sqlite3.Connection]:
    """Run a block as one transaction: commits on success, rolls back on any exception.

    Composes with an already-open transaction: if `connection` is mid-transaction, this joins
    it (a plain `yield`, with no `BEGIN`/`COMMIT`/`ROLLBACK` of its own) so callers such as
    `TaskStore.insert_child` and `TaskStore.repoint_dependents` can be combined by an outer
    `transaction()` into one atomic unit, instead of failing on a nested `BEGIN`.
    """
    if connection.in_transaction:
        yield connection
        return
    connection.execute("BEGIN")
    try:
        yield connection
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    else:
        connection.execute("COMMIT")


def next_sequence(connection: sqlite3.Connection, name: str) -> int:
    """Atomically increment and return `sequences.value` for `name`, inside a transaction."""
    row = connection.execute(
        "INSERT INTO sequences (name, value) VALUES (?, 1) "
        "ON CONFLICT (name) DO UPDATE SET value = value + 1 RETURNING value",
        (name,),
    ).fetchone()
    return row[0]


def dump_json[T](adapter: TypeAdapter[T], value: T) -> str:
    """Serialise `value` to JSON text via `adapter`, for a JSON-typed column."""
    return adapter.dump_json(value).decode()


def load_json[T](adapter: TypeAdapter[T], text: str) -> T:
    """Parse `text` into `adapter`'s type, for a JSON-typed column."""
    return adapter.validate_json(text)


def format_timestamp(value: datetime) -> str:
    """Format `value` as the fixed-width UTC ISO-8601 text every timestamp column stores (05 §2).

    `timespec="microseconds"` always renders the fractional part, so every stored timestamp has
    the same width and plain string comparison (as `agent_blocks.until > ?` relies on) orders
    them the same way their instants do; without it, a whole-second value's missing ".ffffff"
    only sorts correctly by accident.
    """
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def parse_timestamp(text: str | None) -> datetime | None:
    """Parse a nullable timestamp column back into a UTC-aware `datetime`."""
    return datetime.fromisoformat(text) if text is not None else None


def parse_required_timestamp(text: str) -> datetime:
    """Parse a `NOT NULL` timestamp column back into a UTC-aware `datetime`."""
    return datetime.fromisoformat(text)


def _migration_files() -> list[tuple[int, str]]:
    package = resources.files(_MIGRATIONS_PACKAGE)
    migrations: list[tuple[int, str]] = []
    for entry in package.iterdir():
        if entry.name.endswith(_MIGRATION_SUFFIX):
            number = int(entry.name.split(_MIGRATION_NUMBER_SEPARATOR, 1)[0])
            migrations.append((number, entry.read_text(encoding="utf-8")))
    return sorted(migrations, key=lambda migration: migration[0])


def _statements(sql: str) -> list[str]:
    return [statement.strip() for statement in sql.split(";") if statement.strip()]


def _migrate(connection: sqlite3.Connection) -> None:
    current_version = connection.execute("PRAGMA user_version").fetchone()[0]
    for number, sql in _migration_files():
        if number <= current_version:
            continue
        with transaction(connection):
            for statement in _statements(sql):
                connection.execute(statement)
            connection.execute(f"PRAGMA user_version = {number}")
