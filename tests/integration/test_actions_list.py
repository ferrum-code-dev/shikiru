from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from typing import TYPE_CHECKING, Any, cast

import pytest
from textual.widgets import TextArea

from shikiru.agents.contracts import Rung
from shikiru.dashboard.actions_list import ActionItem, ActionsListScreen
from shikiru.dashboard.contract import (
    ExtensionEntry,
    PaletteRequest,
    TaskPaletteRoute,
    TaskRouteChoice,
)
from shikiru.dashboard.palette import PaletteScreen
from shikiru.dashboard.palette_app import PaletteApp
from shikiru.dashboard.resolution import ResolutionScreen
from shikiru.dashboard.task_picker import TaskPickerScreen
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import EscalationReason, Hold, Role, Status, Step
from shikiru.store.runs import RunStore
from shikiru.store.tasks import TaskStore
from shikiru.ui.tokens import SELECTED_SURFACE_COLOR
from tests.support.actions import (
    PENDING,
    ScreenHost,
    action_list,
    actions_screen,
    open_row,
    row_line,
    selected_label,
    structure,
    title_text,
)
from tests.support.clock import FakeClock
from tests.support.escalations import operator_services, resolution_factory, seed_escalation
from tests.support.store import seed_task
from tests.support.waiting import (
    ACTION_CONFIRMATION_EXPIRY_SECONDS,
    UI_WAIT_SECONDS,
    wait_until,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from textual.pilot import Pilot

    from shikiru.store.commands import CommandQueue
    from shikiru.store.reader import StateReader
    from tests.support.store import Connection

_START = datetime(2026, 1, 1, tzinfo=UTC)
_APP_SIZE = (100, 40)
_LIVE_RUNG = Rung(agent="codex", model="sol", effort="high")
_EXTENSION = ExtensionEntry("widget-extra", "Extra view")
_PANE = "%7"
_DOT = "\N{MIDDLE DOT}"
_CONFIRMED_GLYPH = "\N{CHECK MARK}"
_MORE = "More\N{HORIZONTAL ELLIPSIS}"
_FEWER = "Fewer\N{HORIZONTAL ELLIPSIS}"
_SWITCH = "Switch task\N{HORIZONTAL ELLIPSIS}"
_PROJECT_ROWS = ["# Project", "Submit task", "Stop engine", _SWITCH]
_VIEW_ROWS = ["# Views", "Evidence & reports", "Usage", "Commands", "Project diagnostics"]
_EXTENSION_ROWS = ["# Extensions", "Extra view"]
_EXTRA_ROWS = ["Pause now", "Priority", "Retry", "Rung", "Open session", "Export evidence"]


def _start_run(connection: Connection, clock: FakeClock, task_id: str, role: Role) -> int:
    runs = RunStore(connection, clock)
    run = runs.start(task_id, role, _LIVE_RUNG)
    runs.set_session(run.id, "session", "resume")
    return run.id


def _queued(connection: Connection, clock: FakeClock, task_id: str, project_dir: Path) -> None:
    del connection, clock, task_id, project_dir


def _running(connection: Connection, clock: FakeClock, task_id: str, project_dir: Path) -> None:
    del project_dir
    store = TaskStore(connection, clock)
    store.set_panes(task_id, {Role.IMPLEMENTER: _PANE})
    _start_run(connection, clock, task_id, Role.IMPLEMENTER)
    store.apply_transition(
        task_id, Transition.stay(store.require(task_id), step=Step.IMPLEMENT, status=Status.RUNNING)
    )


def _owned(connection: Connection, clock: FakeClock, task_id: str, project_dir: Path) -> None:
    del project_dir
    store = TaskStore(connection, clock)
    store.set_panes(task_id, {Role.LEADER: _PANE})
    RunStore(connection, clock).mark_taken_over(_start_run(connection, clock, task_id, Role.LEADER))
    store.set_holds(task_id, frozenset({Hold.PAUSED}))


def _escalated(connection: Connection, clock: FakeClock, task_id: str, project_dir: Path) -> None:
    seed_escalation(connection, clock, task_id, project_dir, EscalationReason.DECISION_REQUIRED)


_TASK_ROWS = ["# Task", "Message", "Pause", "Cancel", _MORE]
_DASHBOARD: dict[str, Any] = {
    "task_routes_available": True,
    "project_diagnostics_available": True,
    "extensions": (_EXTENSION,),
}
_TAIL = [*_VIEW_ROWS, *_EXTENSION_ROWS, *_PROJECT_ROWS]


@dataclass(frozen=True)
class _Case:
    prepare: Callable[[Connection, FakeClock, str, Path], None]
    flags: dict[str, Any]
    expected: list[str]
    role: str = "Leader"


_CASES = (
    pytest.param(_Case(_queued, _DASHBOARD, [*_TASK_ROWS, *_TAIL]), id="queued-in-the-dashboard"),
    pytest.param(
        _Case(
            _running,
            _DASHBOARD,
            ["# Task", "Message", "Take over", "Pause", "Cancel", _MORE, *_TAIL],
            role="Implementer",
        ),
        id="running-with-a-live-session",
    ),
    pytest.param(
        _Case(
            _escalated,
            _DASHBOARD,
            ["# Task", "Answer", "Message", "Pause", "Cancel", _MORE, *_TAIL],
        ),
        id="escalated-with-a-decision",
    ),
    pytest.param(
        _Case(
            _owned,
            _DASHBOARD,
            ["# Task", "Message", "Hand back", "Pause", "Resume", "Cancel", _MORE, *_TAIL],
        ),
        id="operator-owned-with-holds",
    ),
    pytest.param(
        _Case(_queued, {}, [*_TASK_ROWS, *_PROJECT_ROWS]),
        id="standalone-popup-without-views-or-extensions",
    ),
)


@pytest.mark.parametrize("case", _CASES)
async def test_actions_list_offers_only_what_applies_to_the_task_now(
    connection: Connection, reader: StateReader, tmp_path: Path, case: _Case
) -> None:
    clock = FakeClock(_START)
    task = seed_task(connection, clock, title="Fix the login flow")
    case.prepare(connection, clock, task.id, tmp_path)
    commands, queries, _ = operator_services(connection, reader, clock, tmp_path)
    screen = actions_screen(
        commands,
        queries,
        reader,
        _no_pane_selection,
        PaletteRequest(task_id=task.id),
        **case.flags,
    )
    host = ScreenHost(screen)

    async with host.run_test(size=_APP_SIZE) as pilot:
        await wait_until(lambda: structure(screen) == case.expected, timeout=UI_WAIT_SECONDS)
        expected_title = f"Actions {_DOT} {task.id} Fix the login flow {_DOT} {case.role}"
        assert title_text(screen) == expected_title
        assert selected_label(screen) == case.expected[1]
        await pilot.press("up")
        assert selected_label(screen) == _SWITCH


async def test_actions_list_without_a_task_offers_the_project_group(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    clock = FakeClock(_START)
    commands, queries, _ = operator_services(connection, reader, clock, tmp_path)
    screen = actions_screen(
        commands, queries, reader, _no_pane_selection, PaletteRequest(), **_DASHBOARD
    )
    host = ScreenHost(screen)

    async with host.run_test(size=_APP_SIZE):
        await wait_until(
            lambda: structure(screen) == [*_EXTENSION_ROWS, *_PROJECT_ROWS], timeout=UI_WAIT_SECONDS
        )
        assert title_text(screen) == f"Actions {_DOT} Project"


def _has_result(host: ScreenHost, expected: object) -> bool:
    return host.result == expected


async def _no_pane_selection(pane_id: str) -> None:
    del pane_id
    raise AssertionError


def _popup(app: PaletteApp) -> PaletteScreen | None:
    return app.screen if isinstance(app.screen, PaletteScreen) else None


async def _wait_for_list(app: PaletteApp) -> ActionsListScreen:
    await wait_until(lambda: isinstance(app.screen, ActionsListScreen), timeout=UI_WAIT_SECONDS)
    return cast("ActionsListScreen", app.screen)


async def _wait_for_popup(app: PaletteApp) -> PaletteScreen:
    await wait_until(lambda: _popup(app) is not None, timeout=UI_WAIT_SECONDS)
    popup = _popup(app)
    assert popup is not None
    return popup


async def _keep_a_message_draft(
    app: PaletteApp, pilot: Pilot[None], listing: ActionsListScreen, task_id: str
) -> None:
    await open_row(pilot, listing, "Message")
    await _wait_for_popup(app)
    await pilot.press(*"draft")
    await pilot.press("escape")
    assert await _wait_for_list(app) is listing
    await open_row(pilot, listing, "Message")
    popup = await _wait_for_popup(app)
    assert title_text(popup) == f"Message {_DOT} {task_id}"
    await wait_until(
        lambda: "draft" in popup.query_one("#message", TextArea).text, timeout=UI_WAIT_SECONDS
    )
    await pilot.press("escape")
    assert await _wait_for_list(app) is listing


async def _queue_a_pause_and_click_cancel(
    app: PaletteApp, pilot: Pilot[None], listing: ActionsListScreen, queue: CommandQueue
) -> None:
    await open_row(pilot, listing, "Pause")
    await _wait_for_popup(app)
    await pilot.press("enter")
    assert await _wait_for_list(app) is listing
    await wait_until(
        lambda: _CONFIRMED_GLYPH in row_line(listing, "Pause"), timeout=UI_WAIT_SECONDS
    )
    assert len(queue.pending()) == 1
    await wait_until(
        lambda: _CONFIRMED_GLYPH not in row_line(listing, "Pause"), timeout=UI_WAIT_SECONDS
    )
    cancel_row = next(
        item
        for item in action_list(listing).children
        if isinstance(item, ActionItem) and item.row.label == "Cancel"
    )
    await pilot.click(cancel_row)
    await _wait_for_popup(app)
    await pilot.press("escape")
    assert await _wait_for_list(app) is listing


async def test_actions_list_opens_popups_returns_to_the_list_and_switches_task(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    clock = FakeClock(_START)
    first = seed_task(connection, clock, title="First job")
    second = seed_task(connection, clock, title="Second job")
    _running(connection, clock, second.id, tmp_path)
    commands, queries, queue = operator_services(connection, reader, clock, tmp_path)
    app = PaletteApp(
        commands,
        queries,
        _no_pane_selection,
        on_change=reader.watch,
        action_confirmation_seconds=ACTION_CONFIRMATION_EXPIRY_SECONDS,
        initial_context=PaletteRequest(task_id=first.id),
    )

    async with app.run_test(size=_APP_SIZE) as pilot:
        listing = await _wait_for_list(app)
        await wait_until(lambda: selected_label(listing) == "Message", timeout=UI_WAIT_SECONDS)
        highlighted = action_list(listing).highlighted_child
        assert highlighted is not None
        assert highlighted.styles.background.hex.lower() == SELECTED_SURFACE_COLOR.lower()

        await _keep_a_message_draft(app, pilot, listing, first.id)
        assert queue.pending() == []
        await _queue_a_pause_and_click_cancel(app, pilot, listing, queue)

        await open_row(pilot, listing, _MORE)
        await wait_until(lambda: _FEWER in structure(listing), timeout=UI_WAIT_SECONDS)
        assert structure(listing)[structure(listing).index(_FEWER) + 1 :][:6] == _EXTRA_ROWS
        await pilot.press("enter")
        await wait_until(lambda: _MORE in structure(listing), timeout=UI_WAIT_SECONDS)
        assert "Pause now" not in structure(listing)

        await open_row(pilot, listing, _SWITCH)
        await wait_until(lambda: isinstance(app.screen, TaskPickerScreen), timeout=UI_WAIT_SECONDS)
        await pilot.press("up", "enter")
        assert await _wait_for_list(app) is listing
        await wait_until(lambda: "Take over" in structure(listing), timeout=UI_WAIT_SECONDS)
        assert title_text(listing).startswith(f"Actions {_DOT} {second.id} ")
        assert selected_label(listing) == "Message"

        await pilot.press("escape")
        await wait_until(lambda: not app.is_running, timeout=UI_WAIT_SECONDS)


async def test_actions_list_returns_views_extensions_and_answers_decisions(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    clock = FakeClock(_START)
    task = seed_task(connection, clock, title="Needs a decision")
    _escalated(connection, clock, task.id, tmp_path)
    commands, queries, _ = operator_services(connection, reader, clock, tmp_path)
    factory = resolution_factory(commands, queries, clock, reader)

    def listing(request: PaletteRequest) -> tuple[ScreenHost, ActionsListScreen]:
        screen = actions_screen(
            commands,
            queries,
            reader,
            _no_pane_selection,
            request,
            resolution_screen=factory,
            **_DASHBOARD,
        )
        return ScreenHost(screen), screen

    for label, expected in (
        ("Usage", TaskRouteChoice(task.id, TaskPaletteRoute.USAGE)),
        ("Extra view", _EXTENSION),
    ):
        host, screen = listing(PaletteRequest(task_id=task.id))
        async with host.run_test(size=_APP_SIZE) as pilot:
            await open_row(pilot, screen, label)
            await wait_until(partial(_has_result, host, expected), timeout=UI_WAIT_SECONDS)

    host, screen = listing(PaletteRequest(task_id=task.id))
    async with host.run_test(size=_APP_SIZE) as pilot:
        await open_row(pilot, screen, "Answer")
        await wait_until(lambda: isinstance(host.screen, ResolutionScreen), timeout=UI_WAIT_SECONDS)
        assert host.result == PENDING
        await pilot.press("escape")
        await wait_until(lambda: host.screen is screen, timeout=UI_WAIT_SECONDS)
        assert host.result == PENDING
