from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from textual.widgets import Button, Collapsible, Input, Select, Static, TextArea

from shikiru.agents.contracts import Rung
from shikiru.dashboard.contract import PaletteRequest
from shikiru.dashboard.palette import PaletteApp, PaletteScreen
from shikiru.domain.commands import (
    AcceptResolution,
    AnswerResolution,
    Cancel,
    CancelResolution,
    CommandStatus,
    Pause,
    Prioritize,
    Resolve,
    RetryResolution,
    Say,
    SetRung,
    Submit,
    TakeOver,
)
from shikiru.domain.escalation import Escalation, options_for
from shikiru.domain.reports import EscalationRequest
from shikiru.domain.reports import Option as AgentOption
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import EscalationReason, Mode, Role, Status, Step
from shikiru.engine.escalations import PacketContext, render_packet
from shikiru.engine.handlers.base import HandlerContext
from shikiru.engine.handlers.escalations import ResolveHandler
from shikiru.engine.handlers.tasks import CancelHandler
from shikiru.errors import CommandRejectedError, ConfigError
from shikiru.operator.commands import COMMAND_ACK_WAIT_SECONDS, OperatorCommands
from shikiru.operator.queries import OperatorQueries
from shikiru.store.commands import CommandOutcome, CommandQueue
from shikiru.store.database import transaction
from shikiru.store.escalations import EscalationStore
from shikiru.store.events import EventLog
from shikiru.store.reader import DASHBOARD_CHANGE_POLL_SECONDS, StateReader
from shikiru.store.runs import RunStore
from shikiru.store.tasks import TaskStore
from shikiru.tmux.client import FORMAT_PANE_ID, Tmux, exact_target
from tests.support.clock import FakeClock
from tests.support.store import seed_task
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from rich.text import Text
    from textual.pilot import Pilot

    from shikiru.store.database import Database
    from tests.support.store import Connection
    from tests.support.tmux import TmuxServer


_START = datetime(2026, 1, 1, tzinfo=UTC)
_POLL_SECONDS = 0.25
_APP_SIZE = (120, 32)
_LIVE_RUNG = Rung(agent="codex", model="sol", effort="high")


def _seed_live_session(connection: Connection, clock: FakeClock, task_id: str) -> None:
    runs = RunStore(connection, clock)
    run = runs.start(task_id, Role.LEADER, _LIVE_RUNG)
    runs.set_session(run.id, "session-current", "resume-current")


def _services(
    connection: Connection,
    reader: StateReader,
    clock: FakeClock,
    project_dir: Path,
    *,
    engine_up: bool = False,
) -> tuple[OperatorCommands, OperatorQueries, CommandQueue]:
    queue = CommandQueue(connection, clock)
    for command in queue.pending():
        queue.mark_done(command.id)
    queries = OperatorQueries(
        reader,
        project="palette-test",
        project_root=project_dir,
        engine_up=lambda: engine_up,
        max_parallel=2,
        clock=clock,
    )
    commands = OperatorCommands(
        queue,
        clock,
        engine_up=lambda: engine_up,
    )
    return commands, queries, queue


def _screen(app: PaletteApp) -> PaletteScreen:
    return cast("PaletteScreen", app.screen)


def _palette_app(
    reader: StateReader,
    commands: OperatorCommands,
    queries: OperatorQueries,
    pane_selector: Callable[[str], Awaitable[None]],
    *,
    initial_context: PaletteRequest | None = None,
) -> PaletteApp:
    return PaletteApp(
        commands,
        queries,
        pane_selector,
        on_change=reader.watch,
        initial_context=initial_context,
    )


def _seed_escalation(
    connection: Connection,
    clock: FakeClock,
    task_id: str,
    project_dir: Path,
    reason: EscalationReason,
    *,
    write_packet: bool = True,
) -> tuple[Escalation, str]:
    task = TaskStore(connection, clock).require(task_id)
    TaskStore(connection, clock).apply_transition(
        task_id,
        Transition.escalate(task, reason, step=Step.CONSULT),
    )
    agent_request = EscalationRequest(
        question="Which behavior should remain?",
        options=[
            AgentOption(
                label="Keep existing behavior",
                consequences="Preserve the current call pattern.",
                recommended=True,
            ),
            AgentOption(
                label="Change behavior",
                consequences="Update callers and documentation.",
            ),
        ],
    )
    packet = render_packet(
        PacketContext(
            task_id=task_id,
            step=Step.CONSULT,
            reason=reason,
            timeline=["turn completed: implementation"],
            attempted="One implementation and review round.",
            reports="Implementation report evidence.",
            facts="Recorded git facts.",
            view_tail="Recent stream tail.",
            sessions="Leader session evidence.",
            question=agent_request.question,
            options=options_for(reason),
            agent_request=agent_request,
        )
    )
    packet_path = project_dir / f"{task_id}-{reason.value}.md"
    if write_packet:
        packet_path.write_text(packet)
    escalation = EscalationStore(connection, clock).open(
        task_id,
        step=Step.CONSULT,
        reason=reason,
        headline="A product decision is needed",
        packet_path=str(packet_path),
        options=options_for(reason),
    )
    return escalation, packet


def _handler_context(connection: Connection, clock: FakeClock, project_dir: Path) -> HandlerContext:
    async def no_task_action(_task_id: str) -> None:
        return None

    async def no_title_refresh(_task: object) -> None:
        return None

    async def no_finish(_task: object) -> Path | None:
        return None

    return HandlerContext(
        tasks=TaskStore(connection, clock),
        escalations=EscalationStore(connection, clock),
        events=EventLog(connection, clock),
        runs=RunStore(connection, clock),
        project_root=project_dir,
        cancel_task=no_task_action,
        start_task=no_task_action,
        finish_task=no_finish,
        stop_engine=lambda: None,
        refresh_titles=no_title_refresh,
        atomically=lambda: transaction(connection),
    )


