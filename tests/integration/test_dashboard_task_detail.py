from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import pytest
from textual.containers import HorizontalScroll
from textual.widgets import Button, DataTable, ListView, Select, Static

import shikiru.agents.registry as registry_module
from shikiru.agents.contracts import Usage
from shikiru.clock import SystemClock
from shikiru.config.loader import load_settings
from shikiru.config.settings import RepoConfig
from shikiru.dashboard.app import DashboardApp
from shikiru.dashboard.contract import WIDGET_ENTRY_POINT_GROUP, PaletteRequest
from shikiru.dashboard.detail import (
    TASK_DETAIL_ACTION_ROLE,
    TASK_DETAIL_ATTENTION_BUTTON,
    TASK_DETAIL_HAND_BACK_IMPLEMENTER_BUTTON,
    TASK_DETAIL_HAND_BACK_LEADER_BUTTON,
    TASK_DETAIL_IDENTITY,
    TASK_DETAIL_OWNERSHIP,
    TASK_DETAIL_RESUME_BUTTON,
    TASK_DETAIL_SITUATION,
    TaskDetailScreen,
)
from shikiru.dashboard.palette import PaletteScreen
from shikiru.dashboard.services import TaskNavigation
from shikiru.domain.commands import HandBack, Resume
from shikiru.domain.events import EventKind
from shikiru.domain.reports import (
    FACTS_FILENAME,
    REPORT_FILENAME,
    REPORT_KIND_FILENAME,
    ImplementationReport,
    Issue,
    ReportKind,
    TriageReport,
    Verdict,
)
from shikiru.domain.reports import TestRun as AgentTestRun
from shikiru.domain.steps import Step, Transition
from shikiru.domain.tasks import EscalationReason, Hold, Mode, RepoState, Role, Status, Task
from shikiru.operator.commands import OperatorCommands
from shikiru.operator.queries import OperatorQueries
from shikiru.paths import run_dir, task_root
from shikiru.store.commands import CommandQueue
from shikiru.store.escalations import EscalationStore
from shikiru.store.events import EventLog
from shikiru.store.reader import StateReader
from shikiru.store.runs import RunStore
from shikiru.store.tasks import TaskStore
from shikiru.tmux.client import Tmux, exact_target
from shikiru.tmux.session import HOME_WINDOW_NAME
from shikiru.workspace.reports import ENGINE_VERDICT_FILENAME
from tests.support.clock import FakeClock
from tests.support.config import write_minimal_project
from tests.support.dashboard import StubServices, ignore_palette_action, state_views
from tests.support.entry_points import FakeDistribution
from tests.support.factories import IMPLEMENTER_RUNGS, LEADER_RUNG
from tests.support.git_repo import commit_all, init_repo
from tests.support.render import plain_text
from tests.support.store import seed_task
from tests.support.waiting import wait_until
from tests.support.workspace import task_root_builder

pytestmark = pytest.mark.asyncio

START = datetime(2026, 1, 1, tzinfo=UTC)
TASK_DETAIL_WIDGETS = ["header", "queue", "dialogue", "usage", "resources"]
TASK_WINDOW_OPENED = "opened existing task window"
TASK_WINDOW_MISSING = "task window is unavailable"
TASK_ROLE_PANE_MISMATCH = "role pane is outside the recorded task window"
TASK_TERMINAL = "task is terminal; its workflow window is unavailable"
EXTENSION_WIDGET = "task-view-extension"
TMUX_WINDOW_ID_FORMAT = "#{window_id}"
TMUX_ACTIVE_WINDOW_FORMAT = "#{window_id} #{window_active}"
TMUX_ACTIVE_PANE_FORMAT = "#{pane_id} #{pane_active}"
LEADER_PANE_ID = "%1"
IMPLEMENTER_PANE_ID = "%2"


class _ExtensionWidget:
    name = EXTENSION_WIDGET

    def create(self, context: WidgetContext) -> Widget:
        del context
        return Static(id="task-view-extension-panel")


class _ExtensionEntryPoint:
    name = EXTENSION_WIDGET
    dist = FakeDistribution("task-view-extension-test")

    def load(self) -> type[_ExtensionWidget]:
        return _ExtensionWidget


if TYPE_CHECKING:
    from pathlib import Path

    from rich.text import Text
    from textual.pilot import Pilot
    from textual.widget import Widget

    from shikiru.dashboard.contract import (
        NavigateHome,
        NavigateRolePane,
        NavigateTaskWindow,
        OpenPaletteContext,
        WidgetContext,
    )
    from shikiru.domain.runs import Run
    from shikiru.store.commands import QueuedCommand
    from shikiru.store.database import Database
    from shikiru.workspace.roots import TaskRootBuilder
    from tests.support.store import Connection
    from tests.support.tmux import TmuxServer


@dataclass(frozen=True)
class _NavigationContext:
    project_root: Path
    reader: StateReader
    task: Task
    task_store: TaskStore
    builder: TaskRootBuilder
    repo: RepoState
    navigation: TaskNavigation
    tmux: Tmux
    session_target: str
    window_old: str
    pane_old: str
    pane_current: str
    window_current: str
    clock: FakeClock
    pending_before: list[QueuedCommand]


@dataclass(frozen=True)
class _VerdictCase:
    turns: tuple[tuple[str, ReportKind, str, str | None], ...]
    expected_report: str
    expected_report_source: str
    expected_verdict: str
    expected_verdict_source: str
    expected_verdict_turn: str


