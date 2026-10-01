from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from shikiru.domain.commands import COMMAND_POLL_SECONDS, Prioritize
from shikiru.domain.events import EventKind
from shikiru.domain.reports import ConsultAnswer
from shikiru.domain.steps import Transition, first_step
from shikiru.domain.tasks import EscalationReason, RepoState, Role, Status
from shikiru.errors import DatabaseError
from shikiru.operator.commands import (
    COMMAND_ACK_WAIT_SECONDS,
    AckStatus,
    OperatorCommands,
)
from shikiru.operator.events import EventFeed
from shikiru.operator.queries import PEEK_TAIL_LINES, OperatorQueries, OperatorQueryError
from shikiru.paths import run_dir
from shikiru.store.commands import CommandQueue
from shikiru.store.database import Database, transaction
from shikiru.store.escalations import EscalationStore
from shikiru.store.events import EventLog
from shikiru.store.runs import RunStore
from shikiru.store.tasks import TaskStore
from shikiru.view_log import view_log_path
from shikiru.workspace.reports import InvalidTurnReportError
from tests.support.clock import FakeClock
from tests.support.factories import LEADER_RUNG, LEADER_RUNG_2
from tests.support.store import seed_task
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.store.reader import StateReader
    from tests.support.store import Connection

pytestmark = pytest.mark.asyncio

START = datetime(2026, 1, 1, tzinfo=UTC)


async def test_commands_report_queued_when_engine_is_down(connection: Connection) -> None:
    clock = FakeClock(START)
    queue = CommandQueue(connection, clock)
    commands = OperatorCommands(queue, clock, engine_up=lambda: False)

    ack = await commands.submit_task("queued request")

    assert ack.status is AckStatus.QUEUED
    assert ack.message
    assert ack.result == {"task_id": "T-1"}
    assert len(queue.pending()) == 1


async def test_commands_distinguish_ack_timeout_from_engine_down(
    connection: Connection,
) -> None:
    clock = FakeClock(START)
    queue = CommandQueue(connection, clock)
    down_ack = await OperatorCommands(queue, clock, engine_up=lambda: False).submit_task(
        "engine down"
    )
    commands = OperatorCommands(queue, clock, engine_up=lambda: True)
    pending = asyncio.create_task(commands.send(Prioritize(task_id="T-1", priority=5)))

    await wait_until(clock.has_pending_sleep, timeout=1.0)
    clock.advance(COMMAND_ACK_WAIT_SECONDS)
    ack = await pending

    assert ack.status is AckStatus.QUEUED
    assert ack.message
    assert down_ack.message
    assert ack.message != down_ack.message


async def test_commands_return_done_ack_after_engine_marks_command(
    connection: Connection,
) -> None:
    clock = FakeClock(START)
    queue = CommandQueue(connection, clock)
    commands = OperatorCommands(queue, clock, engine_up=lambda: True)
    pending = asyncio.create_task(commands.send(Prioritize(task_id="T-1", priority=5)))

    await wait_until(clock.has_pending_sleep, timeout=1.0)
    queued = queue.pending()[0]
    queue.mark_done(queued.id, {"accepted": True})
    clock.advance(COMMAND_POLL_SECONDS)
    ack = await pending

    assert ack.status is AckStatus.DONE
    assert ack.result == {"accepted": True}


async def test_a_command_queued_while_the_database_is_locked_fails_as_a_shikiru_error(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "state.db")
    holder = database.connect()
    client = database.connect()
    client.execute("PRAGMA busy_timeout = 50")
    clock = FakeClock(START)
    commands = OperatorCommands(CommandQueue(client, clock), clock, engine_up=lambda: True)

    with transaction(holder, immediate=True), pytest.raises(DatabaseError):
        await commands.submit_task("while locked")

    assert CommandQueue(client, clock).pending() == []
    holder.close()
    client.close()


def _queries(
    project_root: Path, reader: StateReader, clock: FakeClock | None = None
) -> OperatorQueries:
    return OperatorQueries(
        reader,
        project="demo",
        project_root=project_root,
        engine_up=lambda: False,
        max_parallel=2,
        clock=clock or FakeClock(START),
    )


