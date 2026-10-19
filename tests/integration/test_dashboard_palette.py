from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import pytest
from textual.widgets import Button, Input, Select, Static, TextArea
from textual.widgets._select import SelectOverlay

from shikiru.agents.contracts import Rung
from shikiru.dashboard.contract import PaletteRequest
from shikiru.dashboard.palette import SUBMIT_ACTION_LABELS, PaletteScreen
from shikiru.domain.commands import (
    Cancel,
    CommandStatus,
    HandBack,
    IdentifiedCommand,
    Pause,
    Prioritize,
    Resume,
    Retry,
    Say,
    SetRung,
    Submit,
    TakeOver,
)
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import Hold, Mode, Role, Status
from shikiru.errors import ConfigError
from shikiru.operator.commands import (
    COMMAND_ACK_WAIT_SECONDS,
    COMMAND_POLL_SECONDS,
    OperatorCommands,
)
from shikiru.operator.queries import TASK_ACTION_ROLES
from shikiru.store.commands import CommandQueue
from shikiru.store.runs import RunStore
from shikiru.store.tasks import TaskStore
from shikiru.tmux.client import FORMAT_PANE_ID, Tmux, exact_target
from tests.support.actions import (
    ScreenHost,
    actions_screen,
    open_action,
    popup_screen,
    row_line,
)
from tests.support.cells import (
    CYAN_BOLD,
    DISABLED_KEY,
    GREY,
    rendered_lines,
    rendered_select_label,
)
from tests.support.clock import FakeClock
from tests.support.escalations import (
    operator_services,
)
from tests.support.store import seed_task
from tests.support.waiting import (
    ACTION_CONFIRMATION_EXPIRY_SECONDS,
    ACTION_CONFIRMATION_SECONDS,
    UI_WAIT_SECONDS,
    wait_until,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

    from rich.text import Text
    from textual.pilot import Pilot

    from shikiru.dashboard.actions_list import ActionsListScreen
    from shikiru.dashboard.palette_app import PaletteApp
    from shikiru.domain.commands import Command
    from shikiru.operator.queries import OperatorQueries
    from shikiru.store.database import Database
    from shikiru.store.reader import StateReader
    from tests.support.store import Connection
    from tests.support.tmux import TmuxServer


_START = datetime(2026, 1, 1, tzinfo=UTC)
_POLL_SECONDS = 0.25
_APP_SIZE = (120, 32)
_TINY_APP_SIZE = (20, 8)
_LIVE_RUNG = Rung(agent="codex", model="sol", effort="high")
_NATIVE_VISUAL_REVIEW_DIR_ENV = "SHIKIRU_NATIVE_VISUAL_REVIEW_DIR"
_CONFIRMED_GLYPH = "\N{CHECK MARK}"


def _seed_live_session(
    connection: Connection, clock: FakeClock, task_id: str, role: Role = Role.LEADER
) -> int:
    runs = RunStore(connection, clock)
    run = runs.start(task_id, role, _LIVE_RUNG)
    runs.set_session(run.id, "session-current", "resume-current")
    return run.id


def _screen(app: ScreenHost) -> PaletteScreen:
    return cast("PaletteScreen", app.screen)


def _palette_app(
    reader: StateReader,
    commands: OperatorCommands,
    queries: OperatorQueries,
    pane_selector: Callable[[str], Awaitable[None]],
    *,
    initial_context: PaletteRequest,
    action_confirmation_seconds: float = ACTION_CONFIRMATION_SECONDS,
) -> ScreenHost:
    return ScreenHost(
        popup_screen(
            commands,
            queries,
            reader,
            pane_selector,
            initial_context,
            action_confirmation_seconds=action_confirmation_seconds,
        )
    )


async def _wait_for_confirmation(screen: PaletteScreen, *, shown: bool = True) -> None:
    await wait_until(
        lambda: (
            (_CONFIRMED_GLYPH in str(screen.query_one("#palette-submit", Button).label)) is shown
        ),
        timeout=UI_WAIT_SECONDS,
    )


def _hint_key_style(button: Button, text: str) -> set[tuple[bool, str | None]]:
    for cells in rendered_lines(button):
        plain = "".join(char for char, _ in cells)
        start = plain.find(text)
        if start >= 0:
            return {style for _, style in cells[start : start + len(text)]}
    raise AssertionError(text)


async def _wait_for_hint_key(button: Button, style: tuple[bool, str | None]) -> None:
    await wait_until(lambda: _hint_key_style(button, "Ctrl+S") == {style}, timeout=UI_WAIT_SECONDS)


async def _wait_for_ack(screen: PaletteScreen, status: str = "Pending") -> None:
    await wait_until(
        lambda: (
            status in _static_text(screen, "#palette-result")
            and screen.query_one("#palette-result-scroll").region.y
            >= screen.query_one("#palette-form").region.y
            and screen.query_one("#palette-result-scroll").region.bottom
            <= screen.query_one("#palette-form").region.bottom
        ),
        timeout=UI_WAIT_SECONDS,
    )


async def test_palette_context_targets_message_and_rung_without_id_entry(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    clock = FakeClock(_START)
    task = seed_task(connection, clock, title="Context task")
    commands, queries, queue = operator_services(connection, reader, clock, tmp_path)
    context = PaletteRequest(action="message", task_id=task.id, role=Role.IMPLEMENTER.value)
    app = _palette_app(reader, commands, queries, _selection_must_not_run, initial_context=context)

    async with app.run_test(size=_APP_SIZE) as pilot:
        screen = _screen(app)
        role = cast("Select[str]", screen.query_one("#role", Select))
        await wait_until(
            lambda: rendered_select_label(role) == "Implementer", timeout=UI_WAIT_SECONDS
        )
        assert role.value == Role.IMPLEMENTER.value
        screen.query_one("#message", TextArea).text = "Check the failing integration path."
        await pilot.click("#palette-submit")
        await _wait_for_confirmation(screen)
        assert queue.pending()[0].command == Say(
            task_id=task.id,
            role=Role.IMPLEMENTER,
            text="Check the failing integration path.",
            command_id=queue.pending()[0].id,
        )

    rung_app = _palette_app(
        reader,
        commands,
        queries,
        _selection_must_not_run,
        initial_context=replace(context, action="rung"),
    )
    async with rung_app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        screen = _screen(rung_app)
        rung = screen.query_one("#rung", Input)
        await wait_until(lambda: rung_app.focused is rung, timeout=UI_WAIT_SECONDS)
        rung.value = "codex:sol:high"
        await wait_until(
            lambda: not screen.query_one("#palette-submit", Button).disabled,
            timeout=UI_WAIT_SECONDS,
        )
        await pilot.press("enter")
        await wait_until(lambda: len(queue.pending()) == 2, timeout=UI_WAIT_SECONDS)
        assert queue.pending()[1].command == SetRung(
            task_id=task.id,
            role=Role.IMPLEMENTER,
            rung=_LIVE_RUNG,
        )


@pytest.mark.usefixtures("colour_terminal")
async def test_applied_message_feedback_keeps_next_turn_guidance(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    clock = FakeClock(_START)
    task = seed_task(connection, clock, title="Guidance acknowledgement")
    commands, queries, queue = operator_services(
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
        action_confirmation_seconds=ACTION_CONFIRMATION_EXPIRY_SECONDS,
    )

    async with app.run_test(size=_APP_SIZE) as pilot:
        screen = _screen(app)
        role = cast("Select[str]", screen.query_one("#role", Select))
        await wait_until(
            lambda: rendered_select_label(role) == "Implementer", timeout=UI_WAIT_SECONDS
        )
        assert role.value == Role.IMPLEMENTER.value
        assert role.query_one(SelectOverlay).option_count == len(TASK_ACTION_ROLES)
        message = screen.query_one("#message", TextArea)
        await pilot.press("tab")
        await wait_until(lambda: app.focused is message, timeout=UI_WAIT_SECONDS)
        submit = screen.query_one("#palette-submit", Button)
        await _wait_for_hint_key(submit, DISABLED_KEY)
        await pilot.press(*"Review", "enter", *"evidence")
        assert message.text == "Review\nevidence"
        await _wait_for_hint_key(submit, CYAN_BOLD)
        await pilot.press("ctrl+s")
        await wait_until(clock.has_pending_sleep, timeout=UI_WAIT_SECONDS)
        await _wait_for_hint_key(submit, DISABLED_KEY)
        queued = queue.pending()[0]
        assert queued.command == Say(
            task_id=task.id,
            role=Role.IMPLEMENTER,
            text="Review\nevidence",
            command_id=queued.id,
        )
        returned = {"queued_for_next_turn": True}
        queue.mark_done(queued.id, returned)
        clock.advance(_POLL_SECONDS)
        await _wait_for_confirmation(screen)
        assert _static_text(screen, "#palette-result") == ""
        assert app.is_running
        assert message.text == ""
        assert app.focused is message
        assert _hint_key_style(submit, "Ctrl+S") == {DISABLED_KEY}
        assert _hint_key_style(submit, "queue message") == {GREY}
        await pilot.press("tab", "shift+tab")
        await wait_until(lambda: app.focused is message, timeout=UI_WAIT_SECONDS)
        await _wait_for_confirmation(screen, shown=False)

        await pilot.press(*"Second note.")
        await _wait_for_hint_key(submit, CYAN_BOLD)
        assert _hint_key_style(submit, "Enter") == {CYAN_BOLD}
        await pilot.press("ctrl+s")
        await wait_until(lambda: len(queue.pending()) == 1, timeout=UI_WAIT_SECONDS)
        await wait_until(clock.has_pending_sleep, timeout=UI_WAIT_SECONDS)
        rejected = queue.pending()[0]
        queue.mark_rejected(rejected.id, {"error": "guidance refused"})
        clock.advance(_POLL_SECONDS)
        await _wait_for_ack(screen, "Rejected")
        assert "guidance refused" in _static_text(screen, "#palette-result")
        assert message.text == "Second note."
        await _wait_for_hint_key(submit, CYAN_BOLD)


async def _assert_rejected_pause_keeps_hint_keys(
    connection: Connection,
    reader: StateReader,
    clock: FakeClock,
    tmp_path: Path,
    task_id: str,
) -> None:
    commands, queries, queue = operator_services(
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
        initial_context=PaletteRequest(action="pause", task_id=task_id),
    )
    async with app.run_test(size=_APP_SIZE) as pilot:
        screen = await _mounted_palette(app)
        submit = screen.query_one("#palette-submit", Button)
        await wait_until(lambda: not submit.disabled, timeout=UI_WAIT_SECONDS)
        await pilot.click("#palette-submit")
        await wait_until(clock.has_pending_sleep, timeout=UI_WAIT_SECONDS)
        queue.mark_rejected(queue.pending()[0].id, {"error": "engine refused the hold"})
        clock.advance(COMMAND_POLL_SECONDS)
        await wait_until(
            lambda: "Rejected" in _static_text(screen, "#palette-result"),
            timeout=UI_WAIT_SECONDS,
        )
        await wait_until(lambda: not submit.has_class("-active"), timeout=UI_WAIT_SECONDS)
        assert _hint_key_style(submit, "Enter") == {CYAN_BOLD}
        assert app.is_running
        await pilot.press("escape")
        await wait_until(lambda: not app.is_running, timeout=UI_WAIT_SECONDS)


@pytest.mark.usefixtures("colour_terminal")
async def test_task_detail_pause_and_cancel_are_confirmed_with_enter(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    clock = FakeClock(_START)
    task = seed_task(connection, clock, title="Enter confirmations")
    commands, queries, queue = operator_services(connection, reader, clock, tmp_path)

    def open_action(action: str) -> ScreenHost:
        return _palette_app(
            reader,
            commands,
            queries,
            _selection_must_not_run,
            initial_context=PaletteRequest(action=action, task_id=task.id),
        )

    app = open_action("cancel")
    async with app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        screen = _screen(app)
        submit = screen.query_one("#palette-submit", Button)
        assert _no_static_scrollbar(screen)
        await pilot.press("enter")
        await wait_until(lambda: "again" in str(submit.label), timeout=UI_WAIT_SECONDS)
        assert queue.pending() == []
        await pilot.press("escape")
        await wait_until(lambda: "again" not in str(submit.label), timeout=UI_WAIT_SECONDS)
        assert app.screen is screen
        await pilot.press("enter", "enter", "enter")
        await wait_until(lambda: not app.is_running, timeout=UI_WAIT_SECONDS)
        assert [item.command for item in queue.pending()] == [
            Cancel(task_id=task.id, command_id=queue.pending()[0].id)
        ]

    app = open_action("pause")
    async with app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        await pilot.press("enter", "enter", "enter")
        await wait_until(lambda: not app.is_running, timeout=UI_WAIT_SECONDS)
        assert len(queue.pending()) == 2
        assert queue.pending()[1].command == Pause(task_id=task.id)

    app = open_action("resume")
    async with app.run_test(size=_APP_SIZE) as pilot:
        screen = await _mounted_palette(app)
        await wait_until(
            lambda: screen.query_one("#palette-submit", Button).disabled,
            timeout=UI_WAIT_SECONDS,
        )
        await pilot.press("enter")
        assert len(queue.pending()) == 2
        await pilot.press("escape")
        await wait_until(lambda: not app.is_running, timeout=UI_WAIT_SECONDS)

    TaskStore(connection, clock).set_holds(task.id, frozenset({Hold.PAUSED}))
    app = open_action("resume")
    async with app.run_test(size=_APP_SIZE) as pilot:
        screen = await _mounted_palette(app)
        submit = screen.query_one("#palette-submit", Button)
        await wait_until(lambda: not submit.disabled, timeout=UI_WAIT_SECONDS)
        await wait_until(lambda: app.focused is submit, timeout=UI_WAIT_SECONDS)
        assert _no_static_scrollbar(screen)
        await pilot.press("enter", "enter", "enter")
        await wait_until(lambda: not app.is_running, timeout=UI_WAIT_SECONDS)
        assert len(queue.pending()) == 3
        assert queue.pending()[2].command == Resume(task_id=task.id)

    await _assert_rejected_pause_keeps_hint_keys(connection, reader, clock, tmp_path, task.id)


async def test_palette_reports_unknown_action_routes(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    clock = FakeClock(_START)
    task = seed_task(connection, clock, title="Invalid action route")
    commands, queries, queue = operator_services(
        connection,
        reader,
        clock,
        tmp_path,
        engine_up=True,
    )
    for action in (None, "", "unknown-action"):
        app = _palette_app(
            reader,
            commands,
            queries,
            _selection_must_not_run,
            initial_context=PaletteRequest(action=action, task_id=task.id),
        )

        async with app.run_test(size=_APP_SIZE):
            screen = _screen(app)
            expected_action = action or "<missing>"
            expected_error = f"unknown palette action route: {expected_action}"
            await wait_until(
                lambda screen=screen, expected_error=expected_error: (
                    _static_text(screen, "#palette-result") == expected_error
                ),
                timeout=UI_WAIT_SECONDS,
            )
            assert screen.query_one("#palette-submit", Button).disabled
            assert not queue.pending()


async def test_palette_shutdown_keeps_a_pending_command_without_stale_ui_refresh(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    clock = FakeClock(_START)
    task = seed_task(connection, clock, title="Shutdown with pending command")
    commands, queries, queue = operator_services(
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
        initial_context=PaletteRequest(action="message", task_id=task.id, role=Role.LEADER.value),
    )

    async with app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        screen = _screen(app)
        screen.query_one("#message", TextArea).text = "Keep the durable command on shutdown."
        await pilot.click("#palette-submit")
        await wait_until(clock.has_pending_sleep, timeout=UI_WAIT_SECONDS)
        assert len(queue.pending()) == 1

    assert len(queue.pending()) == 1


async def test_palette_keeps_acknowledgement_timeout_pending_with_current_engine_state(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    clock = FakeClock(_START)
    task = seed_task(connection, clock, title="Acknowledgement wait")
    commands, queries, queue = operator_services(
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
        await wait_until(clock.has_pending_sleep, timeout=UI_WAIT_SECONDS)
        queued = queue.pending()[0]
        await pilot.click("#palette-submit")
        assert len(queue.pending()) == 1
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


@pytest.mark.parametrize("terminal", ["done", "rejected"])
async def test_palette_observes_outcome_after_ack_wait_without_form_edits(
    connection: Connection,
    database: Database,
    reader: StateReader,
    tmp_path: Path,
    *,
    terminal: str,
) -> None:
    clock = FakeClock(_START)
    task = seed_task(connection, clock, title="Observed pending outcome")
    commands, queries, queue = operator_services(
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
        await wait_until(clock.has_pending_sleep, timeout=UI_WAIT_SECONDS)
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
        if terminal == "done":
            await wait_until(
                lambda: _static_text(screen, "#palette-result") == "",
                timeout=UI_WAIT_SECONDS,
            )
        else:
            await wait_until(
                lambda: "Rejected" in _static_text(screen, "#palette-result"),
                timeout=UI_WAIT_SECONDS,
            )
            assert f"command {pending.id}" in _static_text(screen, "#palette-result")
            assert "late engine rejection" in _static_text(screen, "#palette-result")
        await wait_until(
            lambda: not screen.query_one("#palette-submit", Button).disabled,
            timeout=UI_WAIT_SECONDS,
        )
        assert len(queue.pending()) == 0


async def test_pending_form_guards_survive_palette_reopen(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    clock = FakeClock(_START)
    task = seed_task(connection, clock, title="Multiple pending forms")
    commands, queries, queue = operator_services(connection, reader, clock, tmp_path)
    context = PaletteRequest(action="message", task_id=task.id, role=Role.LEADER.value)
    first_app = _palette_app(
        reader, commands, queries, _selection_must_not_run, initial_context=context
    )

    async with first_app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        screen = _screen(first_app)
        screen.query_one("#message", TextArea).text = "Do not send twice."
        await pilot.click("#palette-submit")
        await wait_until(lambda: len(queue.pending()) == 1, timeout=UI_WAIT_SECONDS)
        say_id = queue.pending()[0].id
        await _wait_for_confirmation(screen)
        assert screen.query_one("#message", TextArea).text == ""

    pause_app = _palette_app(
        reader,
        commands,
        queries,
        _selection_must_not_run,
        initial_context=replace(context, action="pause"),
    )
    async with pause_app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        await pilot.press("enter", "enter")
        await wait_until(lambda: len(queue.pending()) == 2, timeout=UI_WAIT_SECONDS)
        pause_id = queue.pending()[1].id

    reopened = _palette_app(
        reader, commands, queries, _selection_must_not_run, initial_context=context
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
    commands, queries, queue = operator_services(connection, reader, clock, tmp_path)
    request = "One pending task submission"
    submit = PaletteRequest(action="submit")
    first_app = _palette_app(
        reader, commands, queries, _selection_must_not_run, initial_context=submit
    )

    async with first_app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        screen = _screen(first_app)
        screen.query_one("#request", TextArea).text = request
        await pilot.click("#palette-submit")
        await wait_until(lambda: len(queue.pending()) == 1, timeout=UI_WAIT_SECONDS)
        queued = queue.pending()[0]
        assert isinstance(queued.command, Submit)
        assert queued.command.task_id is not None
        assert queued.command.title == request
        await pilot.press("escape")

    reopened = _palette_app(
        reader, commands, queries, _selection_must_not_run, initial_context=submit
    )
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
    commands, queries, queue = operator_services(connection, reader, clock, tmp_path)
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
            timeout=UI_WAIT_SECONDS,
        )
        assert screen.query_one("#palette-submit", Button).disabled
    assert not queue.pending()


async def _selection_must_not_run(pane_id: str) -> None:
    del pane_id
    raise AssertionError


async def _enter_text(pilot: Pilot[None], text: str) -> None:
    await pilot.press(*("enter" if character == "\n" else character for character in text))


def _static_text(screen: PaletteScreen, selector: str) -> str:
    rendered = cast("Text", screen.query_one(selector, Static).render())
    return rendered.plain


async def _mounted_palette(app: ScreenHost) -> PaletteScreen:
    await wait_until(
        lambda: isinstance(app.screen, PaletteScreen) and app.screen.is_mounted,
        timeout=UI_WAIT_SECONDS,
    )
    return _screen(app)


def _no_static_scrollbar(screen: PaletteScreen) -> bool:
    return not any(
        screen.query_one(selector).show_vertical_scrollbar
        for selector in ("#palette-form", "#palette-result-scroll")
    )


async def _fill_submit(pilot: Pilot[None], screen: PaletteScreen) -> None:
    request = screen.query_one("#request", TextArea)
    for _ in range(12):
        if request.has_focus:
            break
        await pilot.press("tab")
    assert request.has_focus
    await _enter_text(pilot, "first line\nsecond line")
    await pilot.click("#palette-options-fold")
    await wait_until(
        lambda: screen.query_one("#palette-options").display,
        timeout=UI_WAIT_SECONDS,
    )
    mode = cast("Select[str]", screen.query_one("#mode", Select))
    complexity = cast("Select[str]", screen.query_one("#complexity", Select))
    assert mode.value == ""
    assert complexity.value == ""
    assert rendered_select_label(mode) == "Auto detect"
    assert rendered_select_label(complexity) == "Triage decides"
    mode.focus()
    await pilot.press("enter", "down", "enter")
    await wait_until(lambda: rendered_select_label(mode) == "Implement", timeout=UI_WAIT_SECONDS)
    await pilot.resize_terminal(*_TINY_APP_SIZE)
    await wait_until(
        lambda: screen.size.width == _TINY_APP_SIZE[0] and screen.size.height == _TINY_APP_SIZE[1],
        timeout=UI_WAIT_SECONDS,
    )
    mode.focus()
    await wait_until(
        lambda: rendered_select_label(mode) == "Implement",
        timeout=UI_WAIT_SECONDS,
    )
    complexity.focus()
    await wait_until(
        lambda: rendered_select_label(complexity) == "Triage decides",
        timeout=UI_WAIT_SECONDS,
    )
    await pilot.resize_terminal(*_APP_SIZE)
    await wait_until(
        lambda: screen.size.width == _APP_SIZE[0] and screen.size.height == _APP_SIZE[1],
        timeout=UI_WAIT_SECONDS,
    )
    screen.query_one("#submission-priority", Input).value = "17"
    screen.query_one("#dependencies", Input).value = "T-8, T-9"
    screen.query_one("#leader-rung", Input).value = "codex:sol:high"
    screen.query_one("#implementer-rung", Input).value = "codex:sol:high"
    screen.query_one("#title", Input).value = "Palette"


def _is_typed_submit(command: Command) -> bool:
    return isinstance(command, Submit) and (
        command.request,
        command.title,
        command.mode,
        command.complexity,
        command.priority,
        command.depends_on,
        command.leader,
        command.implementer,
    ) == (
        "first line\nsecond line",
        "Palette",
        Mode.IMPLEMENT,
        None,
        17,
        ["T-8", "T-9"],
        _LIVE_RUNG,
        _LIVE_RUNG,
    )


@pytest.mark.usefixtures("colour_terminal")
async def test_palette_submit_queues_a_typed_task_and_closes(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    clock = FakeClock(_START)
    commands, queries, queue = operator_services(connection, reader, clock, tmp_path)
    listing = actions_screen(
        commands,
        queries,
        reader,
        _selection_must_not_run,
        PaletteRequest(),
    )
    app = ScreenHost(listing)

    async with app.run_test(size=_APP_SIZE) as pilot:
        await wait_until(lambda: app.screen is listing, timeout=UI_WAIT_SECONDS)
        await open_action(pilot, listing, "submit")
        screen = await _mounted_palette(app)
        await wait_until(
            lambda: not screen.query_one("#palette-options").display,
            timeout=UI_WAIT_SECONDS,
        )
        hint = screen.query_one("#palette-submit", Button)
        await _wait_for_hint_key(hint, DISABLED_KEY)
        await _fill_submit(pilot, screen)
        await _wait_for_hint_key(hint, CYAN_BOLD)
        await pilot.press("ctrl+s")
        await wait_until(lambda: app.screen is listing, timeout=UI_WAIT_SECONDS)

        queued = queue.pending()
        assert len(queued) == 1
        assert _is_typed_submit(queued[0].command)
        await wait_until(
            lambda: _CONFIRMED_GLYPH in row_line(listing, "Submit task"),
            timeout=UI_WAIT_SECONDS,
        )


@pytest.mark.parametrize(
    ("outcome", "selection_fails", "closes", "expected"),
    [
        pytest.param("done", False, True, (), id="done-selects-the-pane-and-closes"),
        pytest.param(
            "rejected",
            False,
            False,
            ("Rejected", "take over unavailable", "{task_id} · leader"),
            id="rejected-never-selects",
        ),
        pytest.param(
            "done",
            True,
            False,
            ("Applied", "could not select %7"),
            id="done-keeps-ack-with-selection-error",
        ),
    ],
)
async def test_palette_takeover_queues_a_task_role_command_and_selects_the_pane_only_when_done(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
    *,
    outcome: str,
    selection_fails: bool,
    closes: bool,
    expected: tuple[str, ...],
) -> None:
    clock = FakeClock(_START)
    task = seed_task(connection, clock)
    TaskStore(connection, clock).set_panes(task.id, {Role.LEADER: "%7"})
    _seed_live_session(connection, clock, task.id)
    commands, queries, queue = operator_services(
        connection, reader, clock, tmp_path, engine_up=True
    )
    selected: list[str] = []

    async def select_pane(pane_id: str) -> None:
        selected.append(pane_id)
        if selection_fails:
            message = f"could not select {pane_id}"
            raise ConfigError(message)

    app = _palette_app(
        reader,
        commands,
        queries,
        select_pane,
        initial_context=PaletteRequest(action="take-over", task_id=task.id, role=Role.LEADER.value),
    )

    async with app.run_test(size=_APP_SIZE) as pilot:
        screen = await _mounted_palette(app)
        await _focus_role_action_hint(app, pilot, screen)
        await pilot.press("enter")
        await wait_until(clock.has_pending_sleep, timeout=UI_WAIT_SECONDS)
        queued = queue.pending()
        assert len(queued) == 1
        assert queued[0].command == TakeOver(
            task_id=task.id, role=Role.LEADER, command_id=queued[0].id
        )
        if outcome == "rejected":
            queue.mark_rejected(queued[0].id, {"error": "take over unavailable"})
        else:
            queue.mark_done(queued[0].id, {"task_id": task.id})
        clock.advance(_POLL_SECONDS)
        if closes:
            await wait_until(lambda: app.result == "take-over", timeout=UI_WAIT_SECONDS)
        else:
            await wait_until(
                lambda: all(
                    text.format(task_id=task.id) in _static_text(screen, "#palette-result")
                    for text in expected
                ),
                timeout=UI_WAIT_SECONDS,
            )
            assert app.is_running
        assert selected == (["%7"] if outcome == "done" else [])


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
    commands, queries, queue = operator_services(
        connection, reader, clock, tmp_path, engine_up=True
    )
    app = _palette_app(
        reader,
        commands,
        queries,
        tmux.select_pane,
        initial_context=PaletteRequest(
            action="take-over", task_id=task.id, role=Role.IMPLEMENTER.value
        ),
    )

    async with app.run_test(size=_APP_SIZE) as pilot:
        await _mounted_palette(app)
        await _focus_role_action_hint(app, pilot, _screen(app))
        await pilot.press("enter")
        await wait_until(clock.has_pending_sleep, timeout=UI_WAIT_SECONDS)
        queued = queue.pending()
        assert len(queued) == 1
        queue.mark_done(queued[0].id, {"task_id": task.id})
        clock.advance(_POLL_SECONDS)
        await wait_until(lambda: app.result == "take-over", timeout=UI_WAIT_SECONDS)

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
    commands, queries, queue = operator_services(connection, reader, clock, tmp_path)
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
        assert cast("Select[str]", screen.query_one("#role", Select)).value == (
            Role.IMPLEMENTER.value
        )
        screen.query_one("#message", TextArea).text = "Continue the implementer turn."
        await pilot.click("#palette-submit")
        await _wait_for_confirmation(screen)

    queued = queue.pending()[0]
    assert queued.command == Say(
        task_id=task.id,
        role=Role.IMPLEMENTER,
        text="Continue the implementer turn.",
        command_id=queued.id,
    )


@pytest.mark.parametrize(
    ("task_exists", "expected"),
    [(True, "role pane is unavailable"), (False, "task no longer exists")],
)
async def test_palette_disables_takeover_without_task_or_role_pane(
    connection: Connection, reader: StateReader, tmp_path: Path, task_exists: bool, expected: str
) -> None:
    clock = FakeClock(_START)
    task_id = seed_task(connection, clock).id if task_exists else "T-404"
    commands, queries, queue = operator_services(
        connection, reader, clock, tmp_path, engine_up=True
    )
    app = _palette_app(
        reader,
        commands,
        queries,
        _selection_must_not_run,
        initial_context=PaletteRequest(action="take-over", task_id=task_id),
    )

    async with app.run_test(size=_APP_SIZE) as pilot:
        screen = await _mounted_palette(app)
        hint = screen.query_one("#palette-submit", Button)
        await wait_until(
            lambda: hint.disabled and expected in _static_text(screen, "#palette-action-state"),
            timeout=UI_WAIT_SECONDS,
        )
        await pilot.press("enter")
        await wait_until(lambda: not queue.pending(), timeout=UI_WAIT_SECONDS)


_SIMPLE_POPUP_ROLE = Role.IMPLEMENTER
_FIRST_TASK = "T-1"
_SESSION_LINE = "leader · codex"


def _no_edit(popup: PaletteScreen) -> None:
    del popup


def _enter_priority(popup: PaletteScreen) -> None:
    popup.query_one("#priority", Input).value = "17"


def _enter_rung(popup: PaletteScreen) -> None:
    popup.query_one("#rung", Input).value = "codex:sol:high"


def _without_id(command: Command) -> Command:
    if isinstance(command, IdentifiedCommand):
        return command.model_copy(update={"command_id": None})
    return command


@dataclass(frozen=True)
class _SimplePopup:
    action: str
    verb: str
    rows: frozenset[str]
    queued: Command | None
    edit: Callable[[PaletteScreen], None] = _no_edit
    armed: str | None = None
    blank: bool = False


_ONE_ENTER_POPUPS = (
    _SimplePopup(
        "take-over",
        "take over",
        frozenset({"role"}),
        TakeOver(task_id=_FIRST_TASK, role=_SIMPLE_POPUP_ROLE),
    ),
    _SimplePopup(
        "hand-back",
        "hand back",
        frozenset({"role"}),
        HandBack(task_id=_FIRST_TASK, role=Role.LEADER),
    ),
    _SimplePopup("pause-now", "pause now", frozenset(), Pause(task_id=_FIRST_TASK, now=True)),
    _SimplePopup(
        "prioritize",
        "set priority",
        frozenset({"priority"}),
        Prioritize(task_id=_FIRST_TASK, priority=17),
        _enter_priority,
    ),
    _SimplePopup(
        "rung",
        "set rung",
        frozenset({"role", "rung"}),
        SetRung(task_id=_FIRST_TASK, role=Role.LEADER, rung=_LIVE_RUNG),
        _enter_rung,
        blank=True,
    ),
)
_TWO_ENTER_POPUPS = (
    _SimplePopup(
        "retry", "retry", frozenset(), Retry(task_id=_FIRST_TASK), armed="Enter again to retry"
    ),
)


async def _exercise_simple_popup(
    host: ScreenHost,
    pilot: Pilot[None],
    listing: ActionsListScreen,
    queue: CommandQueue,
    case: _SimplePopup,
) -> None:
    await open_action(pilot, listing, case.action)
    popup = await _mounted_palette(host)
    hint = popup.query_one("#palette-submit", Button)
    shown = {
        name for name in ("role", "priority", "rung") if popup.query_one(f"#field-{name}").display
    }
    assert shown == case.rows, case.action
    assert str(hint.label) == f"Enter {case.verb}", case.action
    await wait_until(lambda: hint.disabled is case.blank, timeout=UI_WAIT_SECONDS)
    case.edit(popup)
    if case.action in {"take-over", "hand-back"}:
        await _focus_role_action_hint(host, pilot, popup)
    await wait_until(lambda: not hint.disabled, timeout=UI_WAIT_SECONDS)
    before = len(queue.pending())
    if case.armed is not None:
        await pilot.press("enter")
        await wait_until(lambda: str(hint.label) == case.armed, timeout=UI_WAIT_SECONDS)
        assert len(queue.pending()) == before, case.action
        await pilot.press("x")
        await wait_until(lambda: str(hint.label) == f"Enter {case.verb}", timeout=UI_WAIT_SECONDS)
        assert host.focused is hint
        await pilot.press("enter")
        await wait_until(lambda: str(hint.label) == case.armed, timeout=UI_WAIT_SECONDS)
        assert len(queue.pending()) == before, case.action
    await pilot.press("enter")
    if case.queued is None:
        await wait_until(
            lambda: _SESSION_LINE in _static_text(popup, "#palette-result"), timeout=UI_WAIT_SECONDS
        )
        assert len(queue.pending()) == before, case.action
        await pilot.press("escape")
    else:
        await wait_until(lambda: len(queue.pending()) == before + 1, timeout=UI_WAIT_SECONDS)
        assert _without_id(queue.pending()[-1].command) == case.queued, case.action
        label = SUBMIT_ACTION_LABELS[case.action]
        await wait_until(
            lambda: _CONFIRMED_GLYPH in row_line(listing, label), timeout=UI_WAIT_SECONDS
        )
    await wait_until(lambda: host.screen is listing, timeout=UI_WAIT_SECONDS)


async def _focus_role_action_hint(
    app: PaletteApp | ScreenHost,
    pilot: Pilot[None],
    popup: PaletteScreen,
) -> None:
    role = cast("Select[str]", popup.query_one("#role", Select))
    submit = popup.query_one("#palette-submit", Button)
    await wait_until(lambda: app.focused is role, timeout=UI_WAIT_SECONDS)
    await pilot.press("tab")
    await wait_until(lambda: app.focused is submit, timeout=UI_WAIT_SECONDS)


@pytest.mark.parametrize(
    "cases",
    [
        pytest.param(_ONE_ENTER_POPUPS, id="act-on-the-first-enter"),
        pytest.param(_TWO_ENTER_POPUPS, id="confirm-with-a-second-enter"),
    ],
)
async def test_simple_action_popups_execute_from_keyboard_and_close_after_queueing(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
    cases: tuple[_SimplePopup, ...],
) -> None:
    clock = FakeClock(_START)
    task = seed_task(connection, clock, title="Common style")
    assert task.id == _FIRST_TASK
    TaskStore(connection, clock).set_panes(task.id, {Role.LEADER: "%7", Role.IMPLEMENTER: "%8"})
    RunStore(connection, clock).mark_taken_over(_seed_live_session(connection, clock, task.id))
    _seed_live_session(connection, clock, task.id, Role.IMPLEMENTER)
    commands, queries, queue = operator_services(connection, reader, clock, tmp_path)
    listing = actions_screen(
        commands,
        queries,
        reader,
        _selection_must_not_run,
        PaletteRequest(task_id=task.id),
        action_confirmation_seconds=ACTION_CONFIRMATION_SECONDS,
    )
    host = ScreenHost(listing)

    async with host.run_test(size=_APP_SIZE) as pilot:
        for case in cases:
            await _exercise_simple_popup(host, pilot, listing, queue, case)
