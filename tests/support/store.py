"""Shared fixtures and helpers for state-store integration tests."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest

from shikiru.clock import SystemClock
from shikiru.domain.commands import Submit
from shikiru.store.commands import CommandQueue
from shikiru.store.database import Database
from shikiru.store.reader import StateReader
from shikiru.store.tasks import TaskStore

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from pathlib import Path

    from shikiru.agents.contracts import Rung
    from shikiru.clock import Clock
    from shikiru.config.settings import ComplexityName
    from shikiru.domain.tasks import Mode, Task

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


@pytest.fixture
def reader(database: Database) -> Iterator[StateReader]:
    """A `StateReader` over `database`, closed on teardown."""
    state_reader = StateReader(database, SystemClock())
    try:
        yield state_reader
    finally:
        state_reader.close()


def seed_task(
    connection: Connection,
    clock: Clock,
    *,
    title: str = "t",
    request: str | None = None,
    depends_on: Sequence[str] = (),
    priority: int = 0,
    mode: Mode | None = None,
    complexity: ComplexityName = "normal",
    leader: Rung | None = None,
    implementer: Rung | None = None,
) -> Task:
    """Submit and insert a task in one step, the way `submit "<request>"` would (03 §1.1)."""
    queued = CommandQueue(connection, clock).append(
        Submit(
            title=title,
            request=request if request is not None else title,
            mode=mode,
            complexity=complexity,
            priority=priority,
            depends_on=list(depends_on),
            leader=leader,
            implementer=implementer,
        )
    )
    assert isinstance(queued.command, Submit)
    return TaskStore(connection, clock).insert_from_submit(queued.command)
