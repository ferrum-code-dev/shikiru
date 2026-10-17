from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import pytest
from textual.containers import VerticalScroll
from textual.widgets import Button, Input, Select, Static, TextArea
from textual.widgets._select import SelectOverlay

from shikiru.agents.contracts import Rung
from shikiru.dashboard.contract import PaletteRequest
from shikiru.dashboard.palette import PaletteApp, PaletteScreen
from shikiru.domain.commands import (
    Cancel,
    CommandStatus,
    Pause,
    Prioritize,
    Resume,
    Say,
    SetRung,
    Stop,
    Submit,
    TakeOver,
)
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import Hold, Mode, Role, Status, Step
from shikiru.errors import ConfigError
from shikiru.operator.commands import (
    COMMAND_ACK_WAIT_SECONDS,
    COMMAND_POLL_SECONDS,
    OperatorCommands,
)
from shikiru.operator.queries import TASK_ACTION_ROLES
from shikiru.store.commands import CommandQueue
from shikiru.store.reader import DASHBOARD_CHANGE_POLL_SECONDS, StateReader
from shikiru.store.runs import RunStore
from shikiru.store.tasks import TaskStore
from shikiru.tmux.client import FORMAT_PANE_ID, Tmux, exact_target
from tests.support.cells import CYAN_BOLD, DISABLED_KEY, GREY, rendered_lines
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

    from shikiru.domain.commands import Command
    from shikiru.domain.tasks import Task
    from shikiru.operator.queries import OperatorQueries
    from shikiru.store.database import Database
    from tests.support.store import Connection
    from tests.support.tmux import TmuxServer


_START = datetime(2026, 1, 1, tzinfo=UTC)
_POLL_SECONDS = 0.25
_APP_SIZE = (120, 32)
_LIVE_RUNG = Rung(agent="codex", model="sol", effort="high")
_NATIVE_VISUAL_REVIEW_DIR_ENV = "SHIKIRU_NATIVE_VISUAL_REVIEW_DIR"
_CONFIRMED_GLYPH = "\N{CHECK MARK}"


def _seed_live_session(connection: Connection, clock: FakeClock, task_id: str) -> None:
    runs = RunStore(connection, clock)
    run = runs.start(task_id, Role.LEADER, _LIVE_RUNG)
    runs.set_session(run.id, "session-current", "resume-current")


def _screen(app: PaletteApp) -> PaletteScreen:
    return cast("PaletteScreen", app.screen)


async def _click_menu_button(
    pilot: Pilot[None],
    screen: PaletteScreen,
    button_id: str,
) -> None:
    button = screen.query_one(f"#{button_id}", Button)
    menu = screen.query_one("#palette-menu", VerticalScroll)
    parent = button.parent
    while parent is not None and parent is not menu:
        if not parent.display:
            more_actions = screen.query_one("#palette-menu-more-actions", Button)
            menu.scroll_to_widget(more_actions, animate=False, immediate=True)
            await pilot.pause()
            await pilot.click(more_actions)
            await wait_until(lambda parent=parent: parent.display, timeout=UI_WAIT_SECONDS)
            break
        parent = parent.parent
    menu.scroll_to_widget(button, animate=False, immediate=True)
    await pilot.pause()
    await pilot.click(button)


def _assert_context_menu_role_actions(
    screen: PaletteScreen,
    queue: CommandQueue,
) -> VerticalScroll:
    menu = screen.query_one("#palette-menu", VerticalScroll)
    assert menu.display
    assert screen.query_one("#palette-menu-action-submit").display
    assert screen.query_one("#palette-menu-action-stop").display
    assert screen.query_one("#palette-menu-action-take-over").display
    assert not screen.query_one("#palette-menu-action-resume").display
    assert not screen.query_one("#palette-menu-action-hand-back").display
    assert not screen.query_one("#palette-menu-task-actions").display
    assert screen.query_one("#palette-menu-more-actions").display
    assert queue.pending() == []
    return menu


