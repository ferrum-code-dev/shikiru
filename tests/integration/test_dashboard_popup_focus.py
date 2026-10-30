from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from textual.containers import VerticalScroll
from textual.widgets import Button, ListView, Static

from shikiru.config.loader import load_settings
from shikiru.dashboard.app import DashboardApp
from shikiru.dashboard.contract import PaletteRequest
from shikiru.dashboard.detail import TASK_DETAIL_BODY, TaskDetailBody
from shikiru.dashboard.help_keys import HelpTab
from shikiru.domain.escalation import options_for
from shikiru.domain.events import EventKind
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import EscalationReason, Mode, Status
from shikiru.operator.commands import OperatorCommands
from shikiru.operator.queries import OperatorQueries
from shikiru.store.commands import CommandQueue
from shikiru.store.escalations import EscalationStore
from shikiru.store.events import EventLog
from shikiru.store.tasks import TaskStore
from shikiru.ui.popup import POPUP_CLOSE_CLASS
from shikiru.ui.theme import SCROLLBAR_WIDTH
from shikiru.ui.tokens import STEEL_BLUE
from tests.support.actions import actions_screen
from tests.support.cells import foregrounds, rendered_lines
from tests.support.clock import FakeClock
from tests.support.config import write_minimal_project
from tests.support.dashboard import (
    StubServices,
    fixed_settings,
    open_command_history,
    open_usage_details,
    state_views,
    with_dashboard,
)
from tests.support.store import seed_task
from tests.support.waiting import UI_WAIT_SECONDS, wait_until

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

    from textual.pilot import Pilot
    from textual.screen import Screen
    from textual.widget import Widget

    from shikiru.store.reader import StateReader
    from tests.support.store import Connection

_START = datetime(2026, 1, 1, tzinfo=UTC)
_APP_SIZE = (120, 40)
_COMPACT_APP_SIZE = (48, 20)
_OVERFLOWING_HELP_SIZE = (70, 22)
_OVERFLOWING_WORDS = 800
_PROJECT_NAME = "popup-focus"


async def _await_popup(app: DashboardApp, pilot: Pilot[None], origin: Screen[Any]) -> Screen[Any]:
    await wait_until(lambda: app.screen is not origin, timeout=UI_WAIT_SECONDS)
    await pilot.pause()
    return origin


async def _open_help(app: DashboardApp, pilot: Pilot[None]) -> Screen[Any]:
    origin = app.screen
    await pilot.press("?")
    return await _await_popup(app, pilot, origin)


async def _open_command_history(app: DashboardApp, pilot: Pilot[None]) -> Screen[Any]:
    origin = app.screen
    await open_command_history(pilot)
    return await _await_popup(app, pilot, origin)


async def _open_usage_details(app: DashboardApp, pilot: Pilot[None]) -> Screen[Any]:
    origin = app.screen
    await open_usage_details(pilot)
    return await _await_popup(app, pilot, origin)


async def _open_standalone_palette(app: DashboardApp, pilot: Pilot[None]) -> Screen[Any]:
    origin = app.screen
    await pilot.press("ctrl+p")
    return await _await_popup(app, pilot, origin)


_ROUTES = (
    pytest.param(_open_help, "#workspace-help-scroll", id="help"),
    pytest.param(_open_command_history, "#command-history-filter", id="command-history"),
    pytest.param(_open_usage_details, "#context-widget-content", id="usage-details"),
    pytest.param(_open_standalone_palette, "#actions-list", id="standalone-palette"),
)


async def _select_pane(pane: str) -> None:
    del pane


def _seed(connection: Connection, clock: FakeClock) -> str:
    task = seed_task(connection, clock, title="Popup focus task", mode=Mode.IMPLEMENT)
    TaskStore(connection, clock).apply_transition(
        task.id,
        Transition.stay(task, step=task.step, status=Status.ESCALATED),
    )
    EscalationStore(connection, clock).open(
        task.id,
        step=task.step,
        reason=EscalationReason.DECISION_REQUIRED,
        headline=f"{task.id} · Keep the current behaviour.",
        packet_path=".shikiru/escalations/missing-packet.md",
        options=options_for(EscalationReason.DECISION_REQUIRED),
    )
    EventLog(connection, clock).append(
        EventKind.VERDICT,
        task_id=task.id,
        message="Leader approves",
        data={"decision": "accept"},
    )
    return task.id


