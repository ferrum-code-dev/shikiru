"""`EscalationRaiser`: packet content and the escalation row it opens (03-workflow.md §8)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from shikiru.agents.contracts import Rung, role_dir
from shikiru.clock import SystemClock
from shikiru.domain.escalation import EscalationStatus, options_for
from shikiru.domain.escalation_evidence import parse_escalation_packet
from shikiru.domain.events import EventKind
from shikiru.domain.reports import (
    REPORT_FILENAME,
    REPORT_KIND_FILENAME,
    ConsultAnswer,
    EscalationRequest,
    ReportKind,
)
from shikiru.domain.reports import (
    Option as AgentOption,
)
from shikiru.domain.scheduling import WaitReason
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import EscalationReason, Role, Step
from shikiru.engine.escalations import PacketContext, render_packet
from shikiru.errors import ShikiruError
from shikiru.paths import run_dir, turns_dir
from shikiru.store.escalations import EscalationStore, fetch_open_escalations
from shikiru.store.events import EventLog
from shikiru.store.runs import RunStore
from shikiru.store.tasks import TaskStore
from tests.support.engine import build_raiser
from tests.support.factories import make_verdict
from tests.support.store import seed_task

if TYPE_CHECKING:
    from collections.abc import Callable

    from shikiru.domain.escalation import Escalation
    from shikiru.domain.tasks import Task
    from shikiru.engine.escalations import EscalationRaiser
    from tests.support.store import Connection

_STAR = "★"
_STATE_FAILURE = "state write failed"


def _context_for(reason: EscalationReason) -> PacketContext:
    return PacketContext(
        task_id="T-1",
        step=Step.VERIFY,
        reason=reason,
        timeline=["2026-01-01T00:00:00+00:00 turn_completed: implemented"],
        attempted="Implement and verify cycled once.",
        reports="**verify**\n\n{}",
        facts="### .\n\n#### Commits\n\nabc123 do the thing",
        view_tail='{"signal":"TurnEnded","last_message":"turn ended"}',
        sessions="- leader: fake session `abc` — resume abc",
        question="What should happen next?",
        options=options_for(reason),
    )


def test_every_reason_renders_a_packet_with_its_evidence_and_exactly_one_star() -> None:
    for reason in EscalationReason:
        context = _context_for(reason)

        packet = render_packet(context)

        for evidence in (context.reports, context.facts, context.view_tail, context.sessions):
            assert evidence
            assert evidence in packet
        assert packet.count(_STAR) == 1, reason


async def test_raise_escalation_writes_the_packet_and_opens_the_escalation_row(
    connection: Connection, tmp_path: Path
) -> None:
    clock = SystemClock()
    events = EventLog(connection, clock)
    runs = RunStore(connection, clock)
    escalations_store = EscalationStore(connection, clock)
    raiser = build_raiser(connection, clock, tmp_path)
    task = seed_task(connection, clock, title="Add retries")
    run = runs.start(task.id, Role.LEADER, Rung(agent="claude", model="opus"))
    runs.set_session(run.id, "sess-123", "claude --resume sess-123")
    for step in (Step.IMPLEMENT, Step.VERIFY):
        events.append(
            EventKind.STEP_STARTED, task_id=task.id, message="", data={"step": step.value}
        )
    events.append(
        EventKind.TURN_COMPLETED,
        task_id=task.id,
        message="Reviewed",
        data={"step": "verify", "role": "leader", "turn": "01-verify-i1"},
    )
    verdict = make_verdict("changes_requested")

    escalation = await raiser.raise_escalation(
        task,
        step=Step.VERIFY,
        reason=EscalationReason.ITERATIONS_EXHAUSTED,
        question="Should I accept this as-is?",
        attempted="Implement and verify cycled to the iteration limit.",
        reports=[{"label": "01-verify-i1", "json": verdict.model_dump_json()}],
        facts="### .\n\n#### Commits\n\nabc123 do the thing",
        view_tail='{"signal":"TurnEnded","last_message":"turn ended"}',
        apply_state=lambda: task,
    )

    assert escalation.status is EscalationStatus.OPEN
    assert escalation.reason is EscalationReason.ITERATIONS_EXHAUSTED
    stored = escalations_store.get(escalation.id)
    assert stored is not None
    assert stored.packet_path == escalation.packet_path
    packet_text = await asyncio.to_thread(Path(escalation.packet_path).read_text)
    parsed_packet = parse_escalation_packet(packet_text)
    assert parsed_packet.agent_request is None
    assert parsed_packet.errors == ()
    assert tuple(option.kind for option in parsed_packet.resolutions) == tuple(
        option.label for option in escalation.options
    )
    assert "Should I accept this as-is?" in packet_text
    assert "changes_requested" in packet_text
    tried = dict(parsed_packet.evidence_sections)["What was tried"].splitlines()
    assert tried[0] == "Implement and verify cycled to the iteration limit."
    assert len(tried) > 1
    assert any("implement" in line and "verify" in line for line in tried)
    assert any(line.startswith("Leader: verify") for line in tried)
    assert "abc123 do the thing" in packet_text
    assert "turn ended" in packet_text
    assert "sess-123" in packet_text
    assert "claude --resume sess-123" in packet_text
    raised_events = events.since(0, kinds=[EventKind.ESCALATION], task_id=task.id)
    assert len(raised_events) == 1


def _write_consult_turn(turn_dir: Path, request: EscalationRequest) -> None:
    turn_dir.mkdir(parents=True)
    (turn_dir / REPORT_KIND_FILENAME).write_text(ReportKind.CONSULT.value)
    (turn_dir / REPORT_FILENAME).write_text(
        ConsultAnswer(decision="escalate", escalation=request).model_dump_json()
    )


async def test_restored_packet_uses_the_latest_typed_agent_alternatives(
    connection: Connection,
    tmp_path: Path,
) -> None:
    clock = SystemClock()
    task = seed_task(connection, clock, title="Restore agent alternatives")
    reason = EscalationReason.DECISION_REQUIRED
    TaskStore(connection, clock).apply_transition(
        task.id,
        Transition.escalate(task, reason, step=Step.CONSULT),
    )
    old_request = EscalationRequest(
        question="Should the old behavior remain?",
        options=[
            AgentOption(
                label="Old choice", consequences="Keep the earlier behavior.", recommended=True
            )
        ],
    )
    current_request = EscalationRequest(
        question="Which behavior should remain?",
        options=[
            AgentOption(
                label="Current choice", consequences="Keep the current behavior.", recommended=True
            )
        ],
    )
    turns = turns_dir(tmp_path, task.id)
    await asyncio.to_thread(_write_consult_turn, turns / "01-consult", old_request)
    await asyncio.to_thread(_write_consult_turn, turns / "02-consult", current_request)
    escalation = EscalationStore(connection, clock).open(
        task.id,
        step=Step.CONSULT,
        reason=reason,
        headline="The Leader needs an Operator decision to continue.",
        packet_path=str(tmp_path / "restored-escalation.md"),
        options=options_for(reason),
    )

    await build_raiser(connection, clock, tmp_path).ensure_actionable(task)

    packet = await asyncio.to_thread(Path(escalation.packet_path).read_text)
    alternatives_start = packet.index("## Agent alternatives")
    options_start = packet.index("\n## Options", alternatives_start)
    alternatives = packet[alternatives_start:options_start]
    assert "Which behavior should remain?" in alternatives
    assert "Current choice" in alternatives
    assert "Old choice" not in alternatives
    assert "`answer` ★" in packet
    assert "cancel" in packet


async def _raise(
    raiser: EscalationRaiser, task: Task, apply_state: Callable[[], Task]
) -> Escalation:
    return await raiser.raise_escalation(
        task,
        step=Step.VERIFY,
        reason=EscalationReason.DECISION_REQUIRED,
        question="What next?",
        attempted="Nothing yet.",
        reports=(),
        facts="",
        view_tail="",
        apply_state=apply_state,
    )


async def test_a_failure_after_the_state_write_rolls_back_state_row_event_and_packet(
    connection: Connection, tmp_path: Path
) -> None:
    clock = SystemClock()
    tasks = TaskStore(connection, clock)
    raiser = build_raiser(connection, clock, tmp_path)
    task = seed_task(connection, clock, title="State failure")

    def write_then_fail() -> Task:
        tasks.set_wait_reason(task.id, WaitReason.DEPENDENCY)
        raise ShikiruError(_STATE_FAILURE)

    with pytest.raises(ShikiruError, match=_STATE_FAILURE):
        await _raise(raiser, task, write_then_fail)

    stored = tasks.get(task.id)
    assert stored is not None
    assert stored.wait_reason is None
    assert fetch_open_escalations(connection) == []
    assert not await asyncio.to_thread(
        lambda: list((run_dir(tmp_path, task.id) / "escalations").glob("*"))
    )
    events = EventLog(connection, clock)
    assert events.since(0, kinds=[EventKind.ESCALATION], task_id=task.id) == []


async def test_an_unwritable_packet_directory_still_escalates_and_the_packet_follows_the_repair(
    connection: Connection, tmp_path: Path
) -> None:
    clock = SystemClock()
    events = EventLog(connection, clock)
    raiser = build_raiser(connection, clock, tmp_path)
    task = seed_task(connection, clock, title="Packet failure")
    root = run_dir(tmp_path, task.id)
    blocked = root / "escalations"
    blocked.parent.mkdir(parents=True)
    blocked.write_text("not a directory")
    turn = turns_dir(tmp_path, task.id) / "001-verify"
    turn.mkdir(parents=True)
    (turn / REPORT_KIND_FILENAME).write_text(ReportKind.VERDICT.value)
    (turn / REPORT_FILENAME).write_text(make_verdict("changes_requested").model_dump_json())
    view = role_dir(root, Role.LEADER.value) / "view.jsonl"
    view.parent.mkdir(parents=True)
    view.write_text('{"signal":"TurnEnded","last_message":"kept evidence"}\n')

    escalation = await _raise(raiser, task, lambda: task)
    for _ in range(3):
        await raiser.ensure_actionable(task)

    assert [e.id for e in fetch_open_escalations(connection)] == [escalation.id]
    faults = events.since(0, kinds=[EventKind.ENGINE_FAULT], task_id=task.id)
    assert len(faults) == 1
    assert faults[0].data["recoverable"] is True
    assert not await asyncio.to_thread(Path(escalation.packet_path).exists)

    blocked.unlink()
    await raiser.ensure_actionable(task)

    packet = await asyncio.to_thread(Path(escalation.packet_path).read_text)
    assert "Missing tests" in packet
    assert "kept evidence" in packet
    assert len(fetch_open_escalations(connection)) == 1
