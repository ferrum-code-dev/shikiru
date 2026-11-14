"""Build state databases at an earlier schema version, using the packaged migrations."""

from __future__ import annotations

import sqlite3  # noqa: TID251
from importlib import resources
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

_MIGRATIONS_PACKAGE = "shikiru.store.migrations"
_MIGRATION_SUFFIX = ".sql"
_MIGRATION_NUMBER_SEPARATOR = "_"


def migration_scripts() -> list[tuple[str, str]]:
    """Return every packaged migration as `(file name, SQL)`, in order."""
    return sorted(
        (entry.name, entry.read_text(encoding="utf-8"))
        for entry in resources.files(_MIGRATIONS_PACKAGE).iterdir()
        if entry.name.endswith(_MIGRATION_SUFFIX)
    )


def migration_number(name: str) -> int:
    """Return the schema version a migration file name introduces."""
    return int(name.split(_MIGRATION_NUMBER_SEPARATOR, 1)[0])


def database_at_schema(path: Path, schema: int) -> None:
    """Create the database at `path` migrated through `schema`, as an earlier release left it."""
    legacy = sqlite3.connect(path)
    try:
        legacy.execute("PRAGMA journal_mode = WAL")
        for name, sql in migration_scripts():
            if migration_number(name) <= schema:
                legacy.executescript(sql)
        legacy.execute(f"PRAGMA user_version = {schema}")
        legacy.commit()
    finally:
        legacy.close()
