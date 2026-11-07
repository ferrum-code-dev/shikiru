"""Connections, migrations and JSON/timestamp column helpers."""

from __future__ import annotations

import sqlite3  # noqa: TID251
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib import resources
from typing import TYPE_CHECKING

from shikiru.errors import DatabaseError
from shikiru.private import create_private_file, make_private_dir

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

    from pydantic import TypeAdapter

BUSY_TIMEOUT_MS = 5000
OperationalDatabaseError = sqlite3.OperationalError
"""A database fault of the environment: locked, full or unreadable."""
_MIGRATIONS_PACKAGE = "shikiru.store.migrations"
_MIGRATION_SUFFIX = ".sql"
_MIGRATION_NUMBER_SEPARATOR = "_"
_BEGIN = "BEGIN"
_BEGIN_IMMEDIATE = "BEGIN IMMEDIATE"
_READ_VERSION = "PRAGMA user_version"


class Database:
    """Opens `.shikiru/state.db`, keeping its schema migrated to the packaged latest version."""

    def __init__(self, path: Path) -> None:
        """Point at the SQLite file `path`; no connection is opened until asked for one."""
        self._path = path

    @property
    def path(self) -> Path:
        """Return the database file path used in actionable read errors."""
        return self._path

    def connect(self) -> sqlite3.Connection:
        """Open a read-write connection and migrate the schema up to date.

        The engine uses this for every state table; clients (CLI, MCP) use it only to append
        `commands` rows and, for `Submit`, to allocate the task id from `sequences`.
        """
        make_private_dir(self._path.parent)
        create_private_file(self._path)
        connection = _guard_schema(self._connect(mode="rwc"), self._path)
        connection.execute("PRAGMA journal_mode = WAL")
        _migrate(connection)
        return connection

    def connect_readonly(self) -> sqlite3.Connection:
        """Open a connection SQLite itself refuses to let write."""
        try:
            connection = self._connect(mode="ro")
        except sqlite3.Error as error:
            message = f"could not open read-only database {self._path}: {error}"
            raise DatabaseError(message) from error
        return _guard_schema(connection, self._path)

    def schema_version(self, connection: sqlite3.Connection) -> int:
        """Return the migrated schema version for an open database connection."""
        return _schema_version(connection)

    def health(self) -> int:
        """Open and migrate the database, returning its schema version."""
        connection: sqlite3.Connection | None = None
        try:
            connection = self.connect()
            return self.schema_version(connection)
        except sqlite3.Error as error:
            raise DatabaseError(str(error)) from error
        finally:
            if connection is not None:
                connection.close()

    def _uri(self, mode: str) -> str:
        return f"{self._path.absolute().as_uri()}?mode={mode}"

    def _connect(self, *, mode: str) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self._uri(mode),
            uri=True,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


@contextmanager
def transaction(
    connection: sqlite3.Connection, *, immediate: bool = False
) -> Generator[sqlite3.Connection]:
    """Run a block as one transaction: commits on success, rolls back on any exception.

    With `immediate` the write lock is taken at once, so what the block reads first cannot
    change before it writes.

    Composes with an already-open transaction by joining it, so multiple store operations can
    participate in one atomic unit without attempting a nested `BEGIN`.
    """
    if connection.in_transaction:
        yield connection
        return
    connection.execute(_BEGIN_IMMEDIATE if immediate else _BEGIN)
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
    """Format `value` as the fixed-width UTC ISO-8601 text every timestamp column stores.

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


def require_after_insert[T](value: T | None, message: str) -> T:
    """Unwrap `value`, or raise `RuntimeError(message)` if it's `None`.

    Guards the two ways a same-transaction insert-then-read can't come back empty in practice:
    sqlite refusing a rowid, or an immediate re-fetch by that rowid finding no row.
    """
    if value is None:
        raise RuntimeError(message)
    return value


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


def _schema_version(connection: sqlite3.Connection) -> int:
    return connection.execute(_READ_VERSION).fetchone()[0]


def _guard_schema(connection: sqlite3.Connection, path: Path) -> sqlite3.Connection:
    stored = _schema_version(connection)
    known = _migration_files()[-1][0]
    if stored > known:
        connection.close()
        message = (
            f"state database {path} has schema version {stored}, newer than the version "
            f"{known} this Shikiru knows; use the Shikiru release that wrote it"
        )
        raise DatabaseError(message)
    return connection


def _migrate(connection: sqlite3.Connection) -> None:
    migrations = _migration_files()
    if _schema_version(connection) >= migrations[-1][0]:
        return
    for number, sql in migrations:
        with transaction(connection, immediate=True):
            if number <= _schema_version(connection):
                continue
            for statement in _statements(sql):
                connection.execute(statement)
            connection.execute(f"PRAGMA user_version = {number}")
