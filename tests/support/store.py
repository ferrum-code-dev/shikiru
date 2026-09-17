"""Shared fixtures for state-store integration tests: connections closed on teardown."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest

from shikiru.store.database import Database

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

Connection = sqlite3.Connection


@pytest.fixture
def database(tmp_path: Path) -> Database:
    """A `Database` over a fresh `state.db` file in `tmp_path`."""
    return Database(tmp_path / "state.db")


@pytest.fixture
def connection(database: Database) -> Iterator[Connection]:
    """A migrated read-write connection over `database`, closed on teardown."""
    conn = database.connect()
    try:
        yield conn
    finally:
        conn.close()