def _dashboard_app(
    reader: StateReader,
    project_root: Path,
    *,
    focus_task: str | None,
    widget_names: list[str],
    navigate_task_window: NavigateTaskWindow | None = None,
    navigate_role_pane: NavigateRolePane | None = None,
    navigate_home: NavigateHome | None = None,
    open_palette_context: OpenPaletteContext | None = None,
) -> DashboardApp:
    settings = load_settings(project_root)
    clock = SystemClock()
    return DashboardApp(
        focus_task=focus_task,
        views=state_views(
            reader,
            project_root,
            clock,
            StubServices(),
            focus_task=focus_task,
        ),
        on_change=reader.watch,
        clock=clock,
        motion_interval_seconds=None,
        resources_refresh_seconds=settings.orchestration.resources.sample_seconds,
        docker_refresh_seconds=settings.dashboard.docker_refresh_seconds,
        open_palette=ignore_palette_action,
        widget_names=widget_names,
        open_palette_context=open_palette_context,
        navigate_task_window=navigate_task_window,
        navigate_role_pane=navigate_role_pane,
        navigate_home=navigate_home,
    )


def _text(widget: Static) -> str:
    return cast("Text", widget.render()).plain


async def _unexpected_pane_selection(pane_id: str) -> None:
    raise AssertionError(pane_id)


def _seed_owned_task(
    connection: Connection,
    clock: FakeClock,
) -> tuple[Task, RunStore, TaskStore, CommandQueue, Run, Run]:
    task = seed_task(connection, clock, title="Owned roles and holds")
    runs = RunStore(connection, clock)
    leader = runs.start(task.id, Role.LEADER, LEADER_RUNG)
    implementer = runs.start(task.id, Role.IMPLEMENTER, IMPLEMENTER_RUNGS[0])
    runs.mark_taken_over(leader.id)
    runs.mark_taken_over(implementer.id)
    tasks = TaskStore(connection, clock)
    tasks.set_panes(
        task.id,
        {Role.LEADER: LEADER_PANE_ID, Role.IMPLEMENTER: IMPLEMENTER_PANE_ID},
    )
    tasks.set_holds(task.id, frozenset({Hold.PAUSED, Hold.INTERVENTION}))
    queue = CommandQueue(connection, clock)
    for submitted in queue.pending():
        queue.mark_done(submitted.id)
    return task, runs, tasks, queue, leader, implementer


def _operator_task_app(
    reader: StateReader,
    project_root: Path,
    task_id: str,
    queue: CommandQueue,
    clock: FakeClock,
) -> DashboardApp:
    settings = load_settings(project_root)
    services = StubServices()
    commands = OperatorCommands(queue, clock, engine_up=services.engine_up)
    queries = OperatorQueries(
        reader,
        project=settings.project.name,
        project_root=project_root,
        engine_up=services.engine_up,
        max_parallel=settings.orchestration.max_parallel,
        clock=clock,
    )
    active_apps: list[DashboardApp] = []

    async def open_palette_context(request: PaletteRequest) -> None:
        await active_apps[0].push_screen(
            PaletteScreen(
                commands,
                queries,
                _unexpected_pane_selection,
                on_change=reader.watch,
                initial_context=request,
            )
        )

    app = _dashboard_app(
        reader,
        project_root,
        focus_task=task_id,
        widget_names=TASK_DETAIL_WIDGETS,
        open_palette_context=open_palette_context,
    )
    active_apps.append(app)
    return app


async def _submit_detail_command(
    app: DashboardApp,
    pilot: Pilot[None],
    queue: CommandQueue,
    button_id: str,
) -> QueuedCommand:
    dashboard_screen = app.screen
    app.query_one(f"#{button_id}", Button).focus()
    await pilot.press("enter")
    await wait_until(lambda: isinstance(app.screen, PaletteScreen), timeout=2)
    await pilot.click("#palette-submit")
    await wait_until(lambda: len(queue.pending()) == 1, timeout=2)
    command = queue.pending()[0]
    await pilot.press("escape")
    await wait_until(lambda: app.screen is dashboard_screen, timeout=2)
    return command


def _write_report_turn(
    turns_root: Path,
    turn: str,
    kind: ReportKind,
    summary: str,
    engine_override: str | None,
) -> None:
    turn_dir = turns_root / turn
    turn_dir.mkdir(parents=True)
    report = (
        Verdict(decision="accept", summary=summary)
        if kind is ReportKind.VERDICT
        else ImplementationReport(
            status="completed",
            summary=summary,
            commits=[],
            tests=[],
            trackers_updated=False,
        )
    )
    (turn_dir / REPORT_KIND_FILENAME).write_text(kind.value)
    (turn_dir / REPORT_FILENAME).write_text(report.model_dump_json())
    if engine_override is not None:
        (turn_dir / ENGINE_VERDICT_FILENAME).write_text(
            Verdict(
                decision="changes_requested",
                summary=engine_override,
                issues=[Issue(severity="blocker", description="engine override")],
            ).model_dump_json()
        )


