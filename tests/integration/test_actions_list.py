from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from typing import TYPE_CHECKING, Any, cast

import pytest
from textual.widgets import Button, Static, TextArea

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
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import EscalationReason, Hold, Role, Status, Step
from shikiru.engine.daemon import engine_is_running
from shikiru.operator.commands import AckStatus
from shikiru.paths import engine_lock_path, project_config_path
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
    open_row,
    row_line,
    selected_label,
    structure,
    title_text,
)
from tests.support.cli_project import prepare_project, tmux_environment
from tests.support.clock import FakeClock
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
    from shikiru.store.reader import StateReader
    from tests.support.store import Connection
    from tests.support.tmux import AttachedTmuxClient, TmuxServer

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
        assert selected_label(listing) == "Message"

        await pilot.press("escape")
        await wait_until(lambda: not app.is_running, timeout=UI_WAIT_SECONDS)


async def _start_project_from_actions(
    app: PaletteApp,
    pilot: Pilot[None],
    listing: ActionsListScreen,
    lock_path: Path,
    start_attempts: list[StartResult | None],
) -> None:
    await open_row(pilot, listing, "Start engine")
    popup = await _wait_for_popup(app)
    await wait_until(
        lambda: not popup.query_one("#palette-submit", Button).disabled,
        timeout=UI_WAIT_SECONDS,
    )
    start_count = len(start_attempts)
    await pilot.press("enter", "enter")
    await wait_until(
        lambda: (
            len(start_attempts) == start_count + 1
            and start_attempts[-1] is not None
            and engine_is_running(lock_path)
            and popup.query_one("#palette-submit", Button).disabled
        ),
        timeout=UI_WAIT_SECONDS,
    )
    result = start_attempts[-1]
    assert result is not None
    assert result.started
    assert result.session == exact_target(session_name("task248-overridden"))


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
    button = popup.query_one("#palette-submit", Button)
    await wait_until(lambda: not button.disabled, timeout=UI_WAIT_SECONDS)
    stopped = await lifecycle.stop(root, (), kill_session=False)
    assert stopped.status is AckStatus.DONE
    history_size = services.queries.command_history().total_count
    await pilot.press("enter")
    await wait_until(
        lambda: "Enter again to stop engine" in str(button.label), timeout=UI_WAIT_SECONDS
    )
    await pilot.press("enter")
    await wait_until(
        lambda: bool(str(popup.query_one("#palette-result", Static).content)),
        timeout=UI_WAIT_SECONDS,
    )
    assert services.queries.command_history().total_count == history_size
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
    pilot: Pilot[None],
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
        ),
        timeout=UI_WAIT_SECONDS,
    )
    result = start_attempts[-1]
    assert result is not None
    assert result.started
    assert result.session == target


async def test_standalone_project_actions_start_stop_and_report_start_failure(
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
    clock = SystemClock()
    settings = load_settings(prepared.root)
    services = build_operator(prepared.root, settings, clock=clock)
    lifecycle = build_lifecycle()
    lock_path = engine_lock_path(prepared.root)
    start_project_engine = build_project_starter(prepared.root, (_PROJECT_NAME_OVERRIDE,))
    start_attempts: list[StartResult | None] = []

    async def start_engine() -> StartResult:
        attempt = len(start_attempts)
        start_attempts.append(None)
        start_attempts[attempt] = await start_project_engine()
        result = start_attempts[attempt]
        assert result is not None
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
            await _start_project_from_actions(app, pilot, listing, lock_path, start_attempts)
            assert await tmux.target_exists(target)

            async with attach_tmux_client(tmux_server, target) as client:
                await pilot.press("escape")
                await wait_until(
                    lambda: "Stop engine" in structure(listing), timeout=UI_WAIT_SECONDS
                )
                await _stop_project_from_actions(app, pilot, listing, lock_path)
                assert await tmux.target_exists(target)
                assert client.process.returncode is None

                await wait_until(
                    lambda: "Start engine" in structure(listing), timeout=UI_WAIT_SECONDS
                )
                await _start_project_from_actions(app, pilot, listing, lock_path, start_attempts)
                await pilot.press("escape")
                await wait_until(
                    lambda: "Stop engine" in structure(listing), timeout=UI_WAIT_SECONDS
                )
                await _refuse_stale_stop(
                    app,
                    pilot,
                    listing,
                    prepared.root,
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
                    pilot,
                    config=config,
                    lock_path=lock_path,
                    start_attempts=start_attempts,
                    target=target,
                )
                assert await tmux.target_exists(target)
    finally:
        if engine_is_running(lock_path):
            await lifecycle.stop(prepared.root, (), kill_session=False)
        services.close()


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