def _dashboard(
    connection: Connection,
    reader: StateReader,
    project_dir: Path,
    clock: FakeClock,
    *,
    focus_task: str | None = None,
) -> DashboardApp:
    settings = load_settings(project_dir)
    commands = OperatorCommands(CommandQueue(connection, clock), clock, engine_up=lambda: False)
    queries = OperatorQueries(
        reader,
        project=_PROJECT_NAME,
        project_root=project_dir,
        engine_up=lambda: False,
        max_parallel=settings.orchestration.max_parallel,
        clock=clock,
    )

    async def open_palette(action: str) -> None:
        del action
        await app.push_screen(
            actions_screen(commands, queries, reader, _select_pane, PaletteRequest())
        )

    app = DashboardApp(
        focus_task=focus_task,
        views=state_views(
            reader,
            project_dir,
            clock,
            StubServices(),
            focus_task=focus_task,
            settings=settings,
        ),
        on_change=reader.watch,
        clock=clock,
        motion_interval_seconds=None,
        settings=fixed_settings(reader, with_dashboard(settings, animations=False)),
        open_palette=open_palette,
        widget_names=settings.dashboard.widgets,
        open_palette_context=None,
        tmux_detach_hint=None,
        detach_tmux_client=None,
    )
    return app


@pytest.fixture
def popup_app(connection: Connection, reader: StateReader, tmp_path: Path) -> DashboardApp:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(_START)
    _seed(connection, clock)
    return _dashboard(connection, reader, project_dir, clock)


@pytest.mark.parametrize(("open_popup", "target"), _ROUTES)
async def test_popup_starts_on_its_useful_control_and_closes_with_escape_or_its_close_button(
    popup_app: DashboardApp,
    open_popup: Callable[[DashboardApp, Pilot[None]], Awaitable[Screen[Any]]],
    target: str,
) -> None:
    async with popup_app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        origin = await open_popup(popup_app, pilot)
        assert popup_app.focused is popup_app.screen.query_one(target)
        await pilot.press("escape")
        await wait_until(lambda: popup_app.screen is origin, timeout=UI_WAIT_SECONDS)
        await open_popup(popup_app, pilot)
        screen = popup_app.screen
        close = screen.query_one(f".{POPUP_CLOSE_CLASS}", Button)
        for _ in range(len(screen.focus_chain)):
            if popup_app.focused is close:
                break
            await pilot.press("tab")
        assert popup_app.focused is close
        await pilot.press("enter")
        await wait_until(lambda: popup_app.screen is origin, timeout=UI_WAIT_SECONDS)


@pytest.mark.parametrize(
    ("open_popup", "selector", "key", "label"),
    [
        pytest.param(_open_help, f".{POPUP_CLOSE_CLASS}", "Esc", "\u00d7", id="help-close"),
        pytest.param(
            _open_help, "#workspace-help-copy", "Up/Down", "Select a task", id="help-copy"
        ),
        pytest.param(_open_command_history, f".{POPUP_CLOSE_CLASS}", "Esc", "\u00d7", id="history"),
    ],
)
@pytest.mark.usefixtures("colour_terminal")
async def test_popup_key_names_are_steel_blue_and_their_labels_are_not(
    popup_app: DashboardApp,
    open_popup: Callable[[DashboardApp, Pilot[None]], Awaitable[Screen[Any]]],
    selector: str,
    key: str,
    label: str,
) -> None:
    async with popup_app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        await open_popup(popup_app, pilot)
        widget = popup_app.screen.query_one(selector)
        assert set(foregrounds(widget, key)) == {STEEL_BLUE}
        assert STEEL_BLUE not in foregrounds(widget, label)


def _scrollbar_width(widget: Widget) -> object:
    return widget.styles.scrollbar_size_vertical


async def test_popup_bodies_and_task_lists_use_the_one_column_scrollbar(
    popup_app: DashboardApp,
) -> None:
    async with popup_app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        home = popup_app.screen
        assert _scrollbar_width(popup_app.query_one("#task-browser-list", ListView)) == (
            SCROLLBAR_WIDTH
        )
        await pilot.press("?")
        await wait_until(
            lambda: bool(popup_app.screen.query("#workspace-help-scroll")),
            timeout=UI_WAIT_SECONDS,
        )
        assert _scrollbar_width(popup_app.screen.query_one("#workspace-help-scroll")) == (
            SCROLLBAR_WIDTH
        )
        await pilot.press("escape")
        await wait_until(lambda: popup_app.screen is home, timeout=UI_WAIT_SECONDS)


def _copy_text(widget: Widget) -> str:
    return "\n".join("".join(char for char, _ in cells) for cells in rendered_lines(widget))


def _active_tabs(screen: Screen[Any]) -> list[str]:
    labels = screen.query(".help-tab").results(Static)
    return [
        str(label.content)
        for label in labels
        if set(foregrounds(label, str(label.content))) == {STEEL_BLUE}
    ]