async def _navigation_context(
    connection: Connection,
    database: Database,
    tmp_path: Path,
    tmux_server: TmuxServer,
) -> _NavigationContext:
    project_root = tmp_path / "project"
    project_root.mkdir()
    write_minimal_project(project_root)
    runner = tmux_server.runner
    await init_repo(runner, project_root)
    (project_root / "README.md").write_text("task detail test\n")
    await commit_all(runner, project_root, "initial project")
    builder = task_root_builder(runner, project_root, RepoConfig())
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="Terminal task", mode=Mode.IMPLEMENT)
    repo = await builder.build(task.id, task.title)
    task_store = TaskStore(connection, clock)
    task_store.set_repo(task.id, repo)

    tmux = Tmux(runner, socket=tmux_server.socket_name, env=tmux_server.env)
    session_target = exact_target(tmux_server.session)
    await tmux.rename_window(session_target, HOME_WINDOW_NAME)
    pane_old = await tmux.new_window(session_target, name="old-task-target")
    window_old = await tmux.window_id(pane_old)
    pane_current = await tmux.new_window(session_target, name="current-task-target")
    window_current = await tmux.window_id(pane_current)
    task_store.set_window(task.id, window_old)
    task_store.set_panes(task.id, {Role.LEADER: pane_old, Role.IMPLEMENTER: pane_old})
    reader = StateReader(database, SystemClock())
    settings = load_settings(project_root)
    queries = OperatorQueries(
        reader,
        project=settings.project.name,
        project_root=project_root,
        engine_up=lambda: False,
        max_parallel=settings.orchestration.max_parallel,
        clock=clock,
    )
    navigation = TaskNavigation(tmux, queries, session_target)
    pending_before = CommandQueue(connection, clock).pending()
    task_store.set_window(task.id, window_current)
    task_store.set_panes(
        task.id,
        {Role.LEADER: pane_current, Role.IMPLEMENTER: pane_current},
    )
    return _NavigationContext(
        project_root,
        reader,
        task,
        task_store,
        builder,
        repo,
        navigation,
        tmux,
        session_target,
        window_old,
        pane_old,
        pane_current,
        window_current,
        clock,
        pending_before,
    )


async def test_detail_projects_latest_validated_reports_and_keeps_task_state_when_evidence_is_bad(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = seed_task(
        connection,
        clock,
        title="Visible task after evidence failure",
        request="Implement the recorded request",
        mode=Mode.IMPLEMENT,
    )
    TaskStore(connection, clock).set_repo(
        task.id,
        RepoState(base="main", base_ref="main", base_sha="base-sha", branch="shikiru/T-1-detail"),
    )
    run = RunStore(connection, clock).start(task.id, Role.IMPLEMENTER, IMPLEMENTER_RUNGS[0])
    RunStore(connection, clock).set_session(run.id, "session-current", "resume-current")
    RunStore(connection, clock).record_usage(
        run.id,
        Usage(
            input_tokens=12,
            output_tokens=7,
            cache_read_tokens=3,
            cache_write_tokens=1,
            cost_usd=0.25,
            cost_basis="billed",
            context_used_pct=20,
        ),
    )
    RunStore(connection, clock).mark_taken_over(run.id)
    EventLog(connection, clock).append(
        EventKind.TURN_COMPLETED,
        task_id=task.id,
        message="implement turn completed",
        data={"role": Role.IMPLEMENTER.value, "summary": "Recorded implementation event"},
    )

    turns = project_root / ".shikiru" / "runs" / task.id / "turns"
    triage_dir = turns / "01-triage-i0"
    implementation_dir = turns / "02-implement-i1"
    invalid_dir = turns / "03-verify-i1"
    triage_dir.mkdir(parents=True)
    implementation_dir.mkdir()
    invalid_dir.mkdir()
    triage = TriageReport(
        title=task.title,
        mode=Mode.IMPLEMENT,
        complexity="normal",
        acceptance_criteria=["first acceptance criterion", "second acceptance criterion"],
        references=[],
    )
    (triage_dir / REPORT_KIND_FILENAME).write_text("triage")
    (triage_dir / REPORT_FILENAME).write_text(triage.model_dump_json())
    implementation = ImplementationReport(
        status="completed",
        summary="Latest implementation summary",
        commits=[],
        tests=[AgentTestRun(name="unit suite", result="passed", details="recorded by agent")],
        trackers_updated=False,
    )
    (implementation_dir / REPORT_KIND_FILENAME).write_text("implementation")
    (implementation_dir / REPORT_FILENAME).write_text(implementation.model_dump_json())
    (implementation_dir / ENGINE_VERDICT_FILENAME).write_text(
        Verdict(decision="accept", summary="Latest engine verdict").model_dump_json()
    )
    (implementation_dir / FACTS_FILENAME).write_text(
        "branch shikiru/T-1-detail\nworking tree clean\n"
    )
    (invalid_dir / REPORT_KIND_FILENAME).write_text("invalid report kind")
    signal_path = run_dir(project_root, task.id) / "implementer" / "view.jsonl"
    signal_path.parent.mkdir(parents=True)
    signal_path.write_text(json.dumps({"signal": "ToolFinished", "name": "pytest"}) + "\n")

    views = state_views(reader, project_root, clock, StubServices())
    detail = await views.task_detail(task.id)

    assert detail is not None
    assert detail.task_id == task.id
    assert detail.title == "Visible task after evidence failure"
    assert detail.request == "Implement the recorded request"
    assert detail.acceptance_criteria == (
        "first acceptance criterion",
        "second acceptance criterion",
    )
    assert detail.latest_report is not None
    assert detail.latest_report.summary == "Latest implementation summary"
    assert detail.latest_report.role == Role.IMPLEMENTER.value
    assert detail.latest_report.tests[0].provenance == "agent-reported"
    assert detail.latest_verdict is not None
    assert detail.latest_verdict.role == Role.LEADER.value
    assert detail.latest_verdict.provenance == "engine"
    assert detail.latest_verdict.summary == "Latest engine verdict"
    assert detail.recorded_facts == "branch shikiru/T-1-detail\nworking tree clean\n"
    assert detail.facts_turn == "02-implement-i1"
    assert detail.signals[0].role == Role.IMPLEMENTER.value
    assert detail.sessions[0].session_id == "session-current"
    assert detail.sessions[0].input_tokens == 12
    assert detail.ownership == (Role.IMPLEMENTER.value,)
    assert detail.evidence_errors


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            _VerdictCase(
                turns=(("01-verify-i1", ReportKind.VERDICT, "Leader verdict", None),),
                expected_report="Leader verdict",
                expected_report_source="agent",
                expected_verdict="Leader verdict",
                expected_verdict_source="agent",
                expected_verdict_turn="01-verify-i1",
            ),
            id="ordinary-leader-verdict",
        ),
        pytest.param(
            _VerdictCase(
                turns=(
                    ("01-verify-i1", ReportKind.VERDICT, "Earlier leader verdict", None),
                    (
                        "02-implement-i2",
                        ReportKind.IMPLEMENTATION,
                        "Rework implementation",
                        None,
                    ),
                ),
                expected_report="Rework implementation",
                expected_report_source="agent",
                expected_verdict="Earlier leader verdict",
                expected_verdict_source="agent",
                expected_verdict_turn="01-verify-i1",
            ),
            id="rework-verdict-retained-after-implementation",
        ),
        pytest.param(
            _VerdictCase(
                turns=(("03-verify-i2", ReportKind.VERDICT, "Leader verdict", "Engine override"),),
                expected_report="Leader verdict",
                expected_report_source="agent",
                expected_verdict="Engine override",
                expected_verdict_source="engine",
                expected_verdict_turn="03-verify-i2",
            ),
            id="same-turn-engine-override-wins",
        ),
        pytest.param(
            _VerdictCase(
                turns=(
                    ("99-verify-i1", ReportKind.VERDICT, "Older leader", "Older engine override"),
                    ("100-verify-i2", ReportKind.VERDICT, "Newer leader", None),
                ),
                expected_report="Newer leader",
                expected_report_source="agent",
                expected_verdict="Newer leader",
                expected_verdict_source="agent",
                expected_verdict_turn="100-verify-i2",
            ),
            id="newer-leader-verdict-wins-over-older-override",
        ),
    ],
)
async def test_detail_selects_the_latest_report_and_turn_scoped_verdict(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
    case: _VerdictCase,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="Verdict ordering", mode=Mode.IMPLEMENT)
    turns_root = project_root / ".shikiru" / "runs" / task.id / "turns"
    for turn, kind, summary, engine_override in case.turns:
        _write_report_turn(turns_root, turn, kind, summary, engine_override)

    detail = await state_views(reader, project_root, clock, StubServices()).task_detail(task.id)

    assert detail is not None
    assert detail.latest_report is not None
    assert detail.latest_report.summary == case.expected_report
    assert detail.latest_report.provenance == case.expected_report_source
    assert detail.latest_verdict is not None
    assert detail.latest_verdict.summary == case.expected_verdict
    assert detail.latest_verdict.provenance == case.expected_verdict_source
    assert detail.latest_verdict.turn == case.expected_verdict_turn


