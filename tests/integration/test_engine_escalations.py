"""`EscalationRaiser`: packet content and the escalation row it opens (03-workflow.md §8)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from shikiru.agents.contracts import Rung
from shikiru.clock import SystemClock
from shikiru.domain.escalation import EscalationStatus, options_for
from shikiru.domain.events import EventKind
from shikiru.domain.tasks import EscalationReason, Role, Step
from shikiru.engine.escalations import EscalationRaiser, PacketContext, render_packet
from shikiru.store.escalations import EscalationStore, fetch_open_escalations
from shikiru.store.events import EventLog
from shikiru.store.runs import RunStore
from tests.support.store import seed_task

if TYPE_CHECKING:
    from tests.support.store import Connection

_STAR = "★"


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


@pytest.mark.parametrize("reason", list(EscalationReason))
def test_every_reason_renders_a_packet_with_every_section_and_exactly_one_star(
    reason: EscalationReason,
) -> None:
    context = _context_for(reason)

    packet = render_packet(context)

    headings = (
        "# T-1",
        "## Timeline",
        "## What was tried",
        "## Evidence",
        "## Question",
        "## Options",
    )
    for heading in headings:
        assert heading in packet
    evidence = (context.reports, context.facts, context.view_tail, context.sessions)
    for section in evidence:
        assert section
        assert section in packet
    assert packet.count(_STAR) == 1


async def test_raise_escalation_writes_the_packet_and_opens_the_escalation_row(
    connection: Connection, tmp_path: Path
) -> None:
    clock = SystemClock()
    events = EventLog(connection, clock)
    runs = RunStore(connection, clock)
    escalations_store = EscalationStore(connection, clock)
    raiser = EscalationRaiser(escalations_store, events, runs, tmp_path)
    task = seed_task(connection, clock, title="Add retries")
    run = runs.start(task.id, Role.LEADER, Rung(agent="claude", model="opus"))
    runs.set_session(run.id, "sess-123", "claude --resume sess-123")

    escalation = await raiser.raise_escalation(
        task,
        step=Step.VERIFY,
        reason=EscalationReason.ITERATIONS_EXHAUSTED,
        question="Should I accept this as-is?",
        attempted="Implement and verify cycled to the iteration limit.",
        reports=[{"label": "01-verify-i1", "json": '{"decision": "changes_requested"}'}],
        facts="### .\n\n#### Commits\n\nabc123 do the thing",
        view_tail='{"signal":"TurnEnded","last_message":"turn ended"}',
    )

    assert escalation.status is EscalationStatus.OPEN
    assert escalation.reason is EscalationReason.ITERATIONS_EXHAUSTED
    stored = escalations_store.get(escalation.id)
    assert stored is not None
    assert stored.packet_path == escalation.packet_path
    packet_text = await asyncio.to_thread(Path(escalation.packet_path).read_text)
    assert "Should I accept this as-is?" in packet_text
    assert "changes_requested" in packet_text
    assert "abc123 do the thing" in packet_text
    assert "turn ended" in packet_text
    assert "sess-123" in packet_text
    assert "claude --resume sess-123" in packet_text
    raised_events = events.since(0, kinds=[EventKind.ESCALATION], task_id=task.id)
    assert len(raised_events) == 1


async def test_packet_creation_failure_does_not_open_an_empty_escalation(
    connection: Connection, tmp_path: Path
) -> None:
    clock = SystemClock()
    events = EventLog(connection, clock)
    runs = RunStore(connection, clock)
    escalations_store = EscalationStore(connection, clock)
    raiser = EscalationRaiser(escalations_store, events, runs, tmp_path)
    task = seed_task(connection, clock, title="Packet failure")
    blocked_parent = tmp_path / ".shikiru" / "runs" / task.id / "escalations"
    blocked_parent.parent.mkdir(parents=True)
    blocked_parent.write_text("not a directory")

    with pytest.raises(FileExistsError):
        await raiser.raise_escalation(
            task,
            step=Step.VERIFY,
            reason=EscalationReason.DECISION_REQUIRED,
            question="What next?",
            attempted="Nothing yet.",
            reports=(),
            facts="",
            view_tail="",
        )

    assert fetch_open_escalations(connection) == []