def _palette_app(
    reader: StateReader,
    commands: OperatorCommands,
    queries: OperatorQueries,
    pane_selector: Callable[[str], Awaitable[None]],
    *,
    initial_context: PaletteRequest | None = None,
    action_confirmation_seconds: float = ACTION_CONFIRMATION_SECONDS,
) -> PaletteApp:
    return PaletteApp(
        commands,
        queries,
        pane_selector,
        action_confirmation_seconds=action_confirmation_seconds,
        on_change=reader.watch,
        initial_context=initial_context,
    )


async def _open_action(pilot: Pilot[None], screen: PaletteScreen, action: str) -> None:
    menu = screen.query_one("#palette-menu", VerticalScroll)
    if menu.display:
        button = screen.query_one(f"#palette-menu-action-{action}", Button)
        assert button.display
        await pilot.click(button)
    else:
        screen.query_one("#action", Select).value = action
        await pilot.pause()


async def _set_target(pilot: Pilot[None], screen: PaletteScreen, task_id: str) -> None:
    screen.query_one("#task-id", Input).value = task_id
    await pilot.pause()


async def _set_role(pilot: Pilot[None], screen: PaletteScreen, role: Role) -> None:
    screen.query_one("#role", Select).value = role.value
    await pilot.pause()


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
        await _wait_for_confirmation(screen)
        assert queue.pending()[0].command == Say(
            task_id=task.id,
            role=Role.IMPLEMENTER,
            text="Check the failing integration path.",
            command_id=queue.pending()[0].id,
        )

        menu = screen.query_one("#palette-menu", VerticalScroll)
        await pilot.click("#palette-back-to-menu")
        await wait_until(lambda: menu.display, timeout=UI_WAIT_SECONDS)
        await _click_menu_button(pilot, screen, "palette-menu-action-rung")
        screen.query_one("#rung", Input).value = "codex:sol:high"
        assert not screen.query_one("#palette-submit", Button).disabled, _static_text(
            screen, "#palette-action-state"
        )
        await pilot.click("#palette-submit")
        await wait_until(lambda: len(queue.pending()) == 2, timeout=UI_WAIT_SECONDS)
        assert queue.pending()[1].command == SetRung(
            task_id=task.id,
            role=Role.IMPLEMENTER,
            rung=_LIVE_RUNG,
        )


