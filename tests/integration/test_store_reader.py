"""Integration tests for `StateReader`, including a cross-process reader (06 §1, §5)."""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from shikiru.agents.contracts import QuotaWindow, Rung
from shikiru.domain.commands import Submit
from shikiru.domain.escalation import options_for
from shikiru.domain.events import EventKind
from shikiru.domain.tasks import EscalationReason, Role, Status
from shikiru.process import SubprocessRunner
from shikiru.store.commands import CommandQueue
from shikiru.store.escalations import EscalationStore
from shikiru.store.events import EventLog
from shikiru.store.quotas import QuotaStore
from shikiru.store.reader import StateReader
from shikiru.store.runs import RunStore
from shikiru.store.tasks import TaskStore
from tests.support.clock import FakeClock
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from shikiru.store.database import Database
    from tests.support.store import Connection

PROJECT_ROOT = Path(__file__).resolve().parents[2]
START = datetime(2026, 1, 1, tzinfo=UTC)
RUNG = Rung(agent="codex", model="sol", effort="high")


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
            timeout=45,
            cwd=PROJECT_ROOT,
            check=True,
        )
    )

    await wait_until(ready_path.exists, timeout=20)
    events.append(EventKind.ENGINE_STARTED, message="engine started")

    result = await child

    assert result.stdout.splitlines() == ["changed"]


def test_reader_serves_every_query_seeded_through_the_writer(database: Database) -> None:
    writer = database.connect()
    try:
        clock = FakeClock(START)
        queue = CommandQueue(writer, clock)
        queued = queue.append(Submit(task_id="", title="t", request="r"))
        assert isinstance(queued.command, Submit)
        task = TaskStore(writer, clock).insert_from_submit(queued.command)

        run = RunStore(writer, clock).start(task.id, Role.IMPLEMENTER, RUNG)

        reason = EscalationReason.DECISION_REQUIRED
        escalation = EscalationStore(writer, clock).open(
            task.id,
            step=None,
            reason=reason,
            headline="Need a decision",
            packet_path="runs/T-1/escalations/E-1.md",
            options=options_for(reason),
        )

        QuotaStore(writer).record(
            "codex", QuotaWindow(name="5h", used_pct=10.0, resets_at=None, observed_at=START)
        )
        QuotaStore(writer).block("codex", START + timedelta(hours=1), "quota")

        event = EventLog(writer, clock).append(
            EventKind.TASK_QUEUED, message="queued", task_id=task.id
        )
        EventLog(writer, clock).append(EventKind.TASK_STARTED, message="started", task_id=task.id)
        EventLog(writer, clock).append(EventKind.TASK_QUEUED, message="other task", task_id="T-2")

        reader = StateReader(database, clock)
        try:
            assert [t.id for t in reader.list_tasks()] == [task.id]
            assert [t.id for t in reader.list_tasks(statuses=(Status.QUEUED,))] == [task.id]
            assert reader.list_tasks(statuses=(Status.DONE,)) == []

            assert [r.id for r in reader.list_runs(task.id)] == [run.id]

            assert reader.get_escalation(escalation.id) is not None
            assert reader.get_escalation("E-does-not-exist") is None
            assert [e.id for e in reader.list_open_escalations()] == [escalation.id]

            assert [w.name for w in reader.quotas_for("codex")] == ["5h"]

            assert [b.agent for b in reader.active_agent_blocks(START)] == ["codex"]
            assert reader.active_agent_blocks(START + timedelta(hours=2)) == []

            all_events = reader.events_since(0)
            assert [e.message for e in all_events] == ["queued", "started", "other task"]

            only_started = reader.events_since(0, kinds=[EventKind.TASK_STARTED])
            assert [e.kind for e in only_started] == [EventKind.TASK_STARTED]

            only_task = reader.events_since(0, task_id=task.id)
            assert [e.message for e in only_task] == ["queued", "started"]

            assert [e.message for e in reader.events_since(event.id)] == ["started", "other task"]
        finally:
            reader.close()
    finally:
        writer.close()