async def _enqueue_and_cancel(
    connection: Connection,
    clock: FakeClock,
    task_id: str,
    project_dir: Path,
) -> CommandOutcome:
    queue = CommandQueue(connection, clock)
    inserted = queue.append(Cancel(task_id=task_id))
    queued = next(item for item in queue.pending() if item.id == inserted.id)
    assert isinstance(queued.command, Cancel)
    result = await CancelHandler(_handler_context(connection, clock, project_dir)).handle(
        queued.command
    )
    queue.mark_done(inserted.id, result)
    outcome = queue.status(inserted.id)
    assert outcome is not None
    return outcome


async def _open_action(pilot: Pilot[None], screen: PaletteScreen, action: str) -> None:
    screen.query_one("#action", Select).value = action
    await pilot.pause()


async def _set_target(pilot: Pilot[None], screen: PaletteScreen, task_id: str) -> None:
    screen.query_one("#task-id", Input).value = task_id
    await pilot.pause()


async def _set_role(pilot: Pilot[None], screen: PaletteScreen, role: Role) -> None:
    screen.query_one("#role", Select).value = role.value
    await pilot.pause()


async def _wait_for_ack(screen: PaletteScreen, status: str = "Pending") -> None:
    await wait_until(
        lambda: (
            status in _static_text(screen, "#palette-result")
            and screen.query_one("#palette-result-scroll").region.y
            >= screen.query_one("#palette-form").region.y
            and screen.query_one("#palette-result-scroll").region.bottom
            <= screen.query_one("#palette-form").region.bottom
        ),
        timeout=2,
    )