async def test_status_uses_dispatch_order_and_excludes_escalated_slots(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    clock = FakeClock(START)
    first = seed_task(connection, clock, title="first", priority=1)
    second = seed_task(connection, clock, title="second", priority=9)
    third = seed_task(connection, clock, title="third", priority=0)
    queued_task = seed_task(connection, clock, title="priority", priority=5)
    escalated_task = seed_task(connection, clock, title="escalated")
    tasks = TaskStore(connection, clock)
    tasks.set_requeued(first.id)
    tasks.apply_transition(second.id, first_step(second))
    tasks.set_slot(second.id, 1)
    escalated = Transition.stay(escalated_task, step=escalated_task.step, status=Status.ESCALATED)
    tasks.apply_transition(escalated_task.id, escalated)
    tasks.set_slot(escalated_task.id, 2)

    view = _queries(tmp_path, reader).status()

    assert [task.id for task in view.queued_tasks] == [first.id, queued_task.id, third.id]
    assert [task.id for task in view.active_tasks] == [second.id, escalated_task.id]
    assert view.slots.active == 1


async def test_task_detail_contains_events_reports_branches_and_sessions(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="detail")
    TaskStore(connection, clock).set_repo(
        task.id, RepoState(base="main", base_ref="main", base_sha="a", branch="shikiru/T-1-detail")
    )
    EventLog(connection, clock).append(
        EventKind.TASK_QUEUED,
        task_id=task.id,
        message="queued",
    )
    turn_dir = tmp_path / ".shikiru" / "runs" / task.id / "turns" / "01-triage-i0"
    turn_dir.mkdir(parents=True)
    (turn_dir / "report.kind").write_text("triage")
    (turn_dir / "report.json").write_text(
        '{"title":"triage","mode":"implement","complexity":"normal",'
        '"acceptance_criteria":[],"references":[]}'
    )

    detail = _queries(tmp_path, reader).get_task(task.id)

    assert detail is not None
    assert len(detail.events) == 1
    assert detail.model_dump(mode="json")["reports"] == [
        {
            "turn": "01-triage-i0",
            "kind": "triage",
            "data": {
                "title": "triage",
                "mode": "implement",
                "complexity": "normal",
                "acceptance_criteria": [],
                "references": [],
            },
        }
    ]
    assert detail.branches == ["shikiru/T-1-detail"]
    assert detail.sessions == []


async def test_task_detail_uses_a_valid_helper_rebuild_when_agent_report_is_invalid(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="helper detail")
    turn_dir = tmp_path / ".shikiru" / "runs" / task.id / "turns" / "01-consult-i0"
    turn_dir.mkdir(parents=True)
    (turn_dir / "report.kind").write_text("consult")
    (turn_dir / "report.json").write_text("invalid")
    helper = ConsultAnswer(decision="answer", answer="Use the safe path")
    (turn_dir / "helper.json").write_text(helper.model_dump_json())

    detail = _queries(tmp_path, reader).get_task(task.id)

    assert detail is not None
    assert detail.model_dump(mode="json")["reports"] == [
        {
            "turn": "01-consult-i0",
            "kind": "consult",
            "data": helper.model_dump(mode="json"),
        }
    ]


async def test_task_detail_surfaces_a_corrupt_report(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="corrupt detail")
    turn_dir = tmp_path / ".shikiru" / "runs" / task.id / "turns" / "01-consult-i0"
    turn_dir.mkdir(parents=True)
    (turn_dir / "report.kind").write_text("consult")
    (turn_dir / "report.json").write_text("invalid")

    with pytest.raises(InvalidTurnReportError):
        _queries(tmp_path, reader).get_task(task.id)


async def test_missing_packet_and_session_are_operator_errors(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="peek")
    escalation = EscalationStore(connection, clock).open(
        task.id,
        step=None,
        reason=EscalationReason.DECISION_REQUIRED,
        headline="question",
        packet_path="missing.packet",
        options=(),
    )
    queries = _queries(tmp_path, reader)

    with pytest.raises(OperatorQueryError):
        queries.get_escalation(escalation.id)
    with pytest.raises(OperatorQueryError):
        queries.peek_session(task.id)


async def test_peek_reads_latest_run_and_view_log_tail(
    tmp_path: Path,
    connection: Connection,
    reader: StateReader,
) -> None:
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="peek")
    runs = RunStore(connection, clock)
    stale = runs.start(task.id, Role.LEADER, LEADER_RUNG)
    runs.set_last_message(stale.id, "old")
    current = runs.start(task.id, Role.LEADER, LEADER_RUNG_2)
    runs.set_last_message(current.id, "latest")
    log = view_log_path(run_dir(tmp_path, task.id), Role.LEADER.value)
    log.parent.mkdir(parents=True)
    log.write_text("".join(f"line-{index}\n" for index in range(PEEK_TAIL_LINES + 5)))

    view = _queries(tmp_path, reader).peek_session(task.id, Role.LEADER)

    assert view is not None
    pane = view.panes[Role.LEADER.value]
    assert (pane.agent, pane.last_message) == (LEADER_RUNG_2.agent, "latest")
    assert pane.view == [f"line-{index}" for index in range(5, PEEK_TAIL_LINES + 5)]


async def test_event_feed_filters_and_zero_timeout_are_bounded(
    connection: Connection,
    reader: StateReader,
) -> None:
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="events")
    event_log = EventLog(connection, clock)
    first = event_log.append(EventKind.TASK_QUEUED, task_id=task.id, message="queued")
    event_log.append(EventKind.TASK_DONE, task_id=task.id, message="done")
    feed = EventFeed(reader, clock)

    events = feed.read(first.id - 1, kinds=(EventKind.TASK_DONE,), task=task.id)
    waited = await feed.wait(first.id + 1, task=task.id, timeout=0)

    assert [event.kind for event in events] == [EventKind.TASK_DONE]
    assert waited == []


async def test_status_reports_how_many_run_directories_exist_and_their_size(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    del connection
    for task_id, content in (("T-1", b"abc"), ("T-2", b"defgh")):
        evidence = run_dir(tmp_path, task_id) / "turns" / "1" / "evidence.log"
        evidence.parent.mkdir(parents=True)
        evidence.write_bytes(content)

    usage = _queries(tmp_path, reader).status().run_directories

    assert (usage.directories, usage.size_bytes) == (2, len(b"abc") + len(b"defgh"))