@pytest.mark.parametrize(
    "case",
    [
        pytest.param((False, HelpTab.HOME, HelpTab.TASK, "right", "left"), id="home"),
        pytest.param((True, HelpTab.TASK, HelpTab.HOME, "left", "right"), id="task-details"),
    ],
)
@pytest.mark.usefixtures("colour_terminal")
async def test_help_opens_on_its_context_tab_and_left_right_switch_the_keys_from_the_top(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
    case: tuple[bool, HelpTab, HelpTab, str, str],
) -> None:
    standalone, opening, neighbour, key, back = case
    clock = FakeClock(_START)
    task_id = _seed(connection, clock)
    app = _dashboard(
        connection,
        reader,
        write_minimal_project(tmp_path),
        clock,
        focus_task=task_id if standalone else None,
    )
    async with app.run_test(size=_OVERFLOWING_HELP_SIZE) as pilot:
        await pilot.pause()
        await pilot.press("?")
        await wait_until(
            lambda: bool(app.screen.query("#workspace-help-scroll")), timeout=UI_WAIT_SECONDS
        )
        screen = app.screen
        body = screen.query_one("#workspace-help-scroll", VerticalScroll)
        copy = screen.query_one("#workspace-help-copy")
        await wait_until(lambda: _active_tabs(screen) == [opening.value], timeout=UI_WAIT_SECONDS)
        opening_keys = _copy_text(copy)
        hint = screen.query_one("#workspace-help-hint")
        assert _copy_text(screen.query_one("#workspace-help-context")).strip() == "Help"
        assert _copy_text(hint).strip() == "\N{LEFTWARDS ARROW} \N{RIGHTWARDS ARROW} switch section"
        assert set(foregrounds(hint, "\N{LEFTWARDS ARROW} \N{RIGHTWARDS ARROW}")) == {STEEL_BLUE}
        await pilot.press("pagedown")
        await wait_until(lambda: body.scroll_y > 0, timeout=UI_WAIT_SECONDS)

        await pilot.press(key)
        await wait_until(lambda: _active_tabs(screen) == [neighbour.value], timeout=UI_WAIT_SECONDS)
        assert body.scroll_y == 0
        assert _copy_text(copy) != opening_keys
        assert set(foregrounds(copy, "Up/Down")) == {STEEL_BLUE}

        await pilot.press(back)
        await wait_until(lambda: _active_tabs(screen) == [opening.value], timeout=UI_WAIT_SECONDS)
        assert _copy_text(copy) == opening_keys


def _seed_overflowing_task(connection: Connection, clock: FakeClock) -> str:
    title = " ".join(f"word{index}" for index in range(_OVERFLOWING_WORDS))
    return seed_task(connection, clock, title=title, mode=Mode.IMPLEMENT).id


_TASK_DETAIL_LAYOUTS = (
    pytest.param(_APP_SIZE, False, id="home-wide"),
    pytest.param(_COMPACT_APP_SIZE, False, id="home-compact"),
    pytest.param(_APP_SIZE, True, id="standalone"),
)


@pytest.mark.parametrize(("size", "standalone"), _TASK_DETAIL_LAYOUTS)
async def test_overflowing_task_detail_has_a_one_column_scrollbar(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
    size: tuple[int, int],
    standalone: bool,
) -> None:
    clock = FakeClock(_START)
    task_id = _seed_overflowing_task(connection, clock)
    app = _dashboard(
        connection,
        reader,
        write_minimal_project(tmp_path),
        clock,
        focus_task=task_id if standalone else None,
    )
    async with app.run_test(size=size) as pilot:
        body = app.query_one(f"#{TASK_DETAIL_BODY}", TaskDetailBody)
        await wait_until(lambda: body.max_scroll_y > 0, timeout=UI_WAIT_SECONDS)
        await pilot.pause()
        assert body.scrollable_content_region.width == (
            body.region.width - body.styles.gutter.width - SCROLLBAR_WIDTH
        )
        assert _scrollbar_width(body) == SCROLLBAR_WIDTH


async def test_help_body_scrolls_from_its_initial_focus_without_resizing_the_frame(
    popup_app: DashboardApp,
) -> None:
    async with popup_app.run_test(size=_OVERFLOWING_HELP_SIZE) as pilot:
        await pilot.pause()
        await pilot.press("?")
        await wait_until(
            lambda: bool(popup_app.screen.query("#workspace-help-scroll")),
            timeout=UI_WAIT_SECONDS,
        )
        await pilot.pause()
        help_screen = popup_app.screen
        body = help_screen.query_one("#workspace-help-scroll", VerticalScroll)
        frame = help_screen.query_one("#workspace-help-frame")
        frame_region = frame.region
        await pilot.press("pagedown")
        await wait_until(lambda: body.scroll_y > 0, timeout=UI_WAIT_SECONDS)
        assert frame.region == frame_region