@pytest.mark.parametrize("turns_path", ["missing", "not-a-directory"])
async def test_detail_reads_role_signals_when_turn_report_path_is_unavailable(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
    turns_path: str,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="Signals without reports", mode=Mode.IMPLEMENT)
    RunStore(connection, clock).start(task.id, Role.IMPLEMENTER, IMPLEMENTER_RUNGS[0])
    signal_path = run_dir(project_root, task.id) / Role.IMPLEMENTER.value / "view.jsonl"
    signal_path.parent.mkdir(parents=True)
    signal_path.write_text(json.dumps({"signal": "ToolFinished", "name": "pytest"}) + "\n")
    turns_root = run_dir(project_root, task.id) / "turns"
    if turns_path == "not-a-directory":
        turns_root.write_text("invalid turns path")

    detail = await state_views(reader, project_root, clock, StubServices()).task_detail(task.id)

    assert detail is not None
    assert detail.title == "Signals without reports"
    assert detail.signals
    assert detail.signals[0].role == Role.IMPLEMENTER.value
    assert "pytest" in detail.signals[0].lines[0]
    if turns_path == "not-a-directory":
        assert any("not a directory" in error for error in detail.evidence_errors)


async def test_enter_opens_shared_detail_and_escape_restores_browser_selection_and_scroll(
    connection: Connection,
    database: Database,
    tmp_path: Path,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    for index in range(30):
        seed_task(connection, clock, title=f"Browser task {index}")
    reader = StateReader(database, SystemClock())
    try:
        app = _dashboard_app(
            reader,
            project_root,
            focus_task=None,
            widget_names=TASK_DETAIL_WIDGETS,
        )
        async with app.run_test(size=(80, 40)) as pilot:
            list_view = app.query_one("#task-browser-list", ListView)
            list_view.focus()
            for _ in range(8):
                await pilot.press("down")
            await wait_until(lambda: list_view.index == 8, timeout=2)
            selected_index = list_view.index
            assert selected_index is not None
            selected_row = list_view.children[selected_index]
            selected_id = selected_row.id
            scroll_y = list_view.scroll_y
            home_screen = app.screen

            await pilot.press("enter")
            await wait_until(lambda: isinstance(app.screen, TaskDetailScreen), timeout=2)
            detail_screen = app.screen
            await wait_until(
                lambda: (
                    "Browser task 8"
                    in _text(detail_screen.query_one("#task-detail-identity", Static))
                ),
                timeout=2,
            )
            await pilot.press("escape")
            await wait_until(lambda: app.screen is home_screen, timeout=2)

            restored_list = app.query_one("#task-browser-list", ListView)
            assert restored_list.index == 8
            restored_index = restored_list.index
            assert restored_index is not None
            assert restored_list.children[restored_index].id == selected_id
            assert restored_list.scroll_y == scroll_y
            assert app.focused is restored_list
    finally:
        reader.close()


async def test_explicit_detail_role_survives_implementation_step_change(
    connection: Connection,
    database: Database,
    tmp_path: Path,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = seed_task(
        connection,
        clock,
        title="Detail action target",
        mode=Mode.IMPLEMENT,
    )
    TaskStore(connection, clock).apply_transition(
        task.id,
        Transition.stay(task, step=Step.IMPLEMENT, status=Status.RUNNING),
    )
    reader = StateReader(database, SystemClock())
    opened: list[PaletteRequest] = []

    async def open_palette_context(request: PaletteRequest) -> None:
        opened.append(request)

    try:
        app = _dashboard_app(
            reader,
            project_root,
            focus_task=task.id,
            widget_names=TASK_DETAIL_WIDGETS,
            open_palette_context=open_palette_context,
        )
        async with app.run_test(size=(80, 40)) as pilot:
            await pilot.pause()
            action_role = cast("Select[str]", app.query_one(f"#{TASK_DETAIL_ACTION_ROLE}", Select))
            action_role.value = Role.IMPLEMENTER.value
            current_task = reader.get_task(task.id)
            assert current_task is not None
            TaskStore(connection, clock).apply_transition(
                task.id,
                Transition.stay(current_task, step=Step.VERIFY, status=Status.RUNNING),
            )
            await wait_until(
                lambda: "verify" in _text(app.query_one(f"#{TASK_DETAIL_SITUATION}", Static)),
                timeout=2,
            )
            assert action_role.value == Role.IMPLEMENTER.value
            await pilot.press("ctrl+p")
            await wait_until(lambda: bool(opened), timeout=2)

        assert opened == [PaletteRequest(task_id=task.id, role=Role.IMPLEMENTER.value)]
    finally:
        reader.close()


async def test_pushed_task_detail_ctrl_p_carries_selected_role(
    connection: Connection,
    database: Database,
    tmp_path: Path,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="Pushed detail action target")
    reader = StateReader(database, SystemClock())
    opened: list[PaletteRequest] = []

    async def open_palette_context(request: PaletteRequest) -> None:
        opened.append(request)

    try:
        app = _dashboard_app(
            reader,
            project_root,
            focus_task=None,
            widget_names=TASK_DETAIL_WIDGETS,
            open_palette_context=open_palette_context,
        )
        async with app.run_test(size=(80, 40)) as pilot:
            browser = app.query_one("#task-browser-list", ListView)
            browser.focus()
            await pilot.press("enter")
            await wait_until(lambda: isinstance(app.screen, TaskDetailScreen), timeout=2)
            detail = app.screen
            await wait_until(
                lambda: (
                    "Pushed detail action target"
                    in _text(detail.query_one(f"#{TASK_DETAIL_IDENTITY}", Static))
                ),
                timeout=2,
            )
            detail.query_one(f"#{TASK_DETAIL_ACTION_ROLE}", Select).value = Role.IMPLEMENTER.value
            await pilot.press("ctrl+p")
            await wait_until(lambda: bool(opened), timeout=2)

        assert opened == [PaletteRequest(task_id=task.id, role=Role.IMPLEMENTER.value)]
    finally:
        reader.close()


async def test_implicit_detail_role_tracks_step_change_at_palette_activation(
    connection: Connection,
    database: Database,
    tmp_path: Path,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = seed_task(
        connection,
        clock,
        title="Implementation role default",
        mode=Mode.IMPLEMENT,
    )
    TaskStore(connection, clock).apply_transition(
        task.id,
        Transition.stay(task, step=Step.IMPLEMENT, status=Status.RUNNING),
    )
    reader = StateReader(database, SystemClock())
    opened: list[PaletteRequest] = []

    async def open_palette_context(request: PaletteRequest) -> None:
        opened.append(request)

    try:
        detail_view = await state_views(reader, project_root, clock, StubServices()).task_detail(
            task.id
        )
        assert detail_view is not None
        assert detail_view.acting_role == Role.IMPLEMENTER.value
        app = _dashboard_app(
            reader,
            project_root,
            focus_task=task.id,
            widget_names=TASK_DETAIL_WIDGETS,
            open_palette_context=open_palette_context,
        )
        async with app.run_test(size=(80, 40)) as pilot:
            await wait_until(
                lambda: (
                    "Implementation role default"
                    in _text(app.query_one(f"#{TASK_DETAIL_IDENTITY}", Static))
                ),
                timeout=2,
            )
            action_role = cast("Select[str]", app.query_one(f"#{TASK_DETAIL_ACTION_ROLE}", Select))
            assert action_role.value is Select.BLANK
            assert Role.IMPLEMENTER.value.title() in action_role.prompt
            current_task = reader.get_task(task.id)
            assert current_task is not None
            TaskStore(connection, clock).apply_transition(
                task.id,
                Transition.stay(current_task, step=Step.VERIFY, status=Status.RUNNING),
            )
            await wait_until(
                lambda: (
                    "verify" in _text(app.query_one(f"#{TASK_DETAIL_SITUATION}", Static))
                    and Role.LEADER.value.title() in action_role.prompt
                ),
                timeout=2,
            )
            await pilot.press("ctrl+p")
            await wait_until(lambda: bool(opened), timeout=2)

        assert opened == [PaletteRequest(task_id=task.id)]
    finally:
        reader.close()


async def test_hand_back_and_resume_remain_explicit_while_other_holds_survive(
    connection: Connection,
    database: Database,
    tmp_path: Path,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task, runs, tasks, queue, leader, _implementer = _seed_owned_task(connection, clock)
    reader = StateReader(database, SystemClock())

    try:
        app = _operator_task_app(reader, project_root, task.id, queue, clock)
        async with app.run_test(size=(80, 40)) as pilot:
            ownership = app.query_one(f"#{TASK_DETAIL_OWNERSHIP}", Static)
            await wait_until(
                lambda: (
                    "Leader" in _text(ownership)
                    and "Implementer" in _text(ownership)
                    and Hold.PAUSED.value in _text(ownership)
                    and Hold.INTERVENTION.value in _text(ownership)
                ),
                timeout=2,
            )

            first_command = await _submit_detail_command(
                app,
                pilot,
                queue,
                TASK_DETAIL_HAND_BACK_LEADER_BUTTON,
            )
            assert isinstance(first_command.command, HandBack)
            assert first_command.command.role is Role.LEADER
            runs.mark_live(leader.id)
            tasks.set_holds(task.id, frozenset({Hold.PAUSED, Hold.INTERVENTION}))
            queue.mark_done(first_command.id, {"role": Role.LEADER.value})
            await wait_until(
                lambda: (
                    "Operator owns · Implementer" in _text(ownership)
                    and "Leader" not in _text(ownership)
                    and Hold.PAUSED.value in _text(ownership)
                    and Hold.INTERVENTION.value in _text(ownership)
                ),
                timeout=2,
            )
            assert app.query_one(f"#{TASK_DETAIL_HAND_BACK_LEADER_BUTTON}", Button).disabled
            assert not app.query_one(
                f"#{TASK_DETAIL_HAND_BACK_IMPLEMENTER_BUTTON}", Button
            ).disabled
            assert not app.query_one(f"#{TASK_DETAIL_RESUME_BUTTON}", Button).disabled
            app.query_one(f"#{TASK_DETAIL_RESUME_BUTTON}", Button).focus()
            await pilot.press("enter")
            await wait_until(lambda: isinstance(app.screen, PaletteScreen), timeout=2)
            palette = cast("PaletteScreen", app.screen)
            assert palette.query_one("#palette-submit", Button).disabled
            assert "hand back all taken-over roles" in _text(
                palette.query_one("#palette-action-state", Static)
            )
            await pilot.press("escape")
            task_after_partial_hand_back = reader.get_task(task.id)
            assert task_after_partial_hand_back is not None
            assert task_after_partial_hand_back.holds == frozenset({Hold.PAUSED, Hold.INTERVENTION})
    finally:
        reader.close()


async def test_resume_dispatches_after_hand_back_without_clearing_the_pause(
    connection: Connection,
    database: Database,
    tmp_path: Path,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="Separate resume command")
    runs = RunStore(connection, clock)
    leader = runs.start(task.id, Role.LEADER, LEADER_RUNG)
    runs.mark_taken_over(leader.id)
    tasks = TaskStore(connection, clock)
    tasks.set_panes(task.id, {Role.LEADER: LEADER_PANE_ID})
    tasks.set_holds(task.id, frozenset({Hold.PAUSED}))
    queue = CommandQueue(connection, clock)
    for submitted in queue.pending():
        queue.mark_done(submitted.id)
    reader = StateReader(database, SystemClock())

    try:
        app = _operator_task_app(reader, project_root, task.id, queue, clock)
        async with app.run_test(size=(80, 40)) as pilot:
            ownership = app.query_one(f"#{TASK_DETAIL_OWNERSHIP}", Static)
            await wait_until(
                lambda: (
                    "Operator owns · Leader" in _text(ownership)
                    and Hold.PAUSED.value in _text(ownership)
                ),
                timeout=2,
            )
            assert not app.query_one(f"#{TASK_DETAIL_RESUME_BUTTON}", Button).disabled
            app.query_one(f"#{TASK_DETAIL_RESUME_BUTTON}", Button).focus()
            await pilot.press("enter")
            await wait_until(lambda: isinstance(app.screen, PaletteScreen), timeout=2)
            palette = cast("PaletteScreen", app.screen)
            assert palette.query_one("#palette-submit", Button).disabled
            assert "hand back all taken-over roles" in _text(
                palette.query_one("#palette-action-state", Static)
            )
            await pilot.press("escape")

            hand_back = await _submit_detail_command(
                app,
                pilot,
                queue,
                TASK_DETAIL_HAND_BACK_LEADER_BUTTON,
            )
            assert isinstance(hand_back.command, HandBack)
            assert hand_back.command.role is Role.LEADER
            runs.mark_live(leader.id)
            tasks.set_holds(task.id, frozenset({Hold.PAUSED}))
            queue.mark_done(hand_back.id, {"role": Role.LEADER.value})
            await wait_until(
                lambda: (
                    "Remaining holds · paused" in _text(ownership)
                    and not app.query_one(f"#{TASK_DETAIL_RESUME_BUTTON}", Button).disabled
                ),
                timeout=2,
            )
            paused_task = tasks.get(task.id)
            assert paused_task is not None
            assert paused_task.holds == {Hold.PAUSED}

            resume_command = await _submit_detail_command(
                app,
                pilot,
                queue,
                TASK_DETAIL_RESUME_BUTTON,
            )
            assert isinstance(resume_command.command, Resume)
            assert resume_command.command.task_id == task.id
            task_before_resume = tasks.get(task.id)
            assert task_before_resume is not None
            assert task_before_resume.holds == {Hold.PAUSED}

            tasks.set_holds(task.id, frozenset())
            queue.mark_done(resume_command.id)
            await wait_until(lambda: not _text(ownership), timeout=2)
    finally:
        reader.close()


async def test_resume_availability_includes_an_intervention_only_hold(
    connection: Connection,
    database: Database,
    tmp_path: Path,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="Intervention only")
    tasks = TaskStore(connection, clock)
    tasks.set_holds(task.id, frozenset({Hold.INTERVENTION}))
    queue = CommandQueue(connection, clock)
    for submitted in queue.pending():
        queue.mark_done(submitted.id)
    reader = StateReader(database, SystemClock())

    try:
        app = _operator_task_app(reader, project_root, task.id, queue, clock)
        async with app.run_test(size=(80, 40)) as pilot:
            ownership = app.query_one(f"#{TASK_DETAIL_OWNERSHIP}", Static)
            await wait_until(lambda: Hold.INTERVENTION.value in _text(ownership), timeout=2)
            resume_button = app.query_one(f"#{TASK_DETAIL_RESUME_BUTTON}", Button)
            assert not resume_button.disabled
            resume_button.focus()
            await pilot.press("enter")
            await wait_until(lambda: isinstance(app.screen, PaletteScreen), timeout=2)
            palette = cast("PaletteScreen", app.screen)
            assert not palette.query_one("#palette-submit", Button).disabled
            await pilot.click("#palette-submit")
            await wait_until(lambda: len(queue.pending()) == 1, timeout=2)
            queued = queue.pending()[0]
            assert isinstance(queued.command, Resume)
            assert queued.command.task_id == task.id
    finally:
        reader.close()


async def test_fixed_task_view_scopes_activity_and_usage_and_keeps_extensions_reachable(
    connection: Connection,
    database: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    focused = seed_task(connection, clock, title="Focused task", mode=Mode.IMPLEMENT)
    unrelated = seed_task(connection, clock, title="Unrelated task", mode=Mode.IMPLEMENT)
    focused_run = RunStore(connection, clock).start(
        focused.id, Role.IMPLEMENTER, IMPLEMENTER_RUNGS[0]
    )
    RunStore(connection, clock).record_usage(
        focused_run.id,
        Usage(4, 2, 0, 0, 0.1, "billed", 10),
    )
    other_run = RunStore(connection, clock).start(unrelated.id, Role.IMPLEMENTER, LEADER_RUNG)
    RunStore(connection, clock).record_usage(
        other_run.id,
        Usage(900, 700, 0, 0, 4.0, "billed", 80),
    )
    EventLog(connection, clock).append(
        EventKind.TURN_COMPLETED,
        task_id=focused.id,
        message="focused turn completed",
        data={"role": Role.IMPLEMENTER.value, "summary": "Focused activity only"},
    )
    EventLog(connection, clock).append(
        EventKind.TURN_COMPLETED,
        task_id=unrelated.id,
        message="other turn completed",
        data={"role": Role.IMPLEMENTER.value, "summary": "Unrelated activity"},
    )
    EscalationStore(connection, clock).open(
        focused.id,
        step=None,
        reason=EscalationReason.DECISION_REQUIRED,
        headline="Attention",
        packet_path="unused.packet",
        options=(),
    )
    EventLog(connection, clock).append(
        EventKind.ENGINE_FAULT,
        task_id=focused.id,
        message="recorded fault",
        data={"scope": "scheduler", "error": "task read failed", "recoverable": True, "repeat": 1},
    )
    previous_entry_points = registry_module.entry_points

    def entry_points_with_extension(*, group: str) -> list[object]:
        registered = previous_entry_points(group=group)
        if group == WIDGET_ENTRY_POINT_GROUP:
            return [*registered, _ExtensionEntryPoint()]
        return list(registered)

    monkeypatch.setattr(registry_module, "entry_points", entry_points_with_extension)
    reader = StateReader(database, SystemClock())
    try:
        app = _dashboard_app(
            reader,
            project_root,
            focus_task=focused.id,
            widget_names=["queue", "dialogue", "usage", EXTENSION_WIDGET],
        )
        async with app.run_test(size=(36, 24)):
            identity = app.query_one("#task-detail-identity", Static)
            await wait_until(lambda: focused.title in _text(identity), timeout=2)
            assert identity.region.width > 0
            assert focused.id in _text(identity)
            situation = _text(app.query_one("#task-detail-situation", Static))
            assert "Global attention · 1 decisions · 1 engine faults" in situation
            dialogue = cast("DataTable[object]", app.query_one("#dialogue-table", DataTable))
            assert dialogue.row_count == 2
            dialogue_text = " ".join(
                plain_text(cell)
                for row in range(dialogue.row_count)
                for cell in dialogue.get_row_at(row)
            )
            assert "Focused activity only" in dialogue_text
            assert "recorded fault" in dialogue_text
            assert "Unrelated activity" not in dialogue_text
            usage_text = plain_text(app.query_one("#usage", Static).content)
            assert f"Task recorded usage · {focused.id}" in usage_text
            assert focused_run.agent in usage_text
            assert "4" in usage_text
            assert other_run.agent not in usage_text
            assert "900" not in usage_text
            assert app.query_one("#task-view-extension-panel", Static).region.width > 0
            assert len(app.query("#task-browser-list")) == 0
            assert app.query_one("#task-detail-window", Button).region.width > 0
    finally:
        reader.close()


async def test_task_window_global_attention_uses_injected_home_navigation(
    database: Database,
    connection: Connection,
    tmp_path: Path,
) -> None:
    project_root = write_minimal_project(tmp_path)
    task = seed_task(connection, SystemClock(), title="Return to project attention")
    reader = StateReader(database, SystemClock())
    home_results: list[str] = []

    async def navigate_home() -> str:
        result = "opened existing home window"
        home_results.append(result)
        return result

    try:
        app = _dashboard_app(
            reader,
            project_root,
            focus_task=task.id,
            widget_names=TASK_DETAIL_WIDGETS,
            navigate_home=navigate_home,
        )
        async with app.run_test(size=(100, 30)) as pilot:
            identity = app.query_one(f"#{TASK_DETAIL_IDENTITY}", Static)
            await wait_until(lambda: task.title in _text(identity), timeout=2)
            toolbar = app.query_one("#task-detail-toolbar", HorizontalScroll)
            toolbar.scroll_to_widget(
                app.query_one(f"#{TASK_DETAIL_ATTENTION_BUTTON}", Button),
                animate=False,
            )
            await pilot.pause()
            await pilot.click(f"#{TASK_DETAIL_ATTENTION_BUTTON}")
            feedback = app.query_one("#task-detail-feedback", Static)
            await wait_until(
                lambda: "opened existing home window" in _text(feedback),
                timeout=2,
            )
            assert home_results == ["opened existing home window"]
    finally:
        reader.close()


async def test_navigation_uses_activation_time_targets_and_terminal_cleanup_creates_nothing(
    connection: Connection,
    database: Database,
    tmp_path: Path,
    tmux_server: TmuxServer,
) -> None:
    context = await _navigation_context(connection, database, tmp_path, tmux_server)
    task = context.task
    reader = context.reader

    try:
        assert await context.navigation.task_window(task.id) == (f"{task.id}: {TASK_WINDOW_OPENED}")
        active_window_rows = await tmux_server.run(
            "list-windows",
            "-t",
            context.session_target,
            "-F",
            TMUX_ACTIVE_WINDOW_FORMAT,
        )
        assert f"{context.window_current} 1" in active_window_rows.stdout.splitlines()
        assert await context.navigation.role_pane(task.id, Role.LEADER) == (
            f"{task.id} leader: opened existing role pane"
        )
        active_pane_rows = await tmux_server.run(
            "list-panes",
            "-t",
            context.window_current,
            "-F",
            TMUX_ACTIVE_PANE_FORMAT,
        )
        assert f"{context.pane_current} 1" in active_pane_rows.stdout.splitlines()
        assert CommandQueue(connection, context.clock).pending() == context.pending_before
        await context.tmux.kill_window(context.window_current)
        assert await context.navigation.task_window(task.id) == (
            f"{task.id}: {TASK_WINDOW_MISSING}"
        )
        assert await context.navigation.role_pane(task.id, Role.LEADER) == (
            f"{task.id}: {TASK_WINDOW_MISSING}"
        )

        current_task = reader.get_task(task.id)
        assert current_task is not None
        context.task_store.apply_transition(
            task.id,
            Transition.stay(current_task, step=current_task.step, status=Status.DONE),
        )
        assert await context.builder.cleanup(task.id, context.repo) is None
        assert not task_root(context.project_root, task.id).exists()
        await context.tmux.kill_window(context.window_old)
        windows_before = await tmux_server.run(
            "list-windows",
            "-t",
            context.session_target,
            "-F",
            TMUX_WINDOW_ID_FORMAT,
        )

        assert await context.navigation.task_window(task.id) == (f"{task.id}: {TASK_TERMINAL}")
        assert await context.navigation.role_pane(task.id, Role.LEADER) == (
            f"{task.id}: {TASK_TERMINAL}"
        )
        windows_after = await tmux_server.run(
            "list-windows",
            "-t",
            context.session_target,
            "-F",
            TMUX_WINDOW_ID_FORMAT,
        )
        assert windows_after.stdout == windows_before.stdout

        app = _dashboard_app(
            reader,
            context.project_root,
            focus_task=task.id,
            widget_names=TASK_DETAIL_WIDGETS,
            navigate_task_window=context.navigation.task_window,
        )
        async with app.run_test(size=(36, 24)) as pilot:
            identity = app.query_one("#task-detail-identity", Static)
            await wait_until(lambda: task.title in _text(identity), timeout=2)
            assert "done" in _text(identity)
            workspace = _text(app.query_one("#task-detail-workspace", Static))
            assert str(task_root(context.project_root, task.id)) in workspace
            button = app.query_one("#task-detail-window", Button)
            button.focus()
            await pilot.press("enter")
            feedback = app.query_one("#task-detail-feedback", Static)
            await wait_until(lambda: TASK_TERMINAL in _text(feedback), timeout=2)
            final_windows = await tmux_server.run(
                "list-windows",
                "-t",
                context.session_target,
                "-F",
                TMUX_WINDOW_ID_FORMAT,
            )
            assert final_windows.stdout == windows_before.stdout
    finally:
        reader.close()


async def test_role_navigation_rejects_a_recorded_pane_moved_to_another_window(
    connection: Connection,
    database: Database,
    tmp_path: Path,
    tmux_server: TmuxServer,
) -> None:
    context = await _navigation_context(connection, database, tmp_path, tmux_server)
    try:
        spare_pane = await context.tmux.split_window(
            context.window_current,
            axis="v",
            size_pct=50,
        )
        await tmux_server.run(
            "join-pane",
            "-s",
            context.pane_current,
            "-t",
            context.window_old,
        )
        assert await context.tmux.window_id(context.pane_current) == context.window_old
        await context.tmux.select_window(context.window_current)
        await context.tmux.select_pane(spare_pane)
        windows_before = await tmux_server.run(
            "list-windows",
            "-t",
            context.session_target,
            "-F",
            TMUX_WINDOW_ID_FORMAT,
        )

        result = await context.navigation.role_pane(context.task.id, Role.LEADER)

        assert result == f"{context.task.id} leader: {TASK_ROLE_PANE_MISMATCH}"
        windows_after = await tmux_server.run(
            "list-windows",
            "-t",
            context.session_target,
            "-F",
            TMUX_WINDOW_ID_FORMAT,
        )
        assert windows_after.stdout == windows_before.stdout
        active_window_rows = await tmux_server.run(
            "list-windows",
            "-t",
            context.session_target,
            "-F",
            TMUX_ACTIVE_WINDOW_FORMAT,
        )
        assert f"{context.window_current} 1" in active_window_rows.stdout.splitlines()
        active_pane_rows = await tmux_server.run(
            "list-panes",
            "-t",
            context.window_current,
            "-F",
            TMUX_ACTIVE_PANE_FORMAT,
        )
        assert f"{spare_pane} 1" in active_pane_rows.stdout.splitlines()
        assert CommandQueue(connection, context.clock).pending() == context.pending_before
    finally:
        context.reader.close()
