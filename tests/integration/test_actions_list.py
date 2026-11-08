from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from typing import TYPE_CHECKING, Any, cast

import pytest
from textual.widgets import Button, Select, Static, TextArea

from shikiru.agents.contracts import Rung
from shikiru.bootstrap import build_lifecycle, build_operator, build_project_starter
from shikiru.clock import SystemClock
from shikiru.config.loader import load_settings
from shikiru.dashboard.actions_list import ActionItem, ActionsListScreen
from shikiru.dashboard.contract import (
    ExtensionEntry,
    PaletteRequest,
    ProjectRouteChoice,
    TaskPaletteRoute,
)
from shikiru.dashboard.palette import PaletteScreen
from shikiru.dashboard.palette_app import PaletteApp
from shikiru.dashboard.resolution import ResolutionScreen
from shikiru.dashboard.task_picker import TaskPickerScreen
from shikiru.domain.commands import CommandKind, CommandStatus
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import EscalationReason, Hold, Mode, Role, Status, Step
from shikiru.engine.daemon import engine_is_running
from shikiru.operator.commands import AckStatus
from shikiru.paths import engine_lock_path, engine_log_path, project_config_path, state_db_path
from shikiru.process import ProcessIdentity, identify_pid
from shikiru.store.database import Database
from shikiru.store.reader import StateReader
from shikiru.store.runs import RunStore
from shikiru.store.tasks import TaskStore
from shikiru.tmux.client import Tmux, exact_target
from shikiru.tmux.session import session_name
from shikiru.ui.tokens import SELECTED_SURFACE_COLOR
from tests.support.actions import (
    PENDING,
    ScreenHost,
    action_list,
    actions_screen,
    assert_role_focus_and_tab_order,
    is_composed,
    open_row,
    row_line,
    selected_label,
    structure,
    title_text,
)
from tests.support.cli_project import prepare_project, tmux_environment
from tests.support.clock import FakeClock
from tests.support.dashboard import enter_text
from tests.support.engine_project import ENGINE_START_OVERRIDE
from tests.support.escalations import operator_services, resolution_factory, seed_escalation
from tests.support.store import seed_task
from tests.support.tmux import attach_tmux_client
from tests.support.waiting import (
    ACTION_CONFIRMATION_EXPIRY_SECONDS,
    UI_WAIT_SECONDS,
    wait_until,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from textual.pilot import Pilot

    from shikiru.operator.lifecycle import ProjectLifecycle, StartResult
    from shikiru.operator.services import OperatorServices
    from shikiru.store.commands import CommandQueue
    from tests.support.store import Connection
    from tests.support.tmux import AttachedTmuxClient, TmuxServer

_START = datetime(2026, 1, 1, tzinfo=UTC)
_APP_SIZE = (100, 40)
_LIVE_RUNG = Rung(agent="codex", model="sol", effort="high")
_EXTENSION = ExtensionEntry("widget-extra", "Extra view")
_PANE = "%7"
_LEADER_ROLE_PANE = "%7"
_IMPLEMENTER_ROLE_PANE = "%8"
_DOT = "\N{MIDDLE DOT}"
_CONFIRMED_GLYPH = "\N{CHECK MARK}"
_MORE = "More\N{HORIZONTAL ELLIPSIS}"
_FEWER = "Fewer\N{HORIZONTAL ELLIPSIS}"
_SWITCH = "Switch task\N{HORIZONTAL ELLIPSIS}"
_PROJECT_ROWS = ["# Project", "Submit task", "Start engine", _SWITCH]
_DASHBOARD_PROJECT_ROWS = [
    "# Project",
    "Submit task",
    "Start engine",
    "Command history",
    "Diagnostics",
    _SWITCH,
]
_EXTENSION_ROWS = ["# Extensions", "Extra view"]
_EXTRA_ROWS = ["Pause now", "Priority", "Retry", "Rung", "Export evidence"]
_LIFECYCLE_CONFIG = """
[project]
name = "task248"

[agents.fake]

[roles.leader]
ladder = [{agent = "fake", model = "default"}]

[roles.implementer]
escalate_after = 2
ladder = [{agent = "fake", model = "default"}]

[roles.helper]
ladder = [{agent = "fake", model = "default"}]
"""
_INVALID_LIFECYCLE_CONFIG = f"{_LIFECYCLE_CONFIG}\n[agents.fake.options]\nunexpected = true\n"
_PROJECT_NAME_OVERRIDE = "project.name=task248-overridden"
_START_OVERRIDES = (_PROJECT_NAME_OVERRIDE, ENGINE_START_OVERRIDE)
_HOME_PANE_FORMAT = "#{pane_id}\t#{pane_pid}\t#{pane_dead}"
_PANE_ALIVE = "0"
_ENGINE_RUNNING = "Engine running"
_ENGINE_STOPPED = "Engine stopped"
_ENGINE_ALREADY_STOPPED = "engine is already stopped"
_START_ENGINE = "Start engine"
_STOP_ENGINE = "Stop engine"
_INITIAL_START_ENGINE = "Enter start engine"
_INITIAL_STOP_ENGINE = "Enter stop engine"
_CONFIRM_STOP_ENGINE = "Enter again"
_ENTER_TMUX_KEY = "Enter"
_TASK_IN_DECISION = "A held consult task"
_ESCAPE_TMUX_KEY = "Escape"
_ACTIONS_TMUX_KEY = "C-p"
_END_TMUX_KEY = "End"
_UP_TMUX_KEY = "Up"


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


def _running_with_both_roles(
    connection: Connection, clock: FakeClock, task_id: str, project_dir: Path
) -> None:
    _running(connection, clock, task_id, project_dir)
    store = TaskStore(connection, clock)
    store.set_panes(
        task_id, {Role.LEADER: _LEADER_ROLE_PANE, Role.IMPLEMENTER: _IMPLEMENTER_ROLE_PANE}
    )
    _start_run(connection, clock, task_id, Role.LEADER)


def _owned(connection: Connection, clock: FakeClock, task_id: str, project_dir: Path) -> None:
    del project_dir
    store = TaskStore(connection, clock)
    store.set_panes(task_id, {Role.LEADER: _PANE})
    RunStore(connection, clock).mark_taken_over(_start_run(connection, clock, task_id, Role.LEADER))
    store.set_holds(task_id, frozenset({Hold.PAUSED}))


def _owned_with_both_roles(
    connection: Connection, clock: FakeClock, task_id: str, project_dir: Path
) -> None:
    _owned(connection, clock, task_id, project_dir)
    store = TaskStore(connection, clock)
    store.set_panes(
        task_id, {Role.LEADER: _LEADER_ROLE_PANE, Role.IMPLEMENTER: _IMPLEMENTER_ROLE_PANE}
    )
    RunStore(connection, clock).mark_taken_over(
        _start_run(connection, clock, task_id, Role.IMPLEMENTER)
    )


def _escalated(connection: Connection, clock: FakeClock, task_id: str, project_dir: Path) -> None:
    seed_escalation(connection, clock, task_id, project_dir, EscalationReason.DECISION_REQUIRED)


_TASK_ROWS = ["# Task", "Message", "Pause", "Cancel", _MORE]
_DASHBOARD: dict[str, Any] = {
    "task_routes_available": True,
    "project_diagnostics_available": True,
    "extensions": (_EXTENSION,),
}
_TAIL = [*_EXTENSION_ROWS, *_DASHBOARD_PROJECT_ROWS]


@dataclass(frozen=True)
class _Case:
    prepare: Callable[[Connection, FakeClock, str, Path], None]
    flags: dict[str, Any]
    expected: list[str]


_CASES = (
    pytest.param(_Case(_queued, _DASHBOARD, [*_TASK_ROWS, *_TAIL]), id="queued-in-the-dashboard"),
    pytest.param(
        _Case(
            _running,
            _DASHBOARD,
            ["# Task", "Message", "Take over", "Pause", "Cancel", _MORE, *_TAIL],
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
        expected_title = f"Actions {_DOT} {task.id} Fix the login flow"
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
            lambda: structure(screen) == [*_EXTENSION_ROWS, *_DASHBOARD_PROJECT_ROWS],
            timeout=UI_WAIT_SECONDS,
        )
        assert title_text(screen) == f"Actions {_DOT} Project"


@pytest.mark.parametrize(
    "action",
    [
        pytest.param("Message", id="message"),
        pytest.param("Take over", id="take-over"),
        pytest.param("Hand back", id="hand-back"),
    ],
)
async def test_standalone_role_action_popups_focus_role_and_follow_tab_order(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
    action: str,
) -> None:
    clock = FakeClock(_START)
    task = seed_task(connection, clock, title="Role action target", mode=Mode.IMPLEMENT)
    prepare = {
        "Message": _queued,
        "Take over": _running_with_both_roles,
        "Hand back": _owned_with_both_roles,
    }[action]
    prepare(connection, clock, task.id, tmp_path)
    commands, queries, _ = operator_services(connection, reader, clock, tmp_path)
    app = PaletteApp(
        commands,
        queries,
        _no_pane_selection,
        on_change=reader.watch,
        action_confirmation_seconds=ACTION_CONFIRMATION_EXPIRY_SECONDS,
        initial_context=PaletteRequest(task_id=task.id),
    )

    async with app.run_test(size=_APP_SIZE) as pilot:
        listing = await _wait_for_list(app)
        await open_row(pilot, listing, action)
        popup = await _wait_for_popup(app)
        next_control = popup.query_one("#message" if action == "Message" else "#palette-submit")
        await assert_role_focus_and_tab_order(app, pilot, popup, next_control)


def _has_result(host: ScreenHost, expected: object) -> bool:
    return host.result == expected


async def _no_pane_selection(pane_id: str) -> None:
    del pane_id
    raise AssertionError


async def _home_pane(server: TmuxServer, session: str) -> tuple[str, int] | None:
    result = await server.run(
        "list-panes",
        "-t",
        f"{session}:dashboard",
        "-F",
        _HOME_PANE_FORMAT,
        check=False,
    )
    if result.returncode != 0:
        return None
    fields = result.stdout.strip().split("\t", 2)
    if len(fields) != 3 or fields[2] != _PANE_ALIVE:
        return None
    return fields[0], int(fields[1])


async def _home_pane_exists(server: TmuxServer, session: str) -> bool:
    return await _home_pane(server, session) is not None


async def _pane_shows(server: TmuxServer, pane_id: str, text: str) -> bool:
    result = await server.run("capture-pane", "-p", "-t", pane_id, check=False)
    return result.returncode == 0 and text in result.stdout


async def _pane_shows_all(server: TmuxServer, pane_id: str, *texts: str) -> bool:
    for text in texts:
        if not await _pane_shows(server, pane_id, text):
            return False
    return True


def _popup(app: PaletteApp) -> PaletteScreen | None:
    return app.screen if isinstance(app.screen, PaletteScreen) else None


async def _wait_for_list(app: PaletteApp) -> ActionsListScreen:
    await wait_until(
        lambda: isinstance(app.screen, ActionsListScreen) and is_composed(app.screen),
        timeout=UI_WAIT_SECONDS,
    )
    return cast("ActionsListScreen", app.screen)


async def _wait_for_popup(app: PaletteApp) -> PaletteScreen:
    await wait_until(
        lambda: (popup := _popup(app)) is not None and bool(popup.query("#palette-submit")),
        timeout=UI_WAIT_SECONDS,
    )
    popup = _popup(app)
    assert popup is not None
    return popup


async def _keep_a_message_draft(
    app: PaletteApp, pilot: Pilot[None], listing: ActionsListScreen, task_id: str
) -> None:
    await open_row(pilot, listing, "Message")
    popup = await _wait_for_popup(app)
    role = cast("Select[str]", popup.query_one("#role", Select))
    message = popup.query_one("#message", TextArea)
    await wait_until(lambda: app.focused is role, timeout=UI_WAIT_SECONDS)
    await pilot.press("tab")
    await wait_until(lambda: app.focused is message, timeout=UI_WAIT_SECONDS)
    await enter_text(pilot, "draft")
    await pilot.press("escape")
    assert await _wait_for_list(app) is listing
    await open_row(pilot, listing, "Message")
    popup = await _wait_for_popup(app)
    await wait_until(
        lambda: title_text(popup) == f"Message {_DOT} {task_id}", timeout=UI_WAIT_SECONDS
    )
    role = cast("Select[str]", popup.query_one("#role", Select))
    await wait_until(
        lambda: "draft" in popup.query_one("#message", TextArea).text and app.focused is role,
        timeout=UI_WAIT_SECONDS,
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
        visible_rows = structure(listing)
        extras_start = visible_rows.index(_FEWER) + 1
        assert visible_rows[extras_start : extras_start + len(_EXTRA_ROWS)] == _EXTRA_ROWS
        await pilot.press("enter")
        await wait_until(lambda: _MORE in structure(listing), timeout=UI_WAIT_SECONDS)
        assert "Pause now" not in structure(listing)

        await open_row(pilot, listing, _SWITCH)
        await wait_until(lambda: isinstance(app.screen, TaskPickerScreen), timeout=UI_WAIT_SECONDS)
        await pilot.press("up", "enter")
        assert await _wait_for_list(app) is listing
        await wait_until(lambda: "Take over" in structure(listing), timeout=UI_WAIT_SECONDS)
        assert title_text(listing).startswith(f"Actions {_DOT} {second.id} ")
        await wait_until(lambda: selected_label(listing) == "Message", timeout=UI_WAIT_SECONDS)

        await pilot.press("escape")
        await wait_until(lambda: not app.is_running, timeout=UI_WAIT_SECONDS)


async def _start_project_from_actions(
    app: PaletteApp,
    pilot: Pilot[None],
    listing: ActionsListScreen,
    lock_path: Path,
    start_attempts: list[StartResult | None],
    *,
    services: OperatorServices,
    already_running: bool = False,
    startup_gate: tuple[asyncio.Event, asyncio.Event] | None = None,
) -> None:
    await open_row(pilot, listing, "Start engine")
    popup = await _wait_for_popup(app)
    await wait_until(
        lambda: not popup.query_one("#palette-submit", Button).disabled,
        timeout=UI_WAIT_SECONDS,
    )
    start_count = len(start_attempts)
    history_size = services.queries.command_history().total_count
    await pilot.press("enter")
    if startup_gate is not None:
        entered, release = startup_gate
        try:
            await wait_until(entered.is_set, timeout=UI_WAIT_SECONDS)
            await pilot.press("enter")
            await wait_until(
                lambda: (
                    app.screen is popup
                    and len(start_attempts) == start_count + 1
                    and popup.query_one("#palette-submit", Button).disabled
                ),
                timeout=UI_WAIT_SECONDS,
            )
            assert len(start_attempts) == start_count + 1
        finally:
            release.set()
    await wait_until(
        lambda: (
            len(start_attempts) == start_count + 1
            and start_attempts[-1] is not None
            and engine_is_running(lock_path)
            and app.screen is listing
            and "Stop engine" in structure(listing)
        ),
        timeout=UI_WAIT_SECONDS,
    )
    result = start_attempts[-1]
    assert result is not None
    assert result.started is not already_running
    assert result.session == exact_target(session_name("task248-overridden"))
    assert services.queries.command_history().total_count == history_size


async def _stop_project_from_actions(
    app: PaletteApp,
    pilot: Pilot[None],
    listing: ActionsListScreen,
    lock_path: Path,
) -> None:
    await open_row(pilot, listing, "Stop engine")
    popup = await _wait_for_popup(app)
    await pilot.press("enter")
    await wait_until(
        lambda: (
            "Enter again to stop engine" in str(popup.query_one("#palette-submit", Button).label)
        ),
        timeout=UI_WAIT_SECONDS,
    )
    await pilot.press("enter")
    await wait_until(lambda: not engine_is_running(lock_path), timeout=UI_WAIT_SECONDS)
    await wait_until(lambda: app.screen is listing, timeout=UI_WAIT_SECONDS)


async def _refuse_stale_stop(
    app: PaletteApp,
    pilot: Pilot[None],
    listing: ActionsListScreen,
    root: Path,
    *,
    lifecycle: ProjectLifecycle,
    services: OperatorServices,
    target: str,
    tmux: Tmux,
    client: AttachedTmuxClient,
) -> None:
    await open_row(pilot, listing, "Stop engine")
    popup = await _wait_for_popup(app)
    await wait_until(lambda: bool(popup.query("#palette-submit")), timeout=UI_WAIT_SECONDS)
    button = popup.query_one("#palette-submit", Button)
    await wait_until(lambda: not button.disabled, timeout=UI_WAIT_SECONDS)
    await pilot.press("enter")
    await wait_until(
        lambda: "Enter again to stop engine" in str(button.label), timeout=UI_WAIT_SECONDS
    )
    stopped = await lifecycle.stop(root, (), kill_session=False)
    assert stopped.status is AckStatus.DONE
    history_size = services.queries.command_history().total_count
    action_state = popup.query_one("#palette-action-state", Static)
    result = popup.query_one("#palette-result", Static)
    await pilot.press("enter")
    await wait_until(
        lambda: (
            bool(str(result.content))
            or (button.disabled and action_state.display and bool(str(action_state.content)))
        ),
        timeout=UI_WAIT_SECONDS,
    )
    result_text = str(result.content)
    if result_text:
        assert _ENGINE_ALREADY_STOPPED in result_text
    else:
        assert button.disabled
        assert action_state.display
        assert str(action_state.content)
    assert services.queries.command_history().total_count == history_size
    assert not engine_is_running(engine_lock_path(root))
    assert await tmux.target_exists(target)
    assert client.process.returncode is None
    await pilot.press("escape")
    await wait_until(lambda: "Start engine" in structure(listing), timeout=UI_WAIT_SECONDS)


async def _show_start_failure(
    app: PaletteApp,
    pilot: Pilot[None],
    listing: ActionsListScreen,
    *,
    config: Path,
    lock_path: Path,
    services: OperatorServices,
    history_size: int,
) -> None:
    await asyncio.to_thread(config.write_text, _INVALID_LIFECYCLE_CONFIG)
    await open_row(pilot, listing, "Start engine")
    popup = await _wait_for_popup(app)
    button = popup.query_one("#palette-submit", Button)
    await pilot.press("enter")
    await wait_until(
        lambda: (
            "invalid options for the fake agent"
            in str(popup.query_one("#palette-result", Static).content)
        ),
        timeout=UI_WAIT_SECONDS,
    )
    assert not engine_is_running(lock_path)
    assert services.queries.command_history().total_count == history_size
    await wait_until(lambda: not button.disabled, timeout=UI_WAIT_SECONDS)


async def _retry_project_start(
    app: PaletteApp,
    pilot: Pilot[None],
    listing: ActionsListScreen,
    *,
    config: Path,
    lock_path: Path,
    start_attempts: list[StartResult | None],
    target: str,
) -> None:
    start_count = len(start_attempts)
    await asyncio.to_thread(config.write_text, _LIFECYCLE_CONFIG)
    await pilot.press("enter")
    await wait_until(
        lambda: (
            len(start_attempts) == start_count + 1
            and start_attempts[-1] is not None
            and engine_is_running(lock_path)
            and app.screen is listing
        ),
        timeout=UI_WAIT_SECONDS,
    )
    result = start_attempts[-1]
    assert result is not None
    assert result.started
    assert result.session == target
    await wait_until(lambda: "Stop engine" in structure(listing), timeout=UI_WAIT_SECONDS)


async def _prepare_lifecycle_project(
    tmp_path: Path,
    tmux_server: TmuxServer,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path]:
    prepared = await prepare_project(tmp_path)
    environment = tmux_environment(prepared.root, tmux_server)
    monkeypatch.setenv("PATH", environment["PATH"])
    monkeypatch.setenv("NO_COLOR", "1")
    config = project_config_path(prepared.root)
    config.parent.mkdir(exist_ok=True)
    config.write_text(_LIFECYCLE_CONFIG)
    return prepared.root, config


async def test_standalone_project_actions_start_stop_and_report_start_failure(
    tmp_path: Path,
    tmux_server: TmuxServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root, config = await _prepare_lifecycle_project(tmp_path, tmux_server, monkeypatch)
    clock = SystemClock()
    settings = load_settings(project_root)
    services = build_operator(project_root, settings, clock=clock)
    lifecycle = build_lifecycle()
    lock_path = engine_lock_path(project_root)
    start_project_engine = build_project_starter(project_root, _START_OVERRIDES)
    start_attempts: list[StartResult | None] = []
    startup_gate = (asyncio.Event(), asyncio.Event())

    async def start_engine() -> StartResult:
        attempt = len(start_attempts)
        start_attempts.append(None)
        if attempt == 0:
            startup_gate[0].set()
            await startup_gate[1].wait()
        if attempt == 1:
            concurrent_start = await lifecycle.start(project_root, _START_OVERRIDES, attach=False)
            assert concurrent_start.started
        result = await start_project_engine()
        start_attempts[attempt] = result
        return result

    app = PaletteApp(
        services.commands,
        services.queries,
        _no_pane_selection,
        on_change=services.reader.watch,
        action_confirmation_seconds=settings.dashboard.action_confirmation_seconds,
        engine_state_refresh_hz=settings.dashboard.motion.refresh_hz,
        start_engine=start_engine,
    )
    target = exact_target(session_name("task248-overridden"))
    tmux = Tmux(
        tmux_server.runner,
        socket=tmux_server.socket_name,
        env=tmux_server.env,
    )
    try:
        async with app.run_test(size=_APP_SIZE) as pilot:
            listing = await _wait_for_list(app)
            await wait_until(lambda: "Start engine" in structure(listing), timeout=UI_WAIT_SECONDS)
            await _start_project_from_actions(
                app,
                pilot,
                listing,
                lock_path,
                start_attempts,
                services=services,
                startup_gate=startup_gate,
            )
            assert await tmux.target_exists(target)

            async with attach_tmux_client(tmux_server, target) as client:
                await wait_until(
                    lambda: "Stop engine" in structure(listing), timeout=UI_WAIT_SECONDS
                )
                await _stop_project_from_actions(app, pilot, listing, lock_path)
                assert await tmux.target_exists(target)
                assert client.process.returncode is None

                await wait_until(
                    lambda: "Start engine" in structure(listing), timeout=UI_WAIT_SECONDS
                )
                await _start_project_from_actions(
                    app,
                    pilot,
                    listing,
                    lock_path,
                    start_attempts,
                    services=services,
                    already_running=True,
                )
                await wait_until(
                    lambda: "Stop engine" in structure(listing), timeout=UI_WAIT_SECONDS
                )
                await _refuse_stale_stop(
                    app,
                    pilot,
                    listing,
                    project_root,
                    lifecycle=lifecycle,
                    services=services,
                    target=target,
                    tmux=tmux,
                    client=client,
                )
                history_size = services.queries.command_history().total_count
                await _show_start_failure(
                    app,
                    pilot,
                    listing,
                    config=config,
                    lock_path=lock_path,
                    services=services,
                    history_size=history_size,
                )
                await _retry_project_start(
                    app,
                    pilot,
                    listing,
                    config=config,
                    lock_path=lock_path,
                    start_attempts=start_attempts,
                    target=target,
                )
                assert await tmux.target_exists(target)
    finally:
        if engine_is_running(lock_path):
            await lifecycle.stop(project_root, (), kill_session=False)
        services.close()


def _seed_dashboard_decision_state(root: Path, clock: FakeClock) -> tuple[StateReader, str]:
    database = Database(state_db_path(root))
    connection = database.connect()
    task = seed_task(connection, clock, title=_TASK_IN_DECISION, mode=Mode.IMPLEMENT)
    runs = RunStore(connection, clock)
    panes: dict[Role, str] = {}
    for role, pane_id in (
        (Role.LEADER, _LEADER_ROLE_PANE),
        (Role.IMPLEMENTER, _IMPLEMENTER_ROLE_PANE),
    ):
        run = runs.start(task.id, role, _LIVE_RUNG)
        runs.set_session(run.id, f"{role.value}-session", "fake --resume")
        panes[role] = pane_id
    TaskStore(connection, clock).set_panes(task.id, panes)
    seed_escalation(connection, clock, task.id, root, EscalationReason.DECISION_REQUIRED)
    connection.close()
    return StateReader(database, clock), task.id


async def _stop_engine_through_dashboard(
    tmux_server: TmuxServer,
    pane_id: str,
    *,
    state_reader: StateReader,
    task_id: str,
    lock_path: Path,
    root: Path,
) -> None:
    expected_title = f"Actions {_DOT} {task_id} {_TASK_IN_DECISION}"
    await tmux_server.run("send-keys", "-t", pane_id, _ACTIONS_TMUX_KEY)
    await wait_until(
        lambda: _pane_shows_all(tmux_server, pane_id, expected_title, _STOP_ENGINE),
        timeout=UI_WAIT_SECONDS,
    )
    await tmux_server.run(
        "send-keys",
        "-t",
        pane_id,
        _END_TMUX_KEY,
        _UP_TMUX_KEY,
        _UP_TMUX_KEY,
        _UP_TMUX_KEY,
        _ENTER_TMUX_KEY,
    )
    await wait_until(
        lambda: _pane_shows(tmux_server, pane_id, _INITIAL_STOP_ENGINE),
        timeout=UI_WAIT_SECONDS,
    )
    assert engine_is_running(lock_path), engine_log_path(root).read_text()
    commands_before_stop = state_reader.command_history(limit=10).total_count
    await tmux_server.run("send-keys", "-t", pane_id, _ENTER_TMUX_KEY)
    await wait_until(
        lambda: _pane_shows(tmux_server, pane_id, _CONFIRM_STOP_ENGINE),
        timeout=UI_WAIT_SECONDS,
    )
    assert state_reader.command_history(limit=10).total_count == commands_before_stop
    await tmux_server.run("send-keys", "-t", pane_id, _ENTER_TMUX_KEY)
    await wait_until(
        lambda: _stop_command_is_done(state_reader, commands_before_stop),
        timeout=UI_WAIT_SECONDS,
    )
    await wait_until(lambda: not engine_is_running(lock_path), timeout=UI_WAIT_SECONDS)
    await wait_until(
        lambda: _pane_shows_all(tmux_server, pane_id, expected_title, _START_ENGINE),
        timeout=UI_WAIT_SECONDS,
    )


async def _restart_engine_through_dashboard(
    tmux_server: TmuxServer,
    pane_id: str,
    lock_path: Path,
    task_id: str,
) -> None:
    expected_title = f"Actions {_DOT} {task_id} {_TASK_IN_DECISION}"
    await wait_until(
        lambda: _pane_shows_all(tmux_server, pane_id, expected_title, _START_ENGINE),
        timeout=UI_WAIT_SECONDS,
    )
    await tmux_server.run(
        "send-keys",
        "-t",
        pane_id,
        _END_TMUX_KEY,
        _UP_TMUX_KEY,
        _UP_TMUX_KEY,
        _UP_TMUX_KEY,
        _ENTER_TMUX_KEY,
    )
    await wait_until(
        lambda: _pane_shows(tmux_server, pane_id, _INITIAL_START_ENGINE),
        timeout=UI_WAIT_SECONDS,
    )
    await tmux_server.run("send-keys", "-t", pane_id, _ENTER_TMUX_KEY)
    await wait_until(lambda: engine_is_running(lock_path), timeout=UI_WAIT_SECONDS)
    await wait_until(
        lambda: _pane_shows_all(tmux_server, pane_id, _STOP_ENGINE, expected_title),
        timeout=UI_WAIT_SECONDS,
    )
    await tmux_server.run("send-keys", "-t", pane_id, _ESCAPE_TMUX_KEY)
    await wait_until(
        lambda: _pane_shows(tmux_server, pane_id, _TASK_IN_DECISION), timeout=UI_WAIT_SECONDS
    )


async def _assert_home_process_survives(
    tmux_server: TmuxServer,
    session: str,
    expected_home: tuple[str, int],
    identity: ProcessIdentity,
) -> None:
    current_home = await _home_pane(tmux_server, session)
    assert current_home is not None
    assert current_home[0] == expected_home[0]
    assert current_home[1] == expected_home[1]
    assert identity.matches(identify_pid(expected_home[1]))


async def _assert_home_responds_after_restart(
    tmux_server: TmuxServer,
    session: str,
    home: tuple[str, int],
    identity: ProcessIdentity,
    task_id: str,
) -> None:
    pane_id = home[0]
    await tmux_server.run("send-keys", "-t", pane_id, _ACTIONS_TMUX_KEY)
    await wait_until(
        lambda: _pane_shows_all(
            tmux_server,
            pane_id,
            _STOP_ENGINE,
            f"Actions {_DOT} {task_id} {_TASK_IN_DECISION}",
        ),
        timeout=UI_WAIT_SECONDS,
    )
    await _assert_home_process_survives(tmux_server, session, home, identity)
    await tmux_server.run("send-keys", "-t", pane_id, _ESCAPE_TMUX_KEY)
    await wait_until(
        lambda: _pane_shows(tmux_server, pane_id, _TASK_IN_DECISION), timeout=UI_WAIT_SECONDS
    )


async def test_cli_dashboard_survives_engine_stop_and_restarts_through_the_same_home(
    tmp_path: Path,
    tmux_server: TmuxServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = await prepare_project(tmp_path)
    environment = tmux_environment(prepared.root, tmux_server)
    monkeypatch.setenv("PATH", environment["PATH"])
    monkeypatch.setenv("NO_COLOR", "1")
    config = project_config_path(prepared.root)
    config.parent.mkdir(exist_ok=True)
    config.write_text(_LIFECYCLE_CONFIG)
    clock = FakeClock(_START)
    state_reader, task_id = _seed_dashboard_decision_state(prepared.root, clock)
    lifecycle = build_lifecycle()
    lock_path = engine_lock_path(prepared.root)

    try:
        started = await lifecycle.start(prepared.root, (ENGINE_START_OVERRIDE,), attach=False)
        async with attach_tmux_client(tmux_server, started.session) as client:
            await wait_until(
                lambda: _home_pane_exists(tmux_server, started.session),
                timeout=UI_WAIT_SECONDS,
            )
            home = await _home_pane(tmux_server, started.session)
            assert home is not None
            pane_id, dashboard_pid = home
            dashboard_identity = identify_pid(dashboard_pid)
            assert dashboard_identity is not None
            await wait_until(
                lambda: _pane_shows(tmux_server, pane_id, _ENGINE_RUNNING),
                timeout=UI_WAIT_SECONDS,
            )
            await wait_until(
                lambda: _pane_shows(tmux_server, pane_id, _TASK_IN_DECISION),
                timeout=UI_WAIT_SECONDS,
            )
            await _stop_engine_through_dashboard(
                tmux_server,
                pane_id,
                state_reader=state_reader,
                task_id=task_id,
                lock_path=lock_path,
                root=prepared.root,
            )
            await tmux_server.run("send-keys", "-t", pane_id, _ESCAPE_TMUX_KEY)
            await wait_until(
                lambda: _pane_shows_all(tmux_server, pane_id, _ENGINE_STOPPED, _TASK_IN_DECISION),
                timeout=UI_WAIT_SECONDS,
            )
            await _assert_home_process_survives(
                tmux_server, started.session, home, dashboard_identity
            )
            assert client.process.returncode is None
            await tmux_server.run("send-keys", "-t", pane_id, _ACTIONS_TMUX_KEY)
            await wait_until(
                lambda: _pane_shows_all(
                    tmux_server,
                    pane_id,
                    f"Actions {_DOT} {task_id} {_TASK_IN_DECISION}",
                    _START_ENGINE,
                ),
                timeout=UI_WAIT_SECONDS,
            )
            await _restart_engine_through_dashboard(tmux_server, pane_id, lock_path, task_id)
            await _assert_home_process_survives(
                tmux_server, started.session, home, dashboard_identity
            )
            assert client.process.returncode is None
            await _assert_home_responds_after_restart(
                tmux_server, started.session, home, dashboard_identity, task_id
            )
    finally:
        try:
            await lifecycle.stop(prepared.root, (), kill_session=True)
        finally:
            state_reader.close()


def _stop_command_is_done(reader: StateReader, previous_count: int) -> bool:
    history = reader.command_history(limit=10)
    return (
        history.total_count == previous_count + 1
        and history.entries[0].command.kind is CommandKind.STOP
        and history.entries[0].status is CommandStatus.DONE
    )


async def test_actions_list_returns_project_routes_extensions_and_answers_decisions(
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
        ("Command history", ProjectRouteChoice(TaskPaletteRoute.COMMANDS)),
        ("Diagnostics", ProjectRouteChoice(TaskPaletteRoute.DIAGNOSTICS)),
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