async def test_context_menu_keeps_applicable_role_actions_and_form_drafts(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    clock = FakeClock(_START)
    task = seed_task(connection, clock, title="Context menu role target")
    TaskStore(connection, clock).set_panes(task.id, {Role.IMPLEMENTER: "%7"})
    run = RunStore(connection, clock).start(task.id, Role.IMPLEMENTER, _LIVE_RUNG)
    RunStore(connection, clock).set_session(run.id, "menu-session", "menu-resume")
    commands, queries, queue = operator_services(connection, reader, clock, tmp_path)
    app = _palette_app(
        reader,
        commands,
        queries,
        _selection_must_not_run,
        initial_context=PaletteRequest(task_id=task.id),
    )

    async with app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        screen = _screen(app)
        menu = _assert_context_menu_role_actions(screen, queue)

        await _click_menu_button(pilot, screen, "palette-menu-action-message")
        assert not menu.display
        assert screen.query_one("#field-role").display
        assert screen.query_one("#field-message").display
        _assert_fixed_task_context_title(screen, task.id)
        message = screen.query_one("#message", TextArea)
        await wait_until(lambda: app.focused is message, timeout=UI_WAIT_SECONDS)
        guidance = "Keep the new parser branch behind the existing validation."
        await pilot.press(*guidance)
        await pilot.click("#palette-back-to-menu")
        await wait_until(lambda: menu.display, timeout=UI_WAIT_SECONDS)
        assert message.text == guidance
        assert queue.pending() == []

        await _click_menu_button(pilot, screen, "palette-menu-action-message")
        assert message.text == guidance
        assert screen.query_one("#field-role").display
        await pilot.click("#palette-back-to-menu")
        await wait_until(lambda: menu.display, timeout=UI_WAIT_SECONDS)
        await _click_menu_button(pilot, screen, "palette-menu-action-take-over")
        role = cast("Select[str]", screen.query_one("#role", Select))
        assert role.value == Role.IMPLEMENTER.value
        await wait_until(lambda: app.focused is role, timeout=UI_WAIT_SECONDS)
        assert screen.query_one("#field-role").display
        assert not screen.query_one("#field-message").display
        assert queue.pending() == []

        current = TaskStore(connection, clock).require(task.id)
        TaskStore(connection, clock).apply_transition(
            task.id,
            Transition.stay(current, step=None, status=Status.CANCELLED),
        )
        clock.advance(DASHBOARD_CHANGE_POLL_SECONDS)
        await wait_until(
            lambda: not screen.query_one("#palette-menu-action-take-over").display,
            timeout=UI_WAIT_SECONDS,
        )
        assert not screen.query_one("#palette-menu-action-message").display
        assert queue.pending() == []


async def test_project_context_menu_has_no_task_targets(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    clock = FakeClock(_START)
    commands, queries, queue = operator_services(connection, reader, clock, tmp_path)
    app = _palette_app(
        reader,
        commands,
        queries,
        _selection_must_not_run,
        initial_context=PaletteRequest(),
    )

    async with app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        screen = _screen(app)
        assert screen.query_one("#palette-menu").display
        assert _static_text(screen, "#palette-menu-context") == "Project"
        assert not screen.query_one("#palette-menu-task-actions").display
        assert not screen.query_one("#palette-menu-extensions").display
        assert not screen.query_one("#palette-menu-more-actions").display
        assert screen.query_one("#palette-menu-action-submit").display
        assert screen.query_one("#palette-menu-action-stop").display
        assert queue.pending() == []


async def test_context_menu_preserves_an_explicit_unavailable_role(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    clock = FakeClock(_START)
    task = seed_task(connection, clock, title="Explicit role target")
    TaskStore(connection, clock).set_panes(task.id, {Role.IMPLEMENTER: "%7"})
    run = RunStore(connection, clock).start(task.id, Role.IMPLEMENTER, _LIVE_RUNG)
    RunStore(connection, clock).set_session(run.id, "explicit-session", "explicit-resume")
    commands, queries, queue = operator_services(connection, reader, clock, tmp_path)
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
        assert screen.query_one("#palette-menu-action-take-over").display
        await _click_menu_button(pilot, screen, "palette-menu-action-take-over")
        assert cast("Select[str]", screen.query_one("#role", Select)).value == Role.LEADER.value
        assert screen.query_one("#palette-submit", Button).disabled
        assert queue.pending() == []


async def test_contextual_project_actions_queue_only_after_explicit_submission(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    clock = FakeClock(_START)
    task = seed_task(connection, clock, title="Project action context")
    commands, queries, queue = operator_services(connection, reader, clock, tmp_path)
    app = _palette_app(
        reader,
        commands,
        queries,
        _selection_must_not_run,
        initial_context=PaletteRequest(task_id=task.id),
    )

    async with app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        screen = _screen(app)
        menu = screen.query_one("#palette-menu", VerticalScroll)
        assert menu.display
        assert not screen.query_one("#palette-menu-project-actions").display
        await pilot.click("#palette-menu-more-actions")
        assert screen.query_one("#palette-menu-project-actions").display
        assert queue.pending() == []

        guidance = "Record the requested work before the next release."
        for action in ("submit", "stop"):
            button = screen.query_one(f"#palette-menu-action-{action}", Button)
            menu.scroll_to_widget(button, animate=False, immediate=True)
            button.scroll_visible(animate=False, force=True)
            button.focus()
            await pilot.wait_for_scheduled_animations()
            await pilot.pause()
            assert button.region.y >= menu.region.y
            assert button.region.bottom <= menu.region.bottom
            await pilot.click(button)
            await wait_until(lambda: not menu.display, timeout=UI_WAIT_SECONDS)

            if action == "submit":
                screen.query_one("#request", TextArea).text = guidance
            await pilot.click("#palette-submit")
            expected_pending_count = 1 if action == "submit" else 2
            await wait_until(
                lambda count=expected_pending_count: len(queue.pending()) == count,
                timeout=UI_WAIT_SECONDS,
            )

            if action == "submit":
                submitted = queue.pending()[0].command
                assert isinstance(submitted, Submit)
                assert submitted.request == guidance
            else:
                assert isinstance(queue.pending()[1].command, Stop)

            if action == "submit":
                await pilot.click("#palette-back-to-menu")
                await wait_until(lambda: menu.display, timeout=UI_WAIT_SECONDS)


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
        await pilot.pause()
        screen = _screen(app)
        _assert_fixed_task_context_title(screen, task.id)
        assert not screen.query_one("#palette-back-to-menu").display
        role = cast("Select[str]", screen.query_one("#role", Select))
        assert role.value == Role.IMPLEMENTER.value
        assert role.query_one(SelectOverlay).option_count == len(TASK_ACTION_ROLES)
        message = screen.query_one("#message", TextArea)
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


async def _assert_menu_routes_and_rejected_keys(
    connection: Connection,
    reader: StateReader,
    clock: FakeClock,
    tmp_path: Path,
    task_id: str,
) -> None:
    await _assert_rejected_pause_keeps_hint_keys(connection, reader, clock, tmp_path, task_id)
    for action in ("resume", "pause", "cancel"):
        await _assert_menu_action_returns_to_menu_after_one_command(
            connection, reader, clock, tmp_path, task_id=task_id, action=action
        )


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


async def _assert_menu_action_returns_to_menu_after_one_command(
    connection: Connection,
    reader: StateReader,
    clock: FakeClock,
    tmp_path: Path,
    *,
    task_id: str,
    action: str,
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
        initial_context=PaletteRequest(task_id=task_id),
        action_confirmation_seconds=ACTION_CONFIRMATION_EXPIRY_SECONDS,
    )
    async with app.run_test(size=_APP_SIZE) as pilot:
        screen = await _mounted_palette(app)
        menu = screen.query_one("#palette-menu", VerticalScroll)
        submitted_before = reader.command_history(limit=1).total_count
        await _click_menu_button(pilot, screen, f"palette-menu-action-{action}")
        await wait_until(lambda: not menu.display, timeout=UI_WAIT_SECONDS)
        submit = screen.query_one("#palette-submit", Button)
        await wait_until(lambda: not submit.disabled, timeout=UI_WAIT_SECONDS)
        await pilot.click("#palette-submit")
        await wait_until(clock.has_pending_sleep, timeout=UI_WAIT_SECONDS)
        queue.mark_done(queue.pending()[0].id)
        clock.advance(COMMAND_POLL_SECONDS)
        await wait_until(lambda: menu.display, timeout=UI_WAIT_SECONDS)
        await wait_until(
            lambda: screen.focused is not None and menu in screen.focused.ancestors_with_self,
            timeout=UI_WAIT_SECONDS,
        )
        entry = screen.query_one(f"#palette-menu-action-{action}", Button)
        await wait_until(lambda: _CONFIRMED_GLYPH in str(entry.label), timeout=UI_WAIT_SECONDS)
        await wait_until(lambda: _CONFIRMED_GLYPH not in str(entry.label), timeout=UI_WAIT_SECONDS)
        assert not submit.display
        assert reader.command_history(limit=1).total_count == submitted_before + 1
        assert app.is_running
        await screen.action_close()
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

    def open_action(action: str) -> PaletteApp:
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
        assert not screen.query_one("#palette-back-to-menu").display
        assert _no_static_scrollbar(screen)
        await pilot.press("enter")
        await wait_until(lambda: "again" in str(submit.label), timeout=UI_WAIT_SECONDS)
        assert queue.pending() == []
        await pilot.press("escape")
        await wait_until(lambda: "again" not in str(submit.label), timeout=UI_WAIT_SECONDS)
        assert _hint_key_style(submit, "Enter") == {CYAN_BOLD}
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
        assert not submit.can_focus
        assert not screen.query_one("#palette-back-to-menu").display
        assert _no_static_scrollbar(screen)
        await pilot.press("enter", "enter", "enter")
        await wait_until(lambda: not app.is_running, timeout=UI_WAIT_SECONDS)
        assert len(queue.pending()) == 3
        assert queue.pending()[2].command == Resume(task_id=task.id)

    await _assert_menu_routes_and_rejected_keys(connection, reader, clock, tmp_path, task.id)


async def test_palette_target_follows_search_choice_and_stays_manually_editable(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    clock = FakeClock(_START)
    context_task = seed_task(connection, clock, title="Detail task")
    other_task = seed_task(connection, clock, title="Other task")
    selected_task = seed_task(connection, clock, title="Chosen by title")
    commands, queries, queue = operator_services(connection, reader, clock, tmp_path)
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
        task_id.value = other_task.id
        await wait_until(
            lambda: task_id.value == other_task.id and task_choice.value == other_task.id,
            timeout=UI_WAIT_SECONDS,
        )
        task_id.value = "T-999"
        await wait_until(
            lambda: task_id.value == "T-999" and task_choice.value is Select.BLANK,
            timeout=UI_WAIT_SECONDS,
        )

        screen.query_one("#task-search", Input).value = selected_task.title
        await pilot.pause()
        task_choice.focus()
        task_choice.value = selected_task.id
        await pilot.pause()
        assert task_id.value == selected_task.id
        screen.query_one("#message", TextArea).text = "Use the deliberately selected task."
        await pilot.click("#palette-submit")
        await _wait_for_confirmation(screen)

        (queued,) = queue.pending()
        assert queued.command == Say(
            task_id=selected_task.id,
            role=Role.LEADER,
            text="Use the deliberately selected task.",
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
    commands, queries, _queue = operator_services(connection, reader, clock, tmp_path)
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
            timeout=UI_WAIT_SECONDS,
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
        initial_context=PaletteRequest(task_id=task.id, role=Role.LEADER.value),
    )

    async with app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        screen = _screen(app)
        await _open_action(pilot, screen, "message")
        screen.query_one("#message", TextArea).text = "Send once while awaiting acknowledgement."
        await pilot.click("#palette-submit")
        await wait_until(clock.has_pending_sleep, timeout=UI_WAIT_SECONDS)
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
        initial_context=PaletteRequest(task_id=task.id, role=Role.LEADER.value),
    )

    async with app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        screen = _screen(app)
        await _open_action(pilot, screen, "message")
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


async def test_pending_form_guards_survive_form_switches_and_palette_reopen(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    clock = FakeClock(_START)
    task = seed_task(connection, clock, title="Multiple pending forms")
    commands, queries, queue = operator_services(connection, reader, clock, tmp_path)
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
        await wait_until(lambda: len(queue.pending()) == 1, timeout=UI_WAIT_SECONDS)
        say_id = queue.pending()[0].id
        await _wait_for_confirmation(screen)
        assert screen.query_one("#message", TextArea).text == ""

        menu = screen.query_one("#palette-menu", VerticalScroll)
        await pilot.click("#palette-back-to-menu")
        await wait_until(lambda: menu.display, timeout=UI_WAIT_SECONDS)
        await _click_menu_button(pilot, screen, "palette-menu-action-pause")
        await pilot.click("#palette-submit")
        await wait_until(lambda: len(queue.pending()) == 2, timeout=UI_WAIT_SECONDS)
        pause_id = queue.pending()[1].id
        await wait_until(lambda: menu.display, timeout=UI_WAIT_SECONDS)

        await _click_menu_button(pilot, screen, "palette-menu-action-message")
        assert screen.query_one("#message", TextArea).text == ""
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
    commands, queries, queue = operator_services(connection, reader, clock, tmp_path)
    request = "One pending task submission"
    first_app = _palette_app(reader, commands, queries, _selection_must_not_run)

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
    commands, queries, _queue = operator_services(connection, reader, clock, tmp_path)
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
            timeout=UI_WAIT_SECONDS,
        )

        role.focus()
        role.value = Role.LEADER.value
        await pilot.pause()
        task_id.value = leader_task.id
        await wait_until(lambda: task_id.value == leader_task.id, timeout=UI_WAIT_SECONDS)
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


async def _mounted_palette(app: PaletteApp) -> PaletteScreen:
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


def _assert_fixed_task_context_title(screen: PaletteScreen, task_id: str) -> None:
    assert not screen.query_one("#field-task-search").display
    assert not screen.query_one("#field-task-choice").display
    assert not screen.query_one("#field-task-id").display
    assert _static_text(screen, "#palette-title") == f"Message · {task_id}"


async def _fill_submit(pilot: Pilot[None], screen: PaletteScreen, task: Task) -> None:
    del task
    request = screen.query_one("#request", TextArea)
    for _ in range(12):
        if request.has_focus:
            break
        await pilot.press("tab")
    assert request.has_focus
    await _enter_text(pilot, "first line\nsecond line")
    await pilot.click("#palette-advanced-toggle")
    screen.query_one("#mode", Select).value = Mode.IMPLEMENT.value
    screen.query_one("#submission-priority", Input).value = "17"
    screen.query_one("#dependencies", Input).value = "T-8, T-9"
    screen.query_one("#leader-rung", Input).value = "codex:sol:high"
    screen.query_one("#implementer-rung", Input).value = "codex:sol:high"
    screen.query_one("#title", Input).value = "Palette"


async def _fill_prioritize(pilot: Pilot[None], screen: PaletteScreen, task: Task) -> None:
    await _set_target(pilot, screen, task.id)
    screen.query_one("#priority", Input).value = "17"


async def _fill_pause_now(pilot: Pilot[None], screen: PaletteScreen, task: Task) -> None:
    await _set_target(pilot, screen, task.id)


def _is_typed_submit(command: Command, task: Task) -> bool:
    del task
    return isinstance(command, Submit) and (
        command.request,
        command.title,
        command.mode,
        command.priority,
        command.depends_on,
        command.leader,
        command.implementer,
    ) == (
        "first line\nsecond line",
        "Palette",
        Mode.IMPLEMENT,
        17,
        ["T-8", "T-9"],
        _LIVE_RUNG,
        _LIVE_RUNG,
    )


def _is_prioritize(command: Command, task: Task) -> bool:
    return command == Prioritize(task_id=task.id, priority=17)


def _is_pause_now(command: Command, task: Task) -> bool:
    return command == Pause(task_id=task.id, now=True)


_SUBMIT_CASES = (
    pytest.param("submit", _fill_submit, _is_typed_submit, id="typed-submit"),
    pytest.param("prioritize", _fill_prioritize, _is_prioritize, id="prioritize"),
    pytest.param("pause-now", _fill_pause_now, _is_pause_now, id="pause-now"),
)


@pytest.mark.parametrize(("action", "fill", "queued_as_expected"), _SUBMIT_CASES)
async def test_palette_submit_queues_the_command_and_shows_its_ack(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
    *,
    action: str,
    fill: Callable[[Pilot[None], PaletteScreen, Task], Awaitable[None]],
    queued_as_expected: Callable[[Command, Task], bool],
) -> None:
    clock = FakeClock(_START)
    task = seed_task(connection, clock)
    commands, queries, queue = operator_services(connection, reader, clock, tmp_path)
    app = _palette_app(reader, commands, queries, _selection_must_not_run)

    async with app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        screen = _screen(app)
        await _open_action(pilot, screen, action)
        await fill(pilot, screen, task)
        await pilot.click("#palette-submit")
        await _wait_for_ack(screen)

        queued = queue.pending()
        assert len(queued) == 1
        assert queued_as_expected(queued[0].command, task)
        feedback = _static_text(screen, "#palette-result")
        assert "Pending" in feedback
        assert "acknowledgement wait elapsed" not in feedback


@pytest.mark.parametrize(
    ("engine_up", "outcome", "selection_fails", "expected"),
    [
        pytest.param(False, None, False, ("Pending",), id="queued-without-selection"),
        pytest.param(
            True,
            "rejected",
            False,
            ("Rejected", "take over unavailable", "{task_id} · leader"),
            id="rejected-never-selects",
        ),
        pytest.param(
            True,
            "done",
            True,
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
    engine_up: bool,
    outcome: str | None,
    selection_fails: bool,
    expected: tuple[str, ...],
) -> None:
    clock = FakeClock(_START)
    task = seed_task(connection, clock)
    TaskStore(connection, clock).set_panes(task.id, {Role.LEADER: "%7"})
    _seed_live_session(connection, clock, task.id)
    commands, queries, queue = operator_services(
        connection, reader, clock, tmp_path, engine_up=engine_up
    )
    selected: list[str] = []

    async def select_pane(pane_id: str) -> None:
        selected.append(pane_id)
        if selection_fails:
            message = f"could not select {pane_id}"
            raise ConfigError(message)

    app = _palette_app(reader, commands, queries, select_pane)

    async with app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        screen = _screen(app)
        await _open_action(pilot, screen, "take-over")
        await _set_target(pilot, screen, task.id)
        await _set_role(pilot, screen, Role.LEADER)
        await pilot.click("#palette-submit")
        if engine_up:
            await wait_until(clock.has_pending_sleep, timeout=UI_WAIT_SECONDS)
        else:
            await _wait_for_ack(screen)
        queued = queue.pending()
        assert len(queued) == 1
        assert queued[0].command == TakeOver(
            task_id=task.id, role=Role.LEADER, command_id=queued[0].id
        )
        if outcome == "rejected":
            queue.mark_rejected(queued[0].id, {"error": "take over unavailable"})
        elif outcome == "done":
            queue.mark_done(queued[0].id, {"task_id": task.id})
        if outcome:
            clock.advance(_POLL_SECONDS)
        await wait_until(
            lambda: all(
                text.format(task_id=task.id) in _static_text(screen, "#palette-result")
                for text in expected
            ),
            timeout=UI_WAIT_SECONDS,
        )
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
    app = _palette_app(reader, commands, queries, tmux.select_pane)

    async with app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        screen = _screen(app)
        await _open_action(pilot, screen, "take-over")
        await _set_target(pilot, screen, task.id)
        await _set_role(pilot, screen, Role.IMPLEMENTER)
        await pilot.click("#palette-submit")
        await wait_until(clock.has_pending_sleep, timeout=UI_WAIT_SECONDS)
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
        assert screen.query_one("#task-id", Input).value == task.id
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
    app = _palette_app(reader, commands, queries, _selection_must_not_run)

    async with app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        screen = _screen(app)
        await _open_action(pilot, screen, "take-over")
        await _set_target(pilot, screen, task_id)
        assert screen.query_one("#palette-submit", Button).disabled
        assert expected in _static_text(screen, "#palette-action-state")
        assert not queue.pending()
