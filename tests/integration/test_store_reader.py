"""Integration tests for `StateReader`, including a cross-process reader (06 §1, §5)."""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from shikiru.domain.commands import Submit
from shikiru.domain.events import EventKind
from shikiru.process import SubprocessRunner
from shikiru.store.commands import CommandQueue
from shikiru.store.events import EventLog
from shikiru.store.reader import StateReader
from shikiru.store.tasks import TaskStore
from tests.support.clock import FakeClock
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from shikiru.store.database import Database
    from tests.support.store import Connection

PROJECT_ROOT = Path(__file__).resolve().parents[2]
START = datetime(2026, 1, 1, tzinfo=UTC)


def test_reader_sees_a_task_the_writer_committed(
    database: Database, connection: Connection
) -> None:
    clock = FakeClock(START)
    tasks = TaskStore(connection, clock)
    queue = CommandQueue(connection, clock)
    queued = queue.append(Submit(task_id="", title="t", request="r"))
    assert isinstance(queued.command, Submit)
    task = tasks.insert_from_submit(queued.command)

    reader = StateReader(database, clock)
    try:
        assert reader.get_task(task.id) == task
        assert reader.get_task("T-does-not-exist") is None
    finally:
        reader.close()


async def test_reader_in_another_process_sees_the_writers_commit(
    tmp_path: Path, connection: Connection
) -> None:
    db_path = tmp_path / "state.db"
    ready_path = tmp_path / "ready"
    clock = FakeClock(START)
    events = EventLog(connection, clock)

    runner = SubprocessRunner()
    child = asyncio.ensure_future(
        runner.run(
            [
                sys.executable,
                "-m",
                "tests.support.store_reader_process",
                str(db_path),
                str(ready_path),
            ],
            timeout=15,
            cwd=PROJECT_ROOT,
            check=True,
        )
    )

    await wait_until(ready_path.exists, timeout=5)
    events.append(EventKind.ENGINE_STARTED, message="engine started")

    result = await child

    assert result.stdout.splitlines() == ["changed"]
