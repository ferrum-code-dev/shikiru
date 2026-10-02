"""Integration tests for `StateReader`, including a cross-process reader (06 §1, §5)."""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from shikiru.agents.contracts import QuotaWindow, Rung
from shikiru.domain.commands import CommandStatus, Prioritize, Say
from shikiru.domain.escalation import options_for
from shikiru.domain.events import EventKind
from shikiru.domain.tasks import EscalationReason, Role, Status
from shikiru.process import SubprocessRunner
from shikiru.store.commands import CommandQueue
from shikiru.store.escalations import EscalationStore
from shikiru.store.events import EventLog
from shikiru.store.quotas import QuotaStore
from shikiru.store.runs import RunStore
from tests.support.clock import FakeClock
from tests.support.store import seed_task
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from shikiru.store.database import Database
    from shikiru.store.reader import StateReader
    from tests.support.store import Connection

PROJECT_ROOT = Path(__file__).resolve().parents[2]
START = datetime(2026, 1, 1, tzinfo=UTC)
RUNG = Rung(agent="codex", model="sol", effort="high")


def test_reader_sees_a_task_the_writer_committed(
    connection: Connection, reader: StateReader
) -> None:
    task = seed_task(connection, FakeClock(START))

    assert reader.get_task(task.id) == task
    assert reader.get_task("T-does-not-exist") is None


def test_reader_serves_bounded_command_outcomes_from_another_client(
    connection: Connection,
    database: Database,
    reader: StateReader,
) -> None:
    clock = FakeClock(START)
    queue = CommandQueue(connection, clock)
    applied = queue.append(Prioritize(task_id="T-1", priority=7))
    queue.mark_done(applied.id, {"priority": 7})
    other_client = database.connect()
    try:
        other_queue = CommandQueue(other_client, clock)
        pending = other_queue.append(Say(task_id="T-2", role=Role.IMPLEMENTER, text="next turn"))
        rejected = other_queue.append(Prioritize(task_id="T-3", priority=9))
        rejection = {"error": "engine rejected the priority"}
        other_queue.mark_rejected(rejected.id, rejection)
    finally:
        other_client.close()

    history = reader.command_history(limit=2)
    assert [entry.id for entry in history.entries] == [rejected.id, pending.id]
    assert [entry.status for entry in history.entries] == [
        CommandStatus.REJECTED,
        CommandStatus.PENDING,
    ]
    assert history.entries[0].result == rejection
    pending_page = reader.command_history(limit=1, status=CommandStatus.PENDING)
    assert pending_page.total_count == 1
    assert not pending_page.has_more
    matching_pending = reader.pending_command(
        Say(task_id="T-2", role=Role.IMPLEMENTER, text="next turn")
    )
    assert matching_pending is not None
    assert matching_pending.id == pending.id
    applied_status = reader.command_status(applied.id)
    assert applied_status is not None
    assert applied_status.status is CommandStatus.DONE
    assert applied_status.result == {"priority": 7}


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


def test_reader_serves_every_query_seeded_through_the_writer(
    connection: Connection, reader: StateReader
) -> None:
    clock = FakeClock(START)
    task = seed_task(connection, clock)

    run = RunStore(connection, clock).start(task.id, Role.IMPLEMENTER, RUNG)

    reason = EscalationReason.DECISION_REQUIRED
    escalation = EscalationStore(connection, clock).open(
        task.id,
        step=None,
        reason=reason,
        headline="Need a decision",
        packet_path="runs/T-1/escalations/E-1.md",
        options=options_for(reason),
    )

    QuotaStore(connection).record(
        "codex", QuotaWindow(name="5h", used_pct=10.0, resets_at=None, observed_at=START)
    )

    EventLog(connection, clock).append(EventKind.TASK_QUEUED, message="queued", task_id=task.id)
    EventLog(connection, clock).append(EventKind.TASK_STARTED, message="started", task_id=task.id)
    EventLog(connection, clock).append(EventKind.TASK_QUEUED, message="other task", task_id="T-2")

    assert [t.id for t in reader.list_tasks()] == [task.id]
    assert [t.id for t in reader.list_tasks(statuses=(Status.QUEUED,))] == [task.id]
    assert reader.list_tasks(statuses=(Status.DONE,)) == []

    assert [r.id for r in reader.list_runs(task.id)] == [run.id]

    assert reader.get_escalation(escalation.id) is not None
    assert reader.get_escalation("E-does-not-exist") is None
    assert [e.id for e in reader.list_open_escalations()] == [escalation.id]

    assert [w.name for w in reader.list_quotas(["codex"])["codex"]] == ["5h"]
    assert reader.list_quota_agents() == ["codex"]

    assert [e.message for e in reader.events_since(0)] == ["queued", "started", "other task"]