async def test_palette_context_targets_message_and_rung_without_id_entry(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    clock = FakeClock(_START)
    task = seed_task(connection, clock, title="Context task")
    commands, queries, queue = _services(connection, reader, clock, tmp_path)
    context = PaletteRequest(task_id=task.id, role=Role.IMPLEMENTER.value)
    app = _palette_app(reader, commands, queries, _selection_must_not_run, initial_context=context)

    async with app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        screen = _screen(app)
        assert screen.query_one("#task-id", Input).value == task.id
        assert cast("Select[str]", screen.query_one("#role", Select)).value == (
            Role.IMPLEMENTER.value
        )

        screen.query_one("#action-search", Input).value = "message"
        await pilot.pause()
        await _open_action(pilot, screen, "message")
        screen.query_one("#message", TextArea).text = "Check the failing integration path."
        await pilot.click("#palette-submit")
        await _wait_for_ack(screen)
        assert queue.pending()[0].command == Say(
            task_id=task.id,
            role=Role.IMPLEMENTER,
            text="Check the failing integration path.",
            command_id=queue.pending()[0].id,
        )

        screen.query_one("#action-search", Input).value = "rung"
        await pilot.pause()
        await _open_action(pilot, screen, "rung")
        screen.query_one("#rung", Input).value = "codex:sol:high"
        assert not screen.query_one("#palette-submit", Button).disabled, _static_text(
            screen, "#palette-action-state"
        )
        await pilot.click("#palette-submit")
        await wait_until(lambda: len(queue.pending()) == 2, timeout=2)
        assert queue.pending()[1].command == SetRung(
            task_id=task.id,
            role=Role.IMPLEMENTER,
            rung=_LIVE_RUNG,
        )


async def test_applied_message_feedback_keeps_next_turn_guidance_and_engine_result(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    clock = FakeClock(_START)
    task = seed_task(connection, clock, title="Guidance acknowledgement")
    commands, queries, queue = _services(
        connection,
        reader,
        clock,
        tmp_path,
        engine_up=True,
    )
    app = _palette_app(
        reader,
        commands,
        queries,
        _selection_must_not_run,
        initial_context=PaletteRequest(
            action="message",
            task_id=task.id,
            role=Role.IMPLEMENTER.value,
        ),
    )

    async with app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        screen = _screen(app)
        screen.query_one("#message", TextArea).text = "Review the updated evidence."
        await pilot.click("#palette-submit")
        await wait_until(clock.has_pending_sleep, timeout=2)
        queued = queue.pending()[0]
        returned = {"queued_for_next_turn": True}
        queue.mark_done(queued.id, returned)
        clock.advance(_POLL_SECONDS)
        await _wait_for_ack(screen, "Applied")

        feedback = _static_text(screen, "#palette-result")
        assert f"command {queued.id}" in feedback
        assert f"{task.id} · implementer" in feedback
        assert "guidance queued for the selected role's next turn" in feedback
        assert "Returned result" in feedback
        assert "queued_for_next_turn" in feedback


async def test_palette_task_search_changes_the_editable_target(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    clock = FakeClock(_START)
    context_task = seed_task(connection, clock, title="Detail task")
    selected_task = seed_task(connection, clock, title="Chosen by title")
    commands, queries, queue = _services(connection, reader, clock, tmp_path)
    app = _palette_app(
        reader,
        commands,
        queries,
        _selection_must_not_run,
        initial_context=PaletteRequest(
            task_id=context_task.id,
            role=Role.LEADER.value,
        ),
    )

    async with app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        screen = _screen(app)
        await _open_action(pilot, screen, "message")
        screen.query_one("#task-search", Input).value = "Chosen by title"
        await pilot.pause()
        task_choice = cast("Select[str]", screen.query_one("#task-choice", Select))
        task_choice.focus()
        task_choice.value = selected_task.id
        await pilot.pause()
        assert screen.query_one("#task-id", Input).value == selected_task.id
        screen.query_one("#message", TextArea).text = "Use the deliberately selected task."
        await pilot.click("#palette-submit")
        await _wait_for_ack(screen)

        (queued,) = queue.pending()
        assert queued.command == Say(
            task_id=selected_task.id,
            role=Role.LEADER,
            text="Use the deliberately selected task.",
            command_id=queued.id,
        )


async def test_manual_task_id_edit_stays_authoritative_outside_search(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    clock = FakeClock(_START)
    context_task = seed_task(connection, clock, title="Detail task")
    selected_task = seed_task(connection, clock, title="Other task")
    commands, queries, queue = _services(connection, reader, clock, tmp_path)
    app = _palette_app(
        reader,
        commands,
        queries,
        _selection_must_not_run,
        initial_context=PaletteRequest(
            action="message",
            task_id=context_task.id,
            role=Role.LEADER.value,
        ),
    )

    async with app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        screen = _screen(app)
        screen.query_one("#task-search", Input).value = context_task.title
        await pilot.pause()
        task_id = screen.query_one("#task-id", Input)
        task_choice = cast("Select[str]", screen.query_one("#task-choice", Select))
        task_id.value = selected_task.id
        await wait_until(
            lambda: task_id.value == selected_task.id and task_choice.value == selected_task.id,
            timeout=2,
        )

        task_id.value = "T-999"
        await wait_until(
            lambda: task_id.value == "T-999" and task_choice.value is Select.BLANK,
            timeout=2,
        )
        assert task_id.value == "T-999"

        task_id.value = selected_task.id
        await wait_until(
            lambda: task_id.value == selected_task.id and task_choice.value == selected_task.id,
            timeout=2,
        )
        screen.query_one("#message", TextArea).text = "Use the manually entered task."
        await pilot.click("#palette-submit")
        await _wait_for_ack(screen)

    queued = queue.pending()[0]
    assert queued.command == Say(
        task_id=selected_task.id,
        role=Role.LEADER,
        text="Use the manually entered task.",
        command_id=queued.id,
    )


async def test_palette_task_search_is_bounded_current_and_retains_selected_target(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    clock = FakeClock(_START)
    for index in range(52):
        seed_task(connection, clock, title=f"Older palette task {index}")
    commands, queries, _queue = _services(connection, reader, clock, tmp_path)
    app = _palette_app(reader, commands, queries, _selection_must_not_run)

    async with app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        screen = _screen(app)
        await _open_action(pilot, screen, "message")
        assert "Showing 50 of 52 matches" in _static_text(screen, "#palette-task-count")
        assert screen.query_one("#palette-task-more", Button).display
        screen.query_one("#palette-task-more", Button).focus()
        await pilot.press("enter")
        await wait_until(
            lambda: "Showing 52 of 52 matches" in _static_text(screen, "#palette-task-count"),
            timeout=2,
        )
        assert not screen.query_one("#palette-task-more", Button).display

        arriving_task = seed_task(connection, clock, title="Arrived while palette is open")
        screen.query_one("#task-search", Input).value = "Arrived while palette is open"
        await pilot.pause()
        assert "Showing 1 of 1 matches" in _static_text(screen, "#palette-task-count")
        task_choice = cast("Select[str]", screen.query_one("#task-choice", Select))
        task_choice.focus()
        task_choice.value = arriving_task.id
        await pilot.pause()
        screen.query_one("#task-search", Input).value = "No current match"
        await pilot.pause()

        assert "Showing 0 of 0 matches" in _static_text(screen, "#palette-task-count")
        assert task_choice.value == arriving_task.id
        assert screen.query_one("#task-id", Input).value == arriving_task.id


async def test_palette_serializes_pending_action_and_closes_without_worker_errors(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    clock = FakeClock(_START)
    task = seed_task(connection, clock, title="Pending command target")
    other_task = seed_task(connection, clock, title="Edited command target")
    commands, queries, queue = _services(
        connection,
        reader,
        clock,
        tmp_path,
        engine_up=True,
    )
    app = _palette_app(
        reader,
        commands,
        queries,
        _selection_must_not_run,
        initial_context=PaletteRequest(task_id=task.id, role=Role.LEADER.value),
    )

    async with app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        screen = _screen(app)
        await _open_action(pilot, screen, "message")
        screen.query_one("#message", TextArea).text = "Send once while awaiting acknowledgement."
        await pilot.click("#palette-submit")
        await wait_until(clock.has_pending_sleep, timeout=2)
        assert "Sending" in _static_text(screen, "#palette-result")
        assert f"{task.id} · leader" in _static_text(screen, "#palette-result")
        assert len(queue.pending()) == 1
        assert screen.query_one("#palette-submit", Button).disabled

        screen.query_one("#action-search", Input).value = "message"
        screen.query_one("#task-id", Input).value = other_task.id
        screen.query_one("#role", Select).value = Role.IMPLEMENTER.value
        await pilot.pause()
        assert screen.query_one("#palette-submit", Button).disabled
        await pilot.click("#palette-submit")
        assert len(queue.pending()) == 1
        assert queue.pending()[0].command == Say(
            task_id=task.id,
            role=Role.LEADER,
            text="Send once while awaiting acknowledgement.",
            command_id=queue.pending()[0].id,
        )
        await pilot.press("escape")

    clock.advance(1)
    assert not clock.has_pending_sleep()
    assert len(queue.pending()) == 1


async def test_palette_shutdown_keeps_a_pending_command_without_stale_ui_refresh(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    clock = FakeClock(_START)
    task = seed_task(connection, clock, title="Shutdown with pending command")
    commands, queries, queue = _services(
        connection,
        reader,
        clock,
        tmp_path,
        engine_up=True,
    )
    app = _palette_app(
        reader,
        commands,
        queries,
        _selection_must_not_run,
        initial_context=PaletteRequest(task_id=task.id, role=Role.LEADER.value),
    )

    async with app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        screen = _screen(app)
        await _open_action(pilot, screen, "message")
        screen.query_one("#message", TextArea).text = "Keep the durable command on shutdown."
        await pilot.click("#palette-submit")
        await wait_until(clock.has_pending_sleep, timeout=2)
        assert len(queue.pending()) == 1

    assert len(queue.pending()) == 1


async def test_palette_keeps_acknowledgement_timeout_pending_with_current_engine_state(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    clock = FakeClock(_START)
    task = seed_task(connection, clock, title="Acknowledgement wait")
    commands, queries, queue = _services(
        connection,
        reader,
        clock,
        tmp_path,
        engine_up=True,
    )
    app = _palette_app(
        reader,
        commands,
        queries,
        _selection_must_not_run,
        initial_context=PaletteRequest(
            action="message",
            task_id=task.id,
            role=Role.LEADER.value,
        ),
    )

    async with app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        screen = _screen(app)
        screen.query_one("#message", TextArea).text = "Keep waiting for the engine."
        await pilot.click("#palette-submit")
        await wait_until(clock.has_pending_sleep, timeout=2)
        queued = queue.pending()[0]
        clock.advance(COMMAND_ACK_WAIT_SECONDS)
        await _wait_for_ack(screen)

        feedback = _static_text(screen, "#palette-result")
        assert f"command {queued.id}" in feedback
        assert f"{task.id} · leader" in feedback
        assert "acknowledgement wait elapsed" in feedback
        assert "engine is currently running" in feedback
        assert "Rejected" not in feedback
        outcome = queue.status(queued.id)
        assert outcome is not None
        assert outcome.status is CommandStatus.PENDING


@pytest.mark.parametrize(("terminal", "expected"), [("done", "Applied"), ("rejected", "Rejected")])
async def test_palette_observes_outcome_after_ack_wait_without_form_edits(
    connection: Connection,
    database: Database,
    reader: StateReader,
    tmp_path: Path,
    *,
    terminal: str,
    expected: str,
) -> None:
    clock = FakeClock(_START)
    task = seed_task(connection, clock, title="Observed pending outcome")
    commands, queries, queue = _services(
        connection,
        reader,
        clock,
        tmp_path,
        engine_up=True,
    )
    app = _palette_app(
        reader,
        commands,
        queries,
        _selection_must_not_run,
        initial_context=PaletteRequest(
            action="message",
            task_id=task.id,
            role=Role.LEADER.value,
        ),
    )

    async with app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        screen = _screen(app)
        screen.query_one("#message", TextArea).text = "Keep this exact pending form."
        await pilot.click("#palette-submit")
        await wait_until(clock.has_pending_sleep, timeout=2)
        pending = queue.pending()[0]
        clock.advance(COMMAND_ACK_WAIT_SECONDS)
        await _wait_for_ack(screen)
        assert screen.query_one("#palette-submit", Button).disabled

        engine = database.connect()
        try:
            engine_queue = CommandQueue(engine, clock)
            if terminal == "done":
                engine_queue.mark_done(pending.id, {"effect": "message queued"})
            else:
                engine_queue.mark_rejected(pending.id, {"error": "late engine rejection"})
        finally:
            engine.close()
        await wait_until(
            lambda: (
                expected in _static_text(screen, "#palette-result")
                and not screen.query_one("#palette-submit", Button).disabled
            ),
            timeout=2,
        )
        assert f"command {pending.id}" in _static_text(screen, "#palette-result")
        assert len(queue.pending()) == 0
        if terminal == "done":
            assert "guidance queued for the selected role's next turn" in _static_text(
                screen, "#palette-result"
            )
        else:
            assert "late engine rejection" in _static_text(screen, "#palette-result")


async def test_pending_form_guards_survive_form_switches_and_palette_reopen(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    clock = FakeClock(_START)
    task = seed_task(connection, clock, title="Multiple pending forms")
    commands, queries, queue = _services(connection, reader, clock, tmp_path)
    context = PaletteRequest(task_id=task.id, role=Role.LEADER.value)
    first_app = _palette_app(
        reader, commands, queries, _selection_must_not_run, initial_context=context
    )

    async with first_app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        screen = _screen(first_app)
        await _open_action(pilot, screen, "message")
        screen.query_one("#message", TextArea).text = "Do not send twice."
        await pilot.click("#palette-submit")
        await wait_until(lambda: len(queue.pending()) == 1, timeout=2)
        say_id = queue.pending()[0].id

        await _open_action(pilot, screen, "pause")
        await _set_target(pilot, screen, task.id)
        await pilot.click("#palette-submit")
        await wait_until(lambda: len(queue.pending()) == 2, timeout=2)
        pause_id = queue.pending()[1].id

        await _open_action(pilot, screen, "message")
        assert screen.query_one("#palette-submit", Button).disabled
        await pilot.click("#palette-submit")
        assert [item.id for item in queue.pending()] == [say_id, pause_id]
        await pilot.press("escape")

    reopened = _palette_app(
        reader,
        commands,
        queries,
        _selection_must_not_run,
        initial_context=PaletteRequest(
            action="message",
            task_id=task.id,
            role=Role.LEADER.value,
        ),
    )
    async with reopened.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        screen = _screen(reopened)
        screen.query_one("#message", TextArea).text = "Do not send twice."
        await pilot.pause()
        assert screen.query_one("#palette-submit", Button).disabled
        assert f"command {say_id}" in _static_text(screen, "#palette-result")
        assert [item.id for item in queue.pending()] == [say_id, pause_id]


async def test_pending_submit_recovers_after_store_allocates_its_task_id(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    clock = FakeClock(_START)
    commands, queries, queue = _services(connection, reader, clock, tmp_path)
    request = "One pending task submission"
    first_app = _palette_app(reader, commands, queries, _selection_must_not_run)

    async with first_app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        screen = _screen(first_app)
        screen.query_one("#request", TextArea).text = request
        await pilot.click("#palette-submit")
        await wait_until(lambda: len(queue.pending()) == 1, timeout=2)
        queued = queue.pending()[0]
        assert isinstance(queued.command, Submit)
        assert queued.command.task_id is not None
        assert queued.command.title == request
        await pilot.press("escape")

    reopened = _palette_app(reader, commands, queries, _selection_must_not_run)
    async with reopened.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        screen = _screen(reopened)
        screen.query_one("#request", TextArea).text = request
        await pilot.pause()
        assert screen.query_one("#palette-submit", Button).disabled
        assert f"command {queued.id}" in _static_text(screen, "#palette-result")
        await pilot.click("#palette-submit")
        assert [item.id for item in queue.pending()] == [queued.id]


async def test_palette_revalidates_task_state_before_queueing(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    clock = FakeClock(_START)
    task = seed_task(connection, clock)
    commands, queries, queue = _services(connection, reader, clock, tmp_path)
    app = _palette_app(
        reader,
        commands,
        queries,
        _selection_must_not_run,
        initial_context=PaletteRequest(action="prioritize", task_id=task.id),
    )

    async with app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        screen = _screen(app)
        assert not screen.query_one("#palette-submit", Button).disabled
        TaskStore(connection, clock).apply_transition(
            task.id,
            Transition.stay(task, step=task.step, status=Status.DONE),
        )
        await pilot.click("#palette-submit")
        await wait_until(
            lambda: screen.query_one("#palette-submit", Button).disabled,
            timeout=2,
        )
        assert screen.query_one("#palette-submit", Button).disabled
    assert not queue.pending()


async def test_implicit_role_default_tracks_task_selection_until_explicitly_changed(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    clock = FakeClock(_START)
    leader_task = seed_task(connection, clock, title="Leader default")
    implementer_task = seed_task(
        connection,
        clock,
        title="Implementer default",
        mode=Mode.IMPLEMENT,
    )
    TaskStore(connection, clock).apply_transition(
        implementer_task.id,
        Transition.stay(implementer_task, step=Step.IMPLEMENT, status=Status.RUNNING),
    )
    commands, queries, _queue = _services(connection, reader, clock, tmp_path)
    app = _palette_app(
        reader,
        commands,
        queries,
        _selection_must_not_run,
        initial_context=PaletteRequest(
            action="message",
            task_id=leader_task.id,
        ),
    )

    async with app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        screen = _screen(app)
        role = cast("Select[str]", screen.query_one("#role", Select))
        task_id = screen.query_one("#task-id", Input)
        assert role.value == Role.LEADER.value

        screen.query_one("#task-search", Input).value = implementer_task.title
        await pilot.pause()
        task_choice = cast("Select[str]", screen.query_one("#task-choice", Select))
        task_choice.focus()
        task_choice.value = implementer_task.id
        await wait_until(
            lambda: task_id.value == implementer_task.id and role.value == Role.IMPLEMENTER.value,
            timeout=2,
        )

        role.focus()
        role.value = Role.LEADER.value
        await pilot.pause()
        task_id.value = leader_task.id
        await wait_until(lambda: task_id.value == leader_task.id, timeout=2)
        task_id.value = implementer_task.id
        await pilot.pause()
        assert role.value == Role.LEADER.value


async def _selection_must_not_run(pane_id: str) -> None:
    del pane_id
    raise AssertionError


async def _enter_text(pilot: Pilot[None], text: str) -> None:
    await pilot.press(*("enter" if character == "\n" else character for character in text))


def _static_text(screen: PaletteScreen, selector: str) -> str:
    rendered = cast("Text", screen.query_one(selector, Static).render())
    return rendered.plain


async def test_palette_submit_queues_a_typed_command_and_shows_its_ack(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    clock = FakeClock(_START)
    commands, queries, queue = _services(connection, reader, clock, tmp_path)
    app = _palette_app(reader, commands, queries, _selection_must_not_run)

    async with app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        screen = _screen(app)
        await _open_action(pilot, screen, "submit")
        request = screen.query_one("#request", TextArea)
        for _ in range(12):
            if request.has_focus:
                break
            await pilot.press("tab")
        assert request.has_focus
        await _enter_text(pilot, "first line\nsecond line")
        assert screen.query_one("#request", TextArea).text == "first line\nsecond line"
        await pilot.click("#palette-advanced-toggle")
        screen.query_one("#mode", Select).value = Mode.IMPLEMENT.value
        screen.query_one("#submission-priority", Input).value = "17"
        screen.query_one("#dependencies", Input).value = "T-8, T-9"
        screen.query_one("#leader-rung", Input).value = "codex:sol:high"
        screen.query_one("#implementer-rung", Input).value = "codex:sol:high"
        screen.query_one("#title", Input).value = "Palette"
        await pilot.click("#palette-submit")
        await _wait_for_ack(screen)

        queued = queue.pending()
        assert len(queued) == 1
        assert isinstance(queued[0].command, Submit)
        assert queued[0].command.request == "first line\nsecond line"
        assert queued[0].command.title == "Palette"
        assert queued[0].command.mode is Mode.IMPLEMENT
        assert queued[0].command.priority == 17
        assert queued[0].command.depends_on == ["T-8", "T-9"]
        assert queued[0].command.leader == _LIVE_RUNG
        assert queued[0].command.implementer == _LIVE_RUNG
        assert "Pending" in _static_text(screen, "#palette-result")


async def test_palette_prioritize_queues_the_selected_priority(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    clock = FakeClock(_START)
    task = seed_task(connection, clock)
    commands, queries, queue = _services(connection, reader, clock, tmp_path)
    app = _palette_app(reader, commands, queries, _selection_must_not_run)

    async with app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        screen = _screen(app)
        await _open_action(pilot, screen, "prioritize")
        await _set_target(pilot, screen, task.id)
        screen.query_one("#priority", Input).value = "17"
        await pilot.click("#palette-submit")
        await _wait_for_ack(screen)

        queued = queue.pending()
        assert len(queued) == 1
        assert queued[0].command == Prioritize(task_id=task.id, priority=17)
        feedback = _static_text(screen, "#palette-result")
        assert "Pending" in feedback
        assert "engine was stopped" in feedback
        assert "acknowledgement wait elapsed" not in feedback


@pytest.mark.parametrize(
    ("reason", "kind", "expected_type", "rung_text"),
    [
        (EscalationReason.DECISION_REQUIRED, "answer", AnswerResolution, ""),
        (EscalationReason.AGENT_ERROR, "retry", RetryResolution, "codex:sol:high"),
        (EscalationReason.ITERATIONS_EXHAUSTED, "accept", AcceptResolution, ""),
        (EscalationReason.DECISION_REQUIRED, "cancel", CancelResolution, ""),
    ],
)
async def test_palette_routes_structured_escalation_to_validated_resolution(
    connection: Connection,
    database: Database,
    tmp_path: Path,
    request: pytest.FixtureRequest,
    *,
    reason: EscalationReason,
    kind: str,
    expected_type: type[object],
    rung_text: str,
) -> None:
    clock = FakeClock(_START)
    reader = StateReader(database, clock)
    request.addfinalizer(reader.close)
    task = seed_task(connection, clock, title="Structured decision")
    escalation, packet = _seed_escalation(connection, clock, task.id, tmp_path, reason)
    commands, queries, queue = _services(connection, reader, clock, tmp_path)
    app = _palette_app(
        reader,
        commands,
        queries,
        _selection_must_not_run,
        initial_context=PaletteRequest(action=f"resolve {escalation.id}"),
    )

    async with app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        screen = _screen(app)
        assert cast("Select[str]", screen.query_one("#escalation", Select)).value == escalation.id
        summary = _static_text(screen, "#palette-escalation-summary")
        assert "Which behavior should remain?" in summary
        assert "★ Keep existing behavior" in summary
        assert "Preserve the current call pattern." in summary
        assert "Allowed engine resolutions" in summary
        assert f"{kind} ·" in summary
        assert "preserve its worktree" in summary
        assert _static_text(screen, "#palette-original-packet") == packet
        assert screen.query_one("#palette-evidence-original", Collapsible).collapsed
        assert "turn completed: implementation" in _static_text(
            screen, "#palette-evidence-timeline"
        )
        assert "Implementation report evidence." in _static_text(
            screen, "#palette-evidence-reports"
        )
        assert "Recorded git facts." in _static_text(screen, "#palette-evidence-facts")
        assert "Recent stream tail." in _static_text(screen, "#palette-evidence-recent-signals")
        assert "Leader session evidence." in _static_text(screen, "#palette-evidence-sessions")

        screen.query_one("#resolution", Select).value = kind
        if kind == "answer":
            assert screen.query_one("#palette-submit", Button).disabled
            screen.query_one("#resolution-text", TextArea).text = "Keep the current behavior."
        if rung_text:
            screen.query_one("#resolution-rung", Input).value = rung_text
        await pilot.click("#palette-submit")
        await _wait_for_ack(screen)

        queued = queue.pending()
        assert len(queued) == 1
        assert isinstance(queued[0].command, Resolve)
        assert queued[0].command.escalation_id == escalation.id
        assert isinstance(queued[0].command.resolution, expected_type)
        if kind == "answer":
            assert queued[0].command.resolution == AnswerResolution(
                text="Keep the current behavior."
            )
        if kind == "retry":
            assert isinstance(queued[0].command.resolution, RetryResolution)
            assert queued[0].command.resolution.rung == Rung(
                agent="codex", model="sol", effort="high"
            )
        assert "Pending" in _static_text(screen, "#palette-result")


async def test_palette_keeps_durable_resolution_visible_when_packet_is_unavailable(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    clock = FakeClock(_START)
    task = seed_task(connection, clock, title="Missing packet")
    escalation, _ = _seed_escalation(
        connection,
        clock,
        task.id,
        tmp_path,
        EscalationReason.DECISION_REQUIRED,
        write_packet=False,
    )
    commands, queries, queue = _services(connection, reader, clock, tmp_path)
    app = _palette_app(
        reader,
        commands,
        queries,
        _selection_must_not_run,
        initial_context=PaletteRequest(action=f"resolve {escalation.id}"),
    )

    async with app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        screen = _screen(app)
        summary = _static_text(screen, "#palette-escalation-summary")
        assert escalation.id in summary
        assert escalation.headline in summary
        assert "answer" in summary
        assert "cancel" in summary
        assert "Evidence error:" in _static_text(screen, "#palette-evidence-error")
        assert "missing" in _static_text(screen, "#palette-evidence-error")
        assert not screen.query_one("#palette-evidence-original", Collapsible).display
        assert queue.pending() == []
        assert TaskStore(connection, clock).require(task.id).status is Status.ESCALATED
        current = EscalationStore(connection, clock).get(escalation.id)
        assert current is not None
        assert current.status.value == "open"


async def test_palette_preserves_original_and_readable_sections_of_malformed_packet(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    clock = FakeClock(_START)
    task = seed_task(connection, clock, title="Malformed packet")
    escalation, packet = _seed_escalation(
        connection, clock, task.id, tmp_path, EscalationReason.DECISION_REQUIRED
    )
    section_start = packet.index("## Agent alternatives")
    section_end = packet.index("\n## Options", section_start)
    malformed = (
        packet[:section_start]
        + "## Agent alternatives\n\n{ malformed report data }\n"
        + packet[section_end:]
    )
    await asyncio.to_thread(Path(escalation.packet_path).write_text, malformed)
    commands, queries, queue = _services(connection, reader, clock, tmp_path)
    app = _palette_app(
        reader,
        commands,
        queries,
        _selection_must_not_run,
        initial_context=PaletteRequest(action=f"resolve {escalation.id}"),
    )

    async with app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        screen = _screen(app)
        assert escalation.id in _static_text(screen, "#palette-escalation-summary")
        assert "answer" in _static_text(screen, "#palette-escalation-summary")
        assert "cancel" in _static_text(screen, "#palette-escalation-summary")
        assert "malformed" in _static_text(screen, "#palette-evidence-error")
        assert "Recorded git facts." in _static_text(screen, "#palette-evidence-facts")
        assert _static_text(screen, "#palette-original-packet") == malformed
        assert queue.pending() == []


async def test_palette_disables_an_escalation_after_another_client_cancels_it(
    connection: Connection,
    database: Database,
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    clock = FakeClock(_START)
    reader = StateReader(database, clock)
    request.addfinalizer(reader.close)
    task = seed_task(connection, clock, title="External cancellation")
    escalation, _ = _seed_escalation(
        connection, clock, task.id, tmp_path, EscalationReason.DECISION_REQUIRED
    )
    commands, queries, _ = _services(connection, reader, clock, tmp_path)
    app = _palette_app(
        reader,
        commands,
        queries,
        _selection_must_not_run,
        initial_context=PaletteRequest(action=f"resolve {escalation.id}"),
    )

    async with app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        screen = _screen(app)
        guidance = "Keep this guidance for copying."
        screen.query_one("#resolution-text", TextArea).text = guidance
        await pilot.pause()
        assert not screen.query_one("#palette-submit", Button).disabled
        await wait_until(clock.has_pending_sleep, timeout=2)
        engine = database.connect()
        try:
            external = await _enqueue_and_cancel(engine, clock, task.id, tmp_path)
            assert external.status.value == "done"
        finally:
            engine.close()
        clock.advance(DASHBOARD_CHANGE_POLL_SECONDS)
        await wait_until(
            lambda: (
                screen.query_one("#palette-submit", Button).disabled
                and "was resolved" in _static_text(screen, "#palette-escalation-status")
            ),
            timeout=2,
        )
        assert screen.query_one("#resolution-text", TextArea).text == guidance
        assert queries.command_history().total_count == 2


async def test_palette_shows_engine_rejection_after_resolution_race_without_resubmitting(
    connection: Connection,
    database: Database,
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    clock = FakeClock(_START)
    reader = StateReader(database, clock)
    request.addfinalizer(reader.close)
    task = seed_task(connection, clock, title="Resolution race")
    escalation, _ = _seed_escalation(
        connection, clock, task.id, tmp_path, EscalationReason.DECISION_REQUIRED
    )
    commands, queries, queue = _services(connection, reader, clock, tmp_path)
    app = _palette_app(
        reader,
        commands,
        queries,
        _selection_must_not_run,
        initial_context=PaletteRequest(action=f"resolve {escalation.id}"),
    )

    async with app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        screen = _screen(app)
        guidance = "Answer queued before cancellation."
        screen.query_one("#resolution-text", TextArea).text = guidance
        await pilot.pause()
        await wait_until(clock.has_pending_sleep, timeout=2)
        await pilot.click("#palette-submit")
        await _wait_for_ack(screen)
        submitted = queue.pending()
        assert len(submitted) == 1
        assert isinstance(submitted[0].command, Resolve)
        await wait_until(clock.has_pending_sleep, timeout=2)
        engine = database.connect()
        try:
            external = await _enqueue_and_cancel(engine, clock, task.id, tmp_path)
            resolution_command = next(
                item for item in CommandQueue(engine, clock).pending() if item.id == submitted[0].id
            )
            assert isinstance(resolution_command.command, Resolve)
            with pytest.raises(CommandRejectedError, match="already resolved"):
                await ResolveHandler(_handler_context(engine, clock, tmp_path)).handle(
                    resolution_command.command
                )
            CommandQueue(engine, clock).mark_rejected(
                resolution_command.id,
                {"error": f"{escalation.id} is already resolved"},
            )
            assert external.status.value == "done"
        finally:
            engine.close()
        clock.advance(DASHBOARD_CHANGE_POLL_SECONDS)
        await wait_until(
            lambda: "Rejected" in _static_text(screen, "#palette-result"),
            timeout=2,
        )
        assert "already resolved" in _static_text(screen, "#palette-result")
        assert screen.query_one("#resolution-text", TextArea).text == guidance
        assert queries.command_history().total_count == 3
        assert queue.pending() == []


async def test_palette_takeover_queues_a_task_role_command(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    clock = FakeClock(_START)
    task = seed_task(connection, clock)
    TaskStore(connection, clock).set_panes(task.id, {Role.LEADER: "%7"})
    _seed_live_session(connection, clock, task.id)
    commands, queries, queue = _services(connection, reader, clock, tmp_path)
    app = _palette_app(reader, commands, queries, _selection_must_not_run)

    async with app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        screen = _screen(app)
        await _open_action(pilot, screen, "take-over")
        await _set_target(pilot, screen, task.id)
        await _set_role(pilot, screen, Role.LEADER)
        await pilot.click("#palette-submit")
        await _wait_for_ack(screen)

        queued = queue.pending()
        assert len(queued) == 1
        assert queued[0].command == TakeOver(
            task_id=task.id, role=Role.LEADER, command_id=queued[0].id
        )
        assert "Pending" in _static_text(screen, "#palette-result")


async def test_completed_takeover_selects_the_role_pane_and_its_window(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
    tmux_server: TmuxServer,
) -> None:
    clock = FakeClock(_START)
    task = seed_task(connection, clock)
    tmux = Tmux(tmux_server.runner, socket=tmux_server.socket_name, env=tmux_server.env)
    pane_id = await tmux.new_window(
        exact_target(tmux_server.session),
        name="task",
        cwd=tmp_path,
    )
    inactive_pane_id = await tmux.split_window(pane_id, axis="h", size_pct=50)
    other_pane_id = await tmux.new_window(
        exact_target(tmux_server.session),
        name="other",
        cwd=tmp_path,
    )
    other_active_pane_id = await tmux.split_window(other_pane_id, axis="h", size_pct=50)
    await tmux.select_pane(other_active_pane_id)
    TaskStore(connection, clock).set_panes(task.id, {Role.IMPLEMENTER: pane_id})
    runs = RunStore(connection, clock)
    run = runs.start(task.id, Role.IMPLEMENTER, _LIVE_RUNG)
    runs.set_session(run.id, "session-current", "resume-current")
    commands, queries, queue = _services(connection, reader, clock, tmp_path, engine_up=True)
    app = _palette_app(reader, commands, queries, tmux.select_pane)

    async with app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        screen = _screen(app)
        await _open_action(pilot, screen, "take-over")
        await _set_target(pilot, screen, task.id)
        await _set_role(pilot, screen, Role.IMPLEMENTER)
        await pilot.click("#palette-submit")
        await wait_until(clock.has_pending_sleep, timeout=2)
        queued = queue.pending()
        assert len(queued) == 1
        queue.mark_done(queued[0].id, {"task_id": task.id})
        clock.advance(_POLL_SECONDS)
        await _wait_for_ack(screen, "Applied")

    session_target = exact_target(tmux_server.session)
    windows = await tmux_server.run(
        "list-windows",
        "-t",
        session_target,
        "-F",
        "#{window_name} #{window_active}",
    )
    assert "task 1" in windows.stdout
    assert "other 0" in windows.stdout
    panes = await tmux_server.run(
        "list-panes",
        "-t",
        exact_target(tmux_server.session) + ":task",
        "-F",
        "#{pane_id} #{pane_active}",
    )
    assert f"{pane_id} 1" in panes.stdout.splitlines()
    assert f"{inactive_pane_id} 0" in panes.stdout.splitlines()


async def test_popup_pane_context_selects_its_recorded_task_and_role(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
    tmux_server: TmuxServer,
) -> None:
    clock = FakeClock(_START)
    tmux = Tmux(tmux_server.runner, socket=tmux_server.socket_name, env=tmux_server.env)
    pane_id = await tmux.new_window(
        exact_target(tmux_server.session),
        name="task",
        cwd=tmp_path,
    )
    stale_task = seed_task(connection, clock, title="Closed popup task")
    TaskStore(connection, clock).apply_transition(
        stale_task.id,
        Transition.stay(stale_task, step=stale_task.step, status=Status.DONE),
    )
    TaskStore(connection, clock).set_panes(stale_task.id, {Role.LEADER: pane_id})
    task = seed_task(connection, clock, title="Popup context")
    TaskStore(connection, clock).set_panes(task.id, {Role.IMPLEMENTER: pane_id})
    commands, queries, queue = _services(connection, reader, clock, tmp_path)
    originating_pane = await tmux.display(pane_id, FORMAT_PANE_ID)
    task_role = queries.task_role_for_pane(originating_pane)
    assert task_role is not None
    assert task_role == (task.id, Role.IMPLEMENTER)
    context_task_id, role = task_role
    app = _palette_app(
        reader,
        commands,
        queries,
        tmux.select_pane,
        initial_context=PaletteRequest(
            action="message",
            task_id=context_task_id,
            role=role.value,
        ),
    )

    async with app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        screen = _screen(app)
        assert screen.query_one("#task-id", Input).value == task.id
        assert cast("Select[str]", screen.query_one("#role", Select)).value == (
            Role.IMPLEMENTER.value
        )
        screen.query_one("#message", TextArea).text = "Continue the implementer turn."
        await pilot.click("#palette-submit")
        await _wait_for_ack(screen)

    queued = queue.pending()[0]
    assert queued.command == Say(
        task_id=task.id,
        role=Role.IMPLEMENTER,
        text="Continue the implementer turn.",
        command_id=queued.id,
    )


async def test_rejected_takeover_never_selects_the_role_pane(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    clock = FakeClock(_START)
    task = seed_task(connection, clock)
    TaskStore(connection, clock).set_panes(task.id, {Role.LEADER: "%7"})
    _seed_live_session(connection, clock, task.id)
    commands, queries, queue = _services(connection, reader, clock, tmp_path, engine_up=True)
    app = _palette_app(reader, commands, queries, _selection_must_not_run)

    async with app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        screen = _screen(app)
        await _open_action(pilot, screen, "take-over")
        await _set_target(pilot, screen, task.id)
        await pilot.click("#palette-submit")
        await wait_until(clock.has_pending_sleep, timeout=2)
        queued = queue.pending()
        assert len(queued) == 1
        queue.mark_rejected(queued[0].id, {"message": "take over unavailable"})
        clock.advance(_POLL_SECONDS)
        await _wait_for_ack(screen, "Rejected")

        feedback = _static_text(screen, "#palette-result")
        assert f"command {queued[0].id}" in feedback
        assert f"{task.id} · leader" in feedback
        assert "Rejected" in feedback
        assert "take over unavailable" in feedback


@pytest.mark.parametrize(
    ("task_exists", "expected"),
    [(True, "role pane is unavailable"), (False, "task no longer exists")],
)
async def test_palette_disables_takeover_without_task_or_role_pane(
    connection: Connection, reader: StateReader, tmp_path: Path, task_exists: bool, expected: str
) -> None:
    clock = FakeClock(_START)
    task_id = seed_task(connection, clock).id if task_exists else "T-404"
    commands, queries, queue = _services(connection, reader, clock, tmp_path, engine_up=True)
    app = _palette_app(reader, commands, queries, _selection_must_not_run)

    async with app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        screen = _screen(app)
        await _open_action(pilot, screen, "take-over")
        await _set_target(pilot, screen, task_id)
        assert screen.query_one("#palette-submit", Button).disabled
        assert expected in _static_text(screen, "#palette-action-state")
        assert not queue.pending()


async def test_done_takeover_keeps_ack_with_pane_selection_error(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    clock = FakeClock(_START)
    task = seed_task(connection, clock)
    TaskStore(connection, clock).set_panes(task.id, {Role.LEADER: "%not-a-real-pane"})
    _seed_live_session(connection, clock, task.id)
    commands, queries, queue = _services(connection, reader, clock, tmp_path, engine_up=True)

    async def pane_selection_fails(pane_id: str) -> None:
        message = f"could not select {pane_id}"
        raise ConfigError(message)

    app = _palette_app(reader, commands, queries, pane_selection_fails)

    async with app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        screen = _screen(app)
        await _open_action(pilot, screen, "take-over")
        await _set_target(pilot, screen, task.id)
        await pilot.click("#palette-submit")
        await wait_until(clock.has_pending_sleep, timeout=2)
        queued = queue.pending()
        assert len(queued) == 1
        queue.mark_done(queued[0].id, {"task_id": task.id})
        clock.advance(_POLL_SECONDS)
        await wait_until(
            lambda: (
                "Applied" in _static_text(screen, "#palette-result")
                and "could not select %not-a-real-pane" in _static_text(screen, "#palette-result")
            ),
            timeout=2,
        )


async def test_palette_pause_now_queues_immediate_pause(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    clock = FakeClock(_START)
    task = seed_task(connection, clock)
    commands, queries, queue = _services(connection, reader, clock, tmp_path)
    app = _palette_app(reader, commands, queries, _selection_must_not_run)

    async with app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        screen = _screen(app)
        await _open_action(pilot, screen, "pause-now")
        await _set_target(pilot, screen, task.id)
        await pilot.click("#palette-submit")
        await _wait_for_ack(screen)

        queued = queue.pending()
        assert len(queued) == 1
        assert queued[0].command == Pause(task_id=task.id, now=True)


@pytest.mark.parametrize(
    ("route", "expected"),
    [("resolve E-404", "E-404 is not open"), ("make-it-up", "unknown palette action route")],
)
async def test_palette_preserves_invalid_and_missing_resolve_routes(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
    route: str,
    expected: str,
) -> None:
    clock = FakeClock(_START)
    commands, queries, queue = _services(connection, reader, clock, tmp_path)
    app = _palette_app(reader, commands, queries, _selection_must_not_run)

    async with app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        await app.pop_screen()
        await app.push_screen(
            PaletteScreen(
                commands,
                queries,
                _selection_must_not_run,
                on_change=reader.watch,
                initial_action=route,
            )
        )
        await pilot.pause()
        screen = _screen(app)
        assert screen.query_one("#palette-submit", Button).disabled
        assert expected in _static_text(screen, "#palette-result")
        assert not queue.pending()
