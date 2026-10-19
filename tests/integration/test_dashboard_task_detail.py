from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

import pytest
from rich.text import Text
from textual.containers import VerticalScroll
from textual.widgets import Button, ListView, Select, Static, TextArea

import shikiru.agents.registry as registry_module
from shikiru.agents.contracts import (
    BILLING_MODE_API,
    BILLING_MODE_SUBSCRIPTION,
    BILLING_SOURCE_OPERATOR_CONFIG,
    COST_BASIS_API_EQUIVALENT,
    COST_BASIS_BILLED,
    COST_BASIS_UNKNOWN,
    BillingDeclaration,
    Rung,
    Usage,
)
from shikiru.clock import SystemClock
from shikiru.config.loader import load_settings
from shikiru.config.settings import RepoConfig
from shikiru.dashboard.actions_list import ActionsListScreen
from shikiru.dashboard.app import DashboardApp
from shikiru.dashboard.contract import (
    WIDGET_ENTRY_POINT_GROUP,
    PaletteRequest,
    TaskHistoryGap,
    TaskTimelineCategory,
)
from shikiru.dashboard.detail import (
    AGENT_TIMELINE_IDS,
    TASK_DETAIL_ACTION_PREFIX,
    TASK_DETAIL_AGENT_LIST,
    TASK_DETAIL_BLOCKER,
    TASK_DETAIL_BODY,
    TASK_DETAIL_BRIEFING,
    TASK_DETAIL_CONTEXT_INFO,
    TASK_DETAIL_IDENTITY,
    TASK_DETAIL_OWNERSHIP,
    TASK_DETAIL_SESSIONS,
    TASK_VIEW_AGENT,
    TASK_VIEW_CONTEXT,
    TASK_VIEW_DECISIONS,
    TASK_VIEW_EVIDENCE,
    TASK_VIEW_RELATIONSHIPS,
    TASK_VIEW_REQUEST,
    TASK_VIEW_RESULTS,
    TASK_VIEW_USAGE,
    TASK_VIEW_WORKSPACE,
    TaskDetailPanel,
)
from shikiru.dashboard.detail_fold import DetailFolds, FoldHeader
from shikiru.dashboard.details_list_view import DetailsSection
from shikiru.dashboard.palette import PaletteScreen
from shikiru.dashboard.row_list import RowList
from shikiru.dashboard.services import TaskNavigation
from shikiru.dashboard.timeline_filter import TimelineFilterBar
from shikiru.domain.briefing import (
    BriefingAction,
    ProducedNumbers,
    PullRequestLine,
    ReportedTests,
    VerdictCounts,
)
from shikiru.domain.commands import HandBack, Resume
from shikiru.domain.escalation import options_for
from shikiru.domain.events import EventKind
from shikiru.domain.recovery import Completed
from shikiru.domain.reports import (
    FACTS_FILENAME,
    REPORT_FILENAME,
    REPORT_KIND_FILENAME,
    ImplementationReport,
    Issue,
    PlanReport,
    ReportKind,
    TriageReport,
    Verdict,
)
from shikiru.domain.reports import TestRun as AgentTestRun
from shikiru.domain.steps import ACTING_ROLE, Step, Transition
from shikiru.domain.tasks import EscalationReason, Hold, Mode, RepoState, Role, Status, Task
from shikiru.engine.events import emit_turn_completed, emit_turn_started
from shikiru.engine.turn_execution import TurnExecution
from shikiru.operator.commands import OperatorCommands
from shikiru.operator.queries import TASK_DETAIL_EVENT_LIMIT, OperatorQueries
from shikiru.paths import run_dir, task_root, turns_dir
from shikiru.process import ProcessIdentity
from shikiru.store.commands import CommandQueue
from shikiru.store.escalations import EscalationStore
from shikiru.store.events import EventLog
from shikiru.store.reader import DASHBOARD_CHANGE_POLL_SECONDS, StateReader
from shikiru.store.runs import RunStore
from shikiru.store.tasks import TaskStore
from shikiru.tmux.client import Tmux, exact_target
from shikiru.tmux.session import HOME_WINDOW_NAME
from shikiru.view_log import view_log_path
from shikiru.workspace.facts import WorkspaceFacts, render_facts
from shikiru.workspace.reports import ENGINE_VERDICT_FILENAME
from tests.support.actions import (
    action_list,
    actions_screen,
    open_action,
    popup_screen,
    structure,
    title_text,
)
from tests.support.clock import FakeClock
from tests.support.config import write_minimal_project
from tests.support.dashboard import (
    StubServices,
    choose_details_entry,
    focus_inspector_details,
    ignore_palette_action,
    state_views,
)
from tests.support.entry_points import FakeDistribution
from tests.support.factories import IMPLEMENTER_RUNGS, LEADER_RUNG
from tests.support.git_repo import commit_all, init_repo
from tests.support.render import plain_text, rendered_lines
from tests.support.store import seed_task
from tests.support.waiting import UI_WAIT_SECONDS, wait_until
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
_COST_BASIS_BILLED = "billed"
_RESPONSIVE_TASK_SIZES = ((120, 40), (48, 20), (36, 20), (48, 14), (20, 8))
_STAGE_START_SECONDS = 3
_LEADER_START_SECONDS = 4
_IMPLEMENTER_SIGNAL_SECONDS = 6
_LATEST_SIGNAL_SECONDS = 8
_DETAIL_NOW_SECONDS = 9
_TIMELINE_PAGE_SIZE = 1
_HISTORY_OVERFLOW_EXTRA = 2
_OLD_ITERATION = 1
_CURRENT_ITERATION = 2


def _write_role_signals(project_root: Path, task_id: str) -> None:
    implementer_path = view_log_path(run_dir(project_root, task_id), Role.IMPLEMENTER.value)
    implementer_path.parent.mkdir(parents=True)
    implementer_rows = (
        {
            "ts": (START + timedelta(seconds=_LEADER_START_SECONDS)).isoformat(),
            "turn": "02-implement-i1",
            "signal": "SessionStarted",
        },
        {
            "ts": (START + timedelta(seconds=_IMPLEMENTER_SIGNAL_SECONDS)).isoformat(),
            "turn": "02-implement-i1",
            "signal": "ToolFinished",
        },
    )
    implementer_path.write_text(
        "\n".join([*(json.dumps(row) for row in implementer_rows), ""]), encoding="utf-8"
    )
    leader_path = view_log_path(run_dir(project_root, task_id), Role.LEADER.value)
    leader_path.parent.mkdir(parents=True)
    leader_row = {
        "ts": (START + timedelta(seconds=_LATEST_SIGNAL_SECONDS)).isoformat(),
        "turn": "03-verify-i1",
        "signal": "ToolStarted",
    }
    leader_path.write_text(f"{json.dumps(leader_row)}\n", encoding="utf-8")


def _append_step_event(
    events: EventLog,
    task_id: str,
    step: Step,
    iteration: int,
) -> None:
    events.append(
        EventKind.STEP_STARTED,
        task_id=task_id,
        message=f"{step.value} started",
        data={"step": step.value, "role": ACTING_ROLE[step].value, "iteration": iteration},
    )


def _milestone_state(detail: TaskDetailView, step: Step) -> str:
    label = "Merge" if step is Step.INTEGRATE else step.value.title()
    milestone = next(item for item in detail.milestones if item.stage == label)
    return milestone.state


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
    from collections.abc import Callable
    from pathlib import Path

    from textual.pilot import Pilot
    from textual.screen import Screen
    from textual.widget import Widget

    from shikiru.clock import Clock
    from shikiru.config.settings import Settings
    from shikiru.dashboard.contract import (
        NavigateHome,
        NavigateTaskWindow,
        OpenPaletteContext,
        TaskDetailView,
        TaskModelHistoryView,
        TaskSessionView,
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


@dataclass(frozen=True)
class _ReenteredStageCase:
    step: Step
    report_kind: ReportKind
    turn: str
    role: Role
    rung: Rung


@dataclass(frozen=True)
class _TurnCostCase:
    before: Usage
    after: Usage
    expected_cost: float | None
    expected_basis: str


def _dashboard_app(
    reader: StateReader,
    project_root: Path,
    *,
    focus_task: str | None,
    widget_names: list[str],
    navigate_task_window: NavigateTaskWindow | None = None,
    navigate_home: NavigateHome | None = None,
    open_palette_context: OpenPaletteContext | None = None,
    clock: Clock | None = None,
) -> DashboardApp:
    settings = load_settings(project_root)
    clock = clock or SystemClock()
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
        disk_refresh_seconds=settings.dashboard.disk_refresh_seconds,
        quota_warn_pct=settings.dashboard.gauges.quota_warn_pct,
        quota_alert_pct=settings.dashboard.gauges.quota_alert_pct,
        open_palette=ignore_palette_action,
        widget_names=widget_names,
        open_palette_context=open_palette_context,
        navigate_task_window=navigate_task_window,
        navigate_home=navigate_home,
    )


def _text(widget: Static) -> str:
    content = widget.content
    if isinstance(content, Text):
        return content.plain
    return content if isinstance(content, str) else plain_text(content)


def _visible_text_area_text(text_area: TextArea) -> str:
    return "\n".join(
        text_area.render_line(row).text for row in range(text_area.content_region.height)
    )


def _assert_text_area_cursor_is_visible(text_area: TextArea) -> None:
    cursor_row, _ = text_area.cursor_location
    visible_rows = text_area.content_region.height
    assert visible_rows > 0
    assert text_area.scroll_y <= cursor_row < text_area.scroll_y + visible_rows
    assert text_area.has_focus


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
        def refresh_task_detail(_: object | None) -> None:
            active_apps[0].refresh_task_detail_after_resume()

        build = popup_screen if request.action else actions_screen
        await active_apps[0].push_screen(
            build(commands, queries, reader, _unexpected_pane_selection, request),
            callback=refresh_task_detail,
        )

    app = _dashboard_app(
        reader,
        project_root,
        focus_task=task_id,
        widget_names=TASK_DETAIL_WIDGETS,
        open_palette_context=open_palette_context,
        clock=clock,
    )
    active_apps.append(app)
    return app


async def _open_action_popup(
    app: DashboardApp,
    pilot: Pilot[None],
    action: str,
) -> PaletteScreen:
    await pilot.press("ctrl+p")
    await wait_until(lambda: isinstance(app.screen, ActionsListScreen), timeout=UI_WAIT_SECONDS)
    await open_action(pilot, cast("ActionsListScreen", app.screen), action)
    await wait_until(
        lambda: isinstance(app.screen, PaletteScreen) and app.screen.is_mounted,
        timeout=UI_WAIT_SECONDS,
    )
    return cast("PaletteScreen", app.screen)


async def _submit_detail_command(
    app: DashboardApp,
    pilot: Pilot[None],
    queue: CommandQueue,
    action: str,
    *,
    role: Role | None = None,
) -> QueuedCommand:
    dashboard_screen = app.screen
    palette = await _open_action_popup(app, pilot, action)
    if role is not None:
        role_field = cast("Select[str]", palette.query_one("#role", Select))
        await wait_until(lambda: app.focused is role_field, timeout=UI_WAIT_SECONDS)
        role_field.value = role.value
        await pilot.press("tab")
        await wait_until(
            lambda: app.focused is palette.query_one("#palette-submit", Button),
            timeout=UI_WAIT_SECONDS,
        )
    await wait_until(
        lambda: not palette.query_one("#palette-submit", Button).disabled, timeout=UI_WAIT_SECONDS
    )
    await pilot.press("enter")
    await wait_until(lambda: len(queue.pending()) == 1, timeout=UI_WAIT_SECONDS)
    command = queue.pending()[0]
    await pilot.press("escape")
    await wait_until(lambda: app.screen is dashboard_screen, timeout=UI_WAIT_SECONDS)
    return command


async def _open_task_view(
    app: DashboardApp,
    pilot: Pilot[None],
    view_id: str,
) -> None:
    await choose_details_entry(pilot, view_id)
    await wait_until(
        lambda: view_id in app.screen.query_one("#task-detail-panel", TaskDetailPanel).open_views,
        timeout=UI_WAIT_SECONDS,
    )


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
    _record_subscribed_session(connection, clock, task.id)
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
        Verdict(
            decision="changes_requested",
            summary="Latest engine verdict",
            issues=[
                Issue(severity="major", description="first"),
                Issue(severity="major", description="second"),
                Issue(severity="minor", description="third"),
            ],
        ).model_dump_json()
    )
    _record_briefing_evidence(connection, clock, task.id, implementation_dir)
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
    _assert_briefing_data(detail, load_settings(project_root))
    assert detail.sessions[0].session_id == "session-current"
    assert detail.sessions[0].input_tokens == 12
    assert detail.sessions[0].billing_mode == BILLING_MODE_SUBSCRIPTION
    assert detail.sessions[0].billing_source == BILLING_SOURCE_OPERATOR_CONFIG
    assert detail.ownership == (Role.IMPLEMENTER.value,)

    await _assert_billing_details_are_visible(reader, project_root, task.id, clock)


def _record_briefing_evidence(
    connection: Connection, clock: Clock, task_id: str, turn_dir: Path
) -> None:
    facts = render_facts(
        WorkspaceFacts(
            commits=("abc1234 first", "def5678 second"),
            diff_stat=" a.py | 3 ++-\n 1 file changed, 2 insertions(+), 1 deletion(-)\n",
        )
    )
    (turn_dir / FACTS_FILENAME).write_text(facts)
    EventLog(connection, clock).append(
        EventKind.PR_OPENED,
        task_id=task_id,
        message="PR opened",
        data={"url": "https://example.test/o/r/pull/41", "branch": "b", "commits": []},
    )


def _assert_briefing_data(detail: TaskDetailView, settings: Settings) -> None:
    assert detail.results is not None
    assert detail.results.produced == ProducedNumbers(commits=2, files=1, insertions=2, deletions=1)
    assert detail.verdict_counts == VerdictCounts("changes_requested", 0, 2, 1)
    assert detail.results.tests == ReportedTests(passed=1, failed=0, skipped=0)
    assert detail.results.pull_request == PullRequestLine(
        "OPEN", "https://example.test/o/r/pull/41", 41, None, None
    )
    assert detail.max_iterations == settings.orchestration.max_iterations
    assert detail.started_at is None
    assert detail.escalation_remaining is None


def _record_subscribed_session(connection: Connection, clock: Clock, task_id: str) -> None:
    runs = RunStore(connection, clock)
    run = runs.start(
        task_id,
        Role.IMPLEMENTER,
        IMPLEMENTER_RUNGS[0],
        billing_declaration=BillingDeclaration(
            BILLING_MODE_SUBSCRIPTION,
            BILLING_SOURCE_OPERATOR_CONFIG,
        ),
    )
    runs.set_session(run.id, "session-current", "resume-current")
    runs.record_usage(
        run.id,
        Usage(
            input_tokens=12,
            output_tokens=7,
            cache_read_tokens=3,
            cache_write_tokens=1,
            cost_usd=0.25,
            cost_basis=_COST_BASIS_BILLED,
            context_used_pct=20,
        ),
    )
    runs.mark_taken_over(run.id)


async def _assert_billing_details_are_visible(
    reader: StateReader,
    project_root: Path,
    task_id: str,
    clock: Clock,
) -> None:
    app = _dashboard_app(
        reader,
        project_root,
        focus_task=task_id,
        widget_names=TASK_DETAIL_WIDGETS,
        clock=clock,
    )
    async with app.run_test(size=(90, 42)) as pilot:
        await _open_task_view(app, pilot, TASK_VIEW_USAGE)
        sessions_widget = app.screen.query_one("#task-detail-sessions", Static)
        await wait_until(
            lambda: "Implementer" in plain_text(sessions_widget.content),
            timeout=UI_WAIT_SECONDS,
        )
        row = next(
            line
            for line in rendered_lines(sessions_widget.content, width=90)
            if "Implementer" in line
        )
        assert row.split()[-3:] == ["19", "\N{EM DASH}", "20%"]


async def test_legacy_usage_keeps_known_values_and_marks_default_zeros_unknown(
    connection: Connection,
    database: Database,
    tmp_path: Path,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="Legacy usage remains inspectable")
    runs = RunStore(connection, clock)
    recorded = runs.start(task.id, Role.IMPLEMENTER, IMPLEMENTER_RUNGS[0])
    runs.record_usage(
        recorded.id,
        Usage(500, 50, 0, 0, 1.25, "billed", 10),
    )
    runs.start(task.id, Role.LEADER, IMPLEMENTER_RUNGS[0])
    priced_zero = runs.start(task.id, Role.LEADER, IMPLEMENTER_RUNGS[0])
    runs.record_usage(
        priced_zero.id,
        Usage(0, 0, 0, 0, 0.0, "api_equivalent", None),
    )
    connection.execute("ALTER TABLE runs DROP COLUMN usage_reported")
    connection.execute("ALTER TABLE runs DROP COLUMN billing_mode")
    connection.execute("ALTER TABLE runs DROP COLUMN billing_source")
    connection.execute("PRAGMA user_version = 7")
    migrated_connection = database.connect()
    migrated_connection.close()

    reader = StateReader(database, SystemClock())
    try:
        app = _dashboard_app(
            reader,
            project_root,
            focus_task=task.id,
            widget_names=TASK_DETAIL_WIDGETS,
        )
        async with app.run_test(size=(90, 42)) as pilot:
            await pilot.pause()
            await _open_task_view(app, pilot, TASK_VIEW_USAGE)
            sessions = app.screen.query_one("#task-detail-sessions", Static)
            await wait_until(
                lambda: "Role" in plain_text(sessions.content),
                timeout=UI_WAIT_SECONDS,
            )
            lines = rendered_lines(sessions.content, width=90)

            legacy = next(line for line in lines if "Implementer" in line)
            assert "550" in legacy
            assert "$1.25" in legacy
            assert "10%" in legacy
            silent = [line for line in lines if "Leader" in line]
            assert len(silent) == 2
            assert all(line.split()[-1] == "\N{EM DASH}" for line in silent)
            assert any(line.startswith("!") and "billing unknown" in line for line in lines)
    finally:
        reader.close()


async def test_task_history_keeps_closed_lineages_and_observed_model_deltas(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="Retained model history")
    runs = RunStore(connection, clock)
    events = EventLog(connection, clock)
    previous = runs.start(task.id, Role.IMPLEMENTER, Rung("codex", "gpt-5", "low"))
    _record_history_turn(
        events,
        task.id,
        previous.id,
        Role.IMPLEMENTER,
        agent="codex",
        model="gpt-5",
        effort="low",
        turn="01-implement-i1",
        input_tokens=3,
    )
    runs.record_usage(previous.id, Usage(3, 1, 0, 0, 0.0, "billed", None))
    runs.close(previous.id)
    clock.advance(1)
    current = runs.start(
        task.id,
        Role.IMPLEMENTER,
        Rung("claude", "sonnet", "low"),
        billing_declaration=BillingDeclaration(
            mode=BILLING_MODE_SUBSCRIPTION,
            source=BILLING_SOURCE_OPERATOR_CONFIG,
        ),
    )
    taken_over = runs.start(task.id, Role.LEADER, Rung("codex", "gpt-6-sol", "high"))
    runs.mark_taken_over(taken_over.id)
    _record_history_turn(
        events,
        task.id,
        current.id,
        Role.IMPLEMENTER,
        agent="claude",
        model="sonnet",
        effort="low",
        turn="02-implement-i1",
        input_tokens=4,
    )
    clock.advance(1)
    runs.set_rung(current.id, Rung("claude", "opus", "high"))
    _record_history_turn(
        events,
        task.id,
        current.id,
        Role.IMPLEMENTER,
        agent="claude",
        model="opus",
        effort="high",
        turn="03-verify-i1",
        input_tokens=7,
    )
    runs.record_usage(current.id, Usage(11, 2, 0, 0, 0.25, "billed", None))
    turns_path = run_dir(project_root, task.id)
    turns_path.mkdir(parents=True)
    (turns_path / "temporary-evidence").write_text("retained only for this test")
    shutil.rmtree(turns_path)

    detail = await state_views(reader, project_root, clock, StubServices()).task_detail(task.id)

    assert detail is not None
    assert detail.usage is not None
    assert detail.usage.input_tokens == 14
    assert sum(session.input_tokens for session in detail.sessions) == 14
    prior_view = next(session for session in detail.sessions if session.run_id == previous.id)
    current_view = next(session for session in detail.sessions if session.run_id == current.id)
    assert prior_view.current_lineage is False
    assert prior_view.status == "closed"
    assert prior_view.model == "gpt-5"
    assert prior_view.model_history[0].usage_delta is not None
    assert prior_view.model_history[0].usage_delta.input_tokens == 3
    assert current_view.current_lineage is True
    assert current_view.status == "live"
    assert current_view.billing_mode == BILLING_MODE_SUBSCRIPTION
    assert current_view.model == "opus"
    assert [item.model for item in current_view.model_history] == ["sonnet", "opus"]
    assert [item.effort for item in current_view.model_history] == ["low", "high"]
    deltas = [item.usage_delta for item in current_view.model_history]
    assert all(delta is not None for delta in deltas)
    assert [delta.input_tokens for delta in deltas if delta is not None] == [4, 7]
    taken_over_view = next(
        session for session in detail.sessions if session.run_id == taken_over.id
    )
    assert taken_over_view.current_lineage is True
    assert taken_over_view.status == "taken_over"

    app = _dashboard_app(
        reader,
        project_root,
        focus_task=task.id,
        widget_names=TASK_DETAIL_WIDGETS,
        clock=clock,
    )
    async with app.run_test(size=(90, 42)) as pilot:
        await _assert_task_history_rendering(app, pilot, task)


async def _assert_task_history_rendering(
    app: DashboardApp,
    pilot: Pilot[None],
    task: Task,
) -> None:
    await wait_until(
        lambda: task.title in _text(app.query_one(f"#{TASK_DETAIL_IDENTITY}", Static)),
        timeout=UI_WAIT_SECONDS,
    )
    await _open_task_view(app, pilot, TASK_VIEW_USAGE)
    sessions = app.screen.query_one(f"#{TASK_DETAIL_SESSIONS}", Static)
    await wait_until(lambda: "gpt-5" in plain_text(sessions.content), timeout=UI_WAIT_SECONDS)
    lines = rendered_lines(sessions.content, width=90)
    rows = {
        model: next(line for line in lines if model in line)
        for model in ("gpt-5", "opus", "gpt-6-sol")
    }
    assert rows["gpt-5"].endswith("old")
    assert not rows["opus"].endswith("old")
    assert rows["opus"].split()[-3:] == ["13", "\N{EM DASH}", "\N{EM DASH}"]
    assert not rows["gpt-6-sol"].endswith("old")
    assert not any("sonnet" in line for line in lines)


def _without_effort_has_gaps(session: TaskSessionView, history: TaskModelHistoryView) -> None:
    assert session.effort == "high"
    assert history.model == "gpt-5"
    assert history.effort is None
    assert history.identity_complete is False
    assert history.usage_delta is None
    assert TaskHistoryGap.MISSING_RECORDED_EFFORT in session.history_gaps
    assert TaskHistoryGap.INCOMPLETE_IDENTITY in session.history_gaps
    assert TaskHistoryGap.MISSING_USAGE_DELTA in session.history_gaps


def _numeric_cost_is_unknown_basis(session: TaskSessionView, history: TaskModelHistoryView) -> None:
    del session
    assert history.usage_delta is not None
    assert history.usage_delta.cost_usd == 0.03
    assert history.usage_delta.cost_basis == COST_BASIS_UNKNOWN


@pytest.mark.parametrize(
    ("turn_identity", "cost_usd", "check"),
    [
        pytest.param({"model": "gpt-5"}, None, _without_effort_has_gaps, id="turn-without-effort"),
        pytest.param(
            {"model": "gpt-6", "effort": "high"},
            0.03,
            _numeric_cost_is_unknown_basis,
            id="numeric-turn-cost",
        ),
    ],
)
async def test_legacy_turn_never_inherits_run_level_provenance(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
    *,
    turn_identity: dict[str, str],
    cost_usd: float | None,
    check: Callable[[TaskSessionView, TaskModelHistoryView], None],
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="Legacy turn provenance")
    run = RunStore(connection, clock).start(
        task.id, Role.IMPLEMENTER, Rung("codex", "gpt-6", "high")
    )
    RunStore(connection, clock).record_usage(
        run.id, Usage(30, 2, 0, 0, 0.8, COST_BASIS_BILLED, None)
    )
    events = EventLog(connection, clock)
    turn_data = {
        "role": Role.IMPLEMENTER.value,
        "agent": "codex",
        "run_id": run.id,
        "turn": "01-implement-i1",
        **turn_identity,
    }
    events.append(EventKind.TURN_STARTED, task_id=task.id, message="turn", data=turn_data)
    events.append(
        EventKind.TURN_COMPLETED,
        task_id=task.id,
        message="turn",
        data={
            **turn_data,
            "usage_delta": {
                "input_tokens": 5,
                "output_tokens": 2,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "cost_usd": cost_usd,
            },
        },
    )

    detail = await state_views(reader, project_root, clock, StubServices()).task_detail(task.id)

    assert detail is not None
    [session] = detail.sessions
    [history] = session.model_history
    check(session, history)


async def test_recorded_stop_is_complete_but_unended_turn_has_a_history_gap(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="Stopped and unended turns")
    runs = RunStore(connection, clock)
    rung = Rung("codex", "gpt-6", "high")
    stopped_run = runs.start(task.id, Role.IMPLEMENTER, rung)
    unended_run = runs.start(task.id, Role.IMPLEMENTER, rung)
    events = EventLog(connection, clock)
    stopped_data = {
        "role": Role.IMPLEMENTER.value,
        "agent": rung.agent,
        "model": rung.model,
        "effort": rung.effort,
        "run_id": stopped_run.id,
        "turn": "stopped-turn",
    }
    events.append(
        EventKind.TURN_STARTED,
        task_id=task.id,
        message="turn started",
        data=stopped_data,
    )
    events.append(
        EventKind.TURN_STOPPED,
        task_id=task.id,
        message="turn stopped",
        data=stopped_data,
    )
    unended_data = {
        **stopped_data,
        "run_id": unended_run.id,
        "turn": "unended-turn",
    }
    events.append(
        EventKind.TURN_STARTED,
        task_id=task.id,
        message="turn started",
        data=unended_data,
    )

    detail = await state_views(reader, project_root, clock, StubServices()).task_detail(task.id)

    assert detail is not None
    stopped = next(session for session in detail.sessions if session.run_id == stopped_run.id)
    unended = next(session for session in detail.sessions if session.run_id == unended_run.id)
    assert stopped.model_history[0].status == "interrupted"
    assert TaskHistoryGap.UNENDED_TURN not in stopped.history_gaps
    assert unended.model_history[0].status == "interrupted"
    assert TaskHistoryGap.UNENDED_TURN in unended.history_gaps


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            _TurnCostCase(
                Usage(2, 1, 0, 0, 0.1, COST_BASIS_BILLED, None),
                Usage(5, 3, 0, 0, 0.12, COST_BASIS_BILLED, None),
                0.02,
                COST_BASIS_BILLED,
            ),
            id="comparable-billed-cost",
        ),
        pytest.param(
            _TurnCostCase(
                Usage(2, 1, 0, 0, 0.1, COST_BASIS_API_EQUIVALENT, None),
                Usage(5, 3, 0, 0, 0.12, COST_BASIS_API_EQUIVALENT, None),
                0.02,
                COST_BASIS_API_EQUIVALENT,
            ),
            id="comparable-estimate",
        ),
        pytest.param(
            _TurnCostCase(
                Usage(2, 1, 0, 0, 0.1, COST_BASIS_BILLED, None),
                Usage(5, 3, 0, 0, 0.12, COST_BASIS_API_EQUIVALENT, None),
                None,
                COST_BASIS_UNKNOWN,
            ),
            id="basis-changed",
        ),
        pytest.param(
            _TurnCostCase(
                Usage(2, 1, 0, 0, None, COST_BASIS_UNKNOWN, None),
                Usage(5, 3, 0, 0, 0.12, COST_BASIS_BILLED, None),
                None,
                COST_BASIS_UNKNOWN,
            ),
            id="baseline-unavailable",
        ),
        pytest.param(
            _TurnCostCase(
                Usage(2, 1, 0, 0, 0.1, COST_BASIS_BILLED, None),
                Usage(5, 3, 0, 0, None, COST_BASIS_BILLED, None),
                None,
                COST_BASIS_UNKNOWN,
            ),
            id="latest-amount-unavailable",
        ),
        pytest.param(
            _TurnCostCase(
                Usage(5, 3, 0, 0, 0.12, COST_BASIS_BILLED, None),
                Usage(8, 5, 0, 0, 0.1, COST_BASIS_BILLED, None),
                None,
                COST_BASIS_UNKNOWN,
            ),
            id="cumulative-amount-decreased",
        ),
    ],
)
async def test_turn_cost_basis_requires_comparable_recorded_cumulative_amounts(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
    case: _TurnCostCase,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="Comparable turn cost")
    rung = Rung("claude", "opus", "high")
    run = RunStore(connection, clock).start(task.id, Role.IMPLEMENTER, rung)
    RunStore(connection, clock).record_usage(run.id, case.after)
    events = EventLog(connection, clock)
    turn = "01-implement-i1"
    emit_turn_started(
        events,
        task,
        Step.IMPLEMENT,
        Role.IMPLEMENTER,
        rung,
        run_id=run.id,
        turn=turn,
    )
    report = PlanReport(summary="Turn complete", commits=[])
    execution = TurnExecution(
        result=Completed(report),
        task_id=task.id,
        role=Role.IMPLEMENTER,
        turn_dir=tmp_path / turn,
        rung=rung,
        run_id=run.id,
        usage_before=case.before,
        usage_after=case.after,
    )
    emit_turn_completed(events, task, report, execution)

    detail = await state_views(reader, project_root, clock, StubServices()).task_detail(task.id)

    assert detail is not None
    [session] = detail.sessions
    [history] = session.model_history
    assert history.usage_delta is not None
    assert history.usage_delta.input_tokens == 3
    assert history.usage_delta.output_tokens == 2
    if case.expected_cost is None:
        assert history.usage_delta.cost_usd is None
    else:
        assert history.usage_delta.cost_usd == pytest.approx(case.expected_cost)
    assert history.usage_delta.cost_basis == case.expected_basis


async def test_header_uptime_tracks_recorded_engine_restarts_and_stopped_duration(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    events = EventLog(connection, clock)
    first_identity = ProcessIdentity(4321, 1_700_000_000.0)
    events.append(
        EventKind.ENGINE_STARTED,
        message="engine started",
        data={
            "engine_pid": first_identity.pid,
            "engine_process_started_at": first_identity.started_at,
        },
    )
    running_views = state_views(
        reader,
        project_root,
        clock,
        StubServices(engine_running=True, engine_process_identity=first_identity),
    )
    clock.advance(12)

    live_header = await running_views.header()

    assert live_header.engine_up is True
    assert live_header.engine_uptime_seconds == 12
    assert live_header.previous_engine_run_seconds is None
    events.append(
        EventKind.ENGINE_STOPPED,
        message="engine stopped",
        data={
            "engine_pid": first_identity.pid,
            "engine_process_started_at": first_identity.started_at,
        },
    )
    stopped_views = state_views(reader, project_root, clock, StubServices())
    first_stopped_header = await stopped_views.header()
    clock.advance(60)
    later_stopped_header = await stopped_views.header()

    assert first_stopped_header.engine_up is False
    assert first_stopped_header.engine_uptime_seconds is None
    assert first_stopped_header.previous_engine_run_seconds == 12
    assert later_stopped_header.previous_engine_run_seconds == 12

    second_identity = ProcessIdentity(4322, 1_700_000_100.0)
    events.append(
        EventKind.ENGINE_STARTED,
        message="engine restarted",
        data={
            "engine_pid": second_identity.pid,
            "engine_process_started_at": second_identity.started_at,
        },
    )
    restarted_views = state_views(
        reader,
        project_root,
        clock,
        StubServices(engine_running=True, engine_process_identity=second_identity),
    )
    restarted_header = await restarted_views.header()
    mismatched_views = state_views(
        reader,
        project_root,
        clock,
        StubServices(
            engine_running=True,
            engine_process_identity=ProcessIdentity(9999, 1_700_000_100.0),
        ),
    )
    mismatched_header = await mismatched_views.header()
    no_stop_header = await state_views(
        reader,
        project_root,
        clock,
        StubServices(),
    ).header()

    assert restarted_header.engine_uptime_seconds == 0
    assert restarted_header.previous_engine_run_seconds is None
    assert mismatched_header.engine_uptime_seconds is None
    assert no_stop_header.previous_engine_run_seconds is None


async def test_ambiguous_legacy_turn_evidence_is_not_assigned_to_multiple_lineages(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="Ambiguous retained turn")
    runs = RunStore(connection, clock)
    runs.start(task.id, Role.IMPLEMENTER, Rung("codex", "gpt-5", "low"))
    runs.start(task.id, Role.IMPLEMENTER, Rung("codex", "gpt-5", "low"))
    events = EventLog(connection, clock)
    turn_data = {
        "role": Role.IMPLEMENTER.value,
        "agent": "codex",
        "model": "gpt-5",
        "turn": "01-implement-i1",
    }
    events.append(
        EventKind.TURN_STARTED,
        task_id=task.id,
        message="legacy turn started",
        data=turn_data,
    )
    events.append(
        EventKind.TURN_COMPLETED,
        task_id=task.id,
        message="legacy turn completed",
        data={
            **turn_data,
            "usage_delta": {
                "input_tokens": 5,
                "output_tokens": 2,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "cost_usd": None,
            },
        },
    )

    detail = await state_views(reader, project_root, clock, StubServices()).task_detail(task.id)

    assert detail is not None
    assert len(detail.sessions) == 2
    assert all(not session.model_history for session in detail.sessions)
    assert all(TaskHistoryGap.UNLINKED_TURN in session.history_gaps for session in detail.sessions)


def _record_history_turn(
    events: EventLog,
    task_id: str,
    run_id: int,
    role: Role,
    *,
    agent: str,
    model: str,
    effort: str,
    turn: str,
    input_tokens: int,
) -> None:
    data = {
        "role": role.value,
        "agent": agent,
        "model": model,
        "effort": effort,
        "run_id": run_id,
        "turn": turn,
    }
    events.append(
        EventKind.TURN_STARTED,
        task_id=task_id,
        message=f"{turn} started",
        data=data,
    )
    events.append(
        EventKind.TURN_COMPLETED,
        task_id=task_id,
        message=f"{turn} completed",
        data={
            **data,
            "usage_delta": {
                "input_tokens": input_tokens,
                "output_tokens": 1,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "cost_usd": None,
            },
        },
    )


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


async def test_latest_signal_keeps_global_role_identity_past_filtered_evidence_and_task_state(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="Retained role signals", mode=Mode.IMPLEMENT)
    tasks = TaskStore(connection, clock)
    events = EventLog(connection, clock)
    events.append(EventKind.TASK_QUEUED, task_id=task.id, message="queued")
    task = tasks.require(task.id)
    tasks.apply_transition(
        task.id,
        Transition.stay(task, step=Step.VERIFY, status=Status.RUNNING),
    )
    clock.advance(_STAGE_START_SECONDS)
    events.append(
        EventKind.STEP_STARTED,
        task_id=task.id,
        message="verify started",
        data={"step": Step.VERIFY.value, "role": Role.LEADER.value, "iteration": task.iteration},
    )
    runs = RunStore(connection, clock)
    implementer = runs.start(
        task.id,
        Role.IMPLEMENTER,
        IMPLEMENTER_RUNGS[0],
        billing_declaration=BillingDeclaration(BILLING_MODE_API, BILLING_SOURCE_OPERATOR_CONFIG),
    )
    runs.close(implementer.id)
    clock.advance(_LEADER_START_SECONDS - _STAGE_START_SECONDS)
    leader = runs.start(
        task.id,
        Role.LEADER,
        LEADER_RUNG,
        billing_declaration=BillingDeclaration(
            BILLING_MODE_SUBSCRIPTION,
            BILLING_SOURCE_OPERATOR_CONFIG,
        ),
    )
    clock.advance(_IMPLEMENTER_SIGNAL_SECONDS - _LEADER_START_SECONDS)
    _write_role_signals(project_root, task.id)
    clock.advance(_DETAIL_NOW_SECONDS - _LATEST_SIGNAL_SECONDS)
    settings = load_settings(project_root)
    settings = settings.model_copy(
        update={
            "dashboard": settings.dashboard.model_copy(
                update={"timeline_page_size": _TIMELINE_PAGE_SIZE}
            )
        }
    )
    views = state_views(reader, project_root, clock, StubServices(), settings=settings)

    filtered_latest = await views.task_timeline(
        task.id,
        cursor=None,
        role=Role.IMPLEMENTER.value,
        turn=None,
        category=TaskTimelineCategory.SIGNAL.value,
    )
    assert filtered_latest is not None
    assert filtered_latest.older_cursor is not None
    older = await views.task_timeline(
        task.id,
        cursor=filtered_latest.older_cursor,
        role=Role.IMPLEMENTER.value,
        turn=None,
        category=TaskTimelineCategory.SIGNAL.value,
    )
    detail = await views.task_detail(task.id)

    assert older is not None
    assert older.entries[0].turn == "02-implement-i1"
    assert older.entries[0].summary == "SessionStarted"
    assert detail is not None
    assert detail.latest_signal == "ToolStarted"
    assert detail.latest_signal_at == START + timedelta(seconds=_LATEST_SIGNAL_SECONDS)
    assert detail.actual_stage_started_at == START + timedelta(seconds=_STAGE_START_SECONDS)

    tasks.set_holds(task.id, frozenset({Hold.PAUSED}))
    paused = await views.task_detail(task.id)
    assert paused is not None
    assert paused.latest_signal_at == detail.latest_signal_at

    current_task = tasks.require(task.id)
    tasks.apply_transition(
        task.id,
        Transition.stay(current_task, step=None, status=Status.DONE),
    )
    runs.close(leader.id)
    completed = await views.task_detail(task.id)
    assert completed is not None
    assert completed.actual_stage is None
    assert completed.latest_signal_at == detail.latest_signal_at


@pytest.mark.parametrize(
    ("mode", "step"),
    [(Mode.PLAN, Step.PLAN), (Mode.IMPLEMENT, Step.IMPLEMENT)],
    ids=("planning-bypass", "implementation-bypass"),
)
async def test_first_recorded_plan_or_implement_transition_marks_triage_bypassed(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
    mode: Mode,
    step: Step,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="Explicit workflow mode", mode=mode)
    events = EventLog(connection, clock)
    events.append(EventKind.TASK_QUEUED, task_id=task.id, message="queued")
    TaskStore(connection, clock).apply_transition(
        task.id,
        Transition.stay(task, step=step, status=Status.RUNNING),
    )
    _append_step_event(events, task.id, step, task.iteration)

    detail = await state_views(reader, project_root, clock, StubServices()).task_detail(task.id)

    assert detail is not None
    assert _milestone_state(detail, Step.TRIAGE) == "skipped"
    assert _milestone_state(detail, step) == "current"


@pytest.mark.parametrize(
    "step",
    [Step.CONSULT, Step.VERIFY],
    ids=("plan-integration-consult", "observed-plan-verify"),
)
async def test_plan_detail_keeps_current_stage_and_prior_gaps_without_retained_events(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
    step: Step,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="Plan integration recovery", mode=Mode.PLAN)
    tasks = TaskStore(connection, clock)
    tasks.apply_transition(
        task.id,
        Transition.stay(
            tasks.require(task.id),
            step=step,
            status=Status.INTEGRATING,
        ),
    )

    detail = await state_views(reader, project_root, clock, StubServices()).task_detail(task.id)

    assert detail is not None
    assert _milestone_state(detail, step) == "current"
    for earlier in (Step.TRIAGE, Step.PLAN, Step.INTEGRATE):
        if earlier is not step:
            assert _milestone_state(detail, earlier) == "unknown"


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            _ReenteredStageCase(
                Step.IMPLEMENT,
                ReportKind.IMPLEMENTATION,
                "02-implement-i1",
                Role.IMPLEMENTER,
                IMPLEMENTER_RUNGS[0],
            ),
            id="implementation-reentered",
        ),
        pytest.param(
            _ReenteredStageCase(
                Step.VERIFY,
                ReportKind.VERDICT,
                "03-verify-i1",
                Role.LEADER,
                LEADER_RUNG,
            ),
            id="verification-reentered",
        ),
    ],
)
async def test_older_iteration_report_does_not_complete_the_reentered_stage(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
    case: _ReenteredStageCase,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="Re-entered stage", mode=Mode.IMPLEMENT)
    events = EventLog(connection, clock)
    events.append(EventKind.TASK_QUEUED, task_id=task.id, message="queued")
    runs = RunStore(connection, clock)
    previous = runs.start(task.id, case.role, case.rung)
    _append_step_event(events, task.id, case.step, _OLD_ITERATION)
    events.append(
        EventKind.TURN_STARTED,
        task_id=task.id,
        message="previous iteration started",
        data={
            "step": case.step.value,
            "role": case.role.value,
            "agent": case.rung.agent,
            "model": case.rung.model,
            "effort": case.rung.effort,
            "iteration": _OLD_ITERATION,
            "run_id": previous.id,
            "turn": case.turn,
        },
    )
    runs.close(previous.id)
    _write_report_turn(
        turns_dir(project_root, task.id),
        case.turn,
        case.report_kind,
        "A prior iteration recorded a successful outcome.",
        None,
    )
    current_transition = replace(
        Transition.stay(task, step=case.step, status=Status.RUNNING),
        iteration=_CURRENT_ITERATION,
    )
    TaskStore(connection, clock).apply_transition(task.id, current_transition)
    _append_step_event(events, task.id, case.step, _CURRENT_ITERATION)

    detail = await state_views(reader, project_root, clock, StubServices()).task_detail(task.id)

    assert detail is not None
    assert _milestone_state(detail, case.step) == "current"


async def test_rework_and_truncated_workflow_history_keep_stage_outcomes_honest(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="Implementation rework", mode=Mode.IMPLEMENT)
    events = EventLog(connection, clock)
    events.append(EventKind.TASK_QUEUED, task_id=task.id, message="queued")
    _append_step_event(events, task.id, Step.IMPLEMENT, _OLD_ITERATION)
    _append_step_event(events, task.id, Step.VERIFY, _OLD_ITERATION)
    events.append(
        EventKind.VERDICT,
        task_id=task.id,
        message="changes requested",
        data={"decision": "changes_requested"},
    )
    current_transition = replace(
        Transition.stay(task, step=Step.IMPLEMENT, status=Status.RUNNING),
        iteration=_CURRENT_ITERATION,
    )
    TaskStore(connection, clock).apply_transition(task.id, current_transition)
    _append_step_event(events, task.id, Step.IMPLEMENT, _CURRENT_ITERATION)
    turns_root = turns_dir(project_root, task.id)
    _write_report_turn(
        turns_root,
        "02-implement-i1",
        ReportKind.IMPLEMENTATION,
        "The earlier implementation passed its recorded checks.",
        None,
    )

    rework = await state_views(reader, project_root, clock, StubServices()).task_detail(task.id)

    assert rework is not None
    assert _milestone_state(rework, Step.IMPLEMENT) == "current"
    assert _milestone_state(rework, Step.VERIFY) == "rework"

    truncated = seed_task(connection, clock, title="Truncated workflow", mode=Mode.IMPLEMENT)
    events.append(EventKind.TASK_QUEUED, task_id=truncated.id, message="queued")
    truncated_task = TaskStore(connection, clock).require(truncated.id)
    TaskStore(connection, clock).apply_transition(
        truncated.id,
        Transition.stay(truncated_task, step=Step.VERIFY, status=Status.RUNNING),
    )
    _append_step_event(events, truncated.id, Step.TRIAGE, _OLD_ITERATION)
    _append_step_event(events, truncated.id, Step.IMPLEMENT, _OLD_ITERATION)
    _append_step_event(events, truncated.id, Step.VERIFY, _OLD_ITERATION)
    for _ in range(TASK_DETAIL_EVENT_LIMIT + _HISTORY_OVERFLOW_EXTRA):
        events.append(EventKind.NUDGE, task_id=truncated.id, message="retained activity")

    incomplete = await state_views(reader, project_root, clock, StubServices()).task_detail(
        truncated.id
    )

    assert incomplete is not None
    assert _milestone_state(incomplete, Step.TRIAGE) == "done"
    assert _milestone_state(incomplete, Step.IMPLEMENT) == "done"
    assert _milestone_state(incomplete, Step.VERIFY) == "current"

    consulting = seed_task(connection, clock, title="Consulting", mode=Mode.IMPLEMENT)
    events.append(EventKind.TASK_QUEUED, task_id=consulting.id, message="queued")
    consulting_task = TaskStore(connection, clock).require(consulting.id)
    TaskStore(connection, clock).apply_transition(
        consulting.id,
        Transition.stay(consulting_task, step=Step.CONSULT, status=Status.RUNNING),
    )
    _append_step_event(events, consulting.id, Step.TRIAGE, _OLD_ITERATION)
    _append_step_event(events, consulting.id, Step.IMPLEMENT, _OLD_ITERATION)
    _append_step_event(events, consulting.id, Step.CONSULT, _OLD_ITERATION)

    asking = await state_views(reader, project_root, clock, StubServices()).task_detail(
        consulting.id
    )

    assert asking is not None
    assert _milestone_state(asking, Step.IMPLEMENT) == "done"
    assert _milestone_state(asking, Step.CONSULT) == "current"

    queued = seed_task(connection, clock, title="Not started", mode=Mode.IMPLEMENT)
    events.append(EventKind.TASK_QUEUED, task_id=queued.id, message="queued")

    waiting = await state_views(reader, project_root, clock, StubServices()).task_detail(queued.id)

    assert waiting is not None
    assert {item.state for item in waiting.milestones} == {"later"}


async def test_enter_keeps_inline_selection_and_popup_close_restores_browser_scroll(
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
            await wait_until(lambda: list_view.index == 8, timeout=UI_WAIT_SECONDS)
            selected_index = list_view.index
            assert selected_index is not None
            selected_row = list_view.children[selected_index]
            selected_id = selected_row.id
            scroll_y = list_view.scroll_y
            home_screen = app.screen

            await pilot.press("enter")
            await wait_until(
                lambda: "Browser task 8" in _text(app.query_one("#task-detail-identity", Static)),
                timeout=UI_WAIT_SECONDS,
            )
            assert app.screen is home_screen
            await pilot.press("?")
            await wait_until(
                lambda: bool(app.screen.query("#workspace-help-copy")),
                timeout=UI_WAIT_SECONDS,
            )
            await pilot.press("escape")
            await wait_until(lambda: app.screen is home_screen, timeout=UI_WAIT_SECONDS)

            restored_list = app.query_one("#task-browser-list", ListView)
            assert restored_list.index == 8
            restored_index = restored_list.index
            assert restored_index is not None
            assert restored_list.children[restored_index].id == selected_id
            assert restored_list.scroll_y == scroll_y
            assert app.focused is restored_list
    finally:
        reader.close()


async def test_detail_ctrl_p_carries_the_task_and_its_current_role(
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
    current_task = TaskStore(connection, clock).get(task.id)
    assert current_task is not None
    TaskStore(connection, clock).apply_transition(
        task.id,
        Transition.stay(current_task, step=Step.VERIFY, status=Status.RUNNING),
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
            await wait_until(
                lambda: "Verifying" in _text(app.query_one(f"#{TASK_DETAIL_IDENTITY}", Static)),
                timeout=UI_WAIT_SECONDS,
            )
            await pilot.press("ctrl+p")
            await wait_until(lambda: bool(opened), timeout=UI_WAIT_SECONDS)
            app.query_one(
                f"#{TASK_DETAIL_ACTION_PREFIX}{BriefingAction.PAUSE.value}", Button
            ).focus()
            await pilot.press("enter")
            await wait_until(lambda: len(opened) == 2, timeout=UI_WAIT_SECONDS)
            app.query_one(
                f"#{TASK_DETAIL_ACTION_PREFIX}{BriefingAction.CANCEL.value}", Button
            ).focus()
            await pilot.press("enter")
            await wait_until(lambda: len(opened) == 3, timeout=UI_WAIT_SECONDS)
            await focus_inspector_details(pilot)
            await pilot.press("v")
            await wait_until(lambda: isinstance(app.focused, FoldHeader), timeout=UI_WAIT_SECONDS)

        assert opened == [
            PaletteRequest(task_id=task.id, role=Role.LEADER.value),
            PaletteRequest(
                action=BriefingAction.PAUSE.value, task_id=task.id, role=Role.LEADER.value
            ),
            PaletteRequest(
                action=BriefingAction.CANCEL.value, task_id=task.id, role=Role.LEADER.value
            ),
        ]
    finally:
        reader.close()


async def _assert_responsive_task_sections(
    app: DashboardApp,
    pilot: Pilot[None],
    task: Task,
    run: Run,
) -> None:
    body = app.query_one(f"#{TASK_DETAIL_BODY}", VerticalScroll)
    identity = app.query_one(f"#{TASK_DETAIL_IDENTITY}", Static)
    ownership = app.query_one(f"#{TASK_DETAIL_OWNERSHIP}", Static)
    briefing = app.query_one(f"#{TASK_DETAIL_BRIEFING}", Static)
    assert task.id in _text(identity)
    assert task.title in _text(identity)
    body.scroll_to_widget(identity, animate=False)
    await pilot.pause()
    assert identity.region.overlaps(body.scrollable_content_region)

    body.scroll_to_widget(ownership, animate=False)
    await pilot.pause()
    assert Hold.PAUSED.value in _text(ownership)
    assert Hold.INTERVENTION.value in _text(ownership)
    assert ownership.region.overlaps(body.scrollable_content_region)

    body.scroll_to_widget(briefing, animate=False)
    await pilot.pause()
    assert "Escalation open" in plain_text(briefing.content)
    assert briefing.region.overlaps(body.scrollable_content_region)

    await _open_task_view(app, pilot, TASK_VIEW_USAGE)
    detail_panel = app.screen.query_one("#task-detail-panel", TaskDetailPanel)
    detail_body = detail_panel.query_one(f"#{TASK_DETAIL_BODY}", VerticalScroll)
    detailed_sessions = detail_panel.query_one(f"#{TASK_DETAIL_SESSIONS}", Static)
    await wait_until(
        lambda: detailed_sessions.display and run.model in plain_text(detailed_sessions.content),
        timeout=UI_WAIT_SECONDS,
    )
    detail_body.scroll_to_widget(detailed_sessions, animate=False)
    await pilot.pause()
    assert detailed_sessions.region.overlaps(detail_body.scrollable_content_region)
    await pilot.press("escape")


async def _assert_responsive_task_actions(
    app: DashboardApp,
    pilot: Pilot[None],
    task: Task,
) -> None:
    await pilot.press("ctrl+p")
    await wait_until(lambda: isinstance(app.screen, ActionsListScreen), timeout=UI_WAIT_SECONDS)
    listing = cast("ActionsListScreen", app.screen)
    assert task.id in title_text(listing)
    rows = action_list(listing)
    assert rows.region.width > 0
    assert rows.region.x >= 0
    assert rows.region.right <= app.size.width
    await wait_until(
        lambda: {"Submit task", "Start engine"} <= set(structure(listing)), timeout=UI_WAIT_SECONDS
    )
    await pilot.press("escape")
    await wait_until(lambda: not isinstance(app.screen, ActionsListScreen), timeout=UI_WAIT_SECONDS)


async def _assert_task_context_keeps_priority(app: DashboardApp, pilot: Pilot[None]) -> None:
    await choose_details_entry(pilot, TASK_VIEW_CONTEXT)
    await wait_until(
        lambda: (
            "Priority high (5)"
            in _text(app.screen.query_one(f"#{TASK_DETAIL_CONTEXT_INFO}", Static))
        ),
        timeout=UI_WAIT_SECONDS,
    )


async def test_compact_task_detail_keeps_decisions_usage_and_actions_reachable(
    connection: Connection,
    database: Database,
    tmp_path: Path,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = seed_task(
        connection,
        clock,
        title="A complete task title remains reachable in compact panes",
        priority=5,
        mode=Mode.IMPLEMENT,
    )
    tasks = TaskStore(connection, clock)
    escalation_reason = EscalationReason.DECISION_REQUIRED
    tasks.apply_transition(task.id, Transition.escalate_current(task, escalation_reason))
    tasks.set_holds(task.id, frozenset({Hold.PAUSED, Hold.INTERVENTION}))
    EscalationStore(connection, clock).open(
        task.id,
        step=None,
        reason=escalation_reason,
        headline="The task needs an operator decision",
        packet_path="unused.packet",
        options=options_for(escalation_reason),
    )
    run = RunStore(connection, clock).start(task.id, Role.IMPLEMENTER, IMPLEMENTER_RUNGS[0])
    RunStore(connection, clock).record_usage(run.id, Usage(8, 5, 0, 0, 0.12, "billed", 8))
    reader = StateReader(database, clock)
    queue = CommandQueue(connection, clock)
    try:
        app = _operator_task_app(reader, project_root, task.id, queue, clock)
        async with app.run_test(size=_RESPONSIVE_TASK_SIZES[0]) as pilot:
            await wait_until(
                lambda: task.title in _text(app.query_one(f"#{TASK_DETAIL_IDENTITY}", Static)),
                timeout=UI_WAIT_SECONDS,
            )

            for width, height in _RESPONSIVE_TASK_SIZES:
                if (width, height) != _RESPONSIVE_TASK_SIZES[0]:
                    await pilot.resize_terminal(width, height)
                    await pilot.wait_for_scheduled_animations()
                await _assert_responsive_task_sections(app, pilot, task, run)
                await _assert_responsive_task_actions(app, pilot, task)

                if (width, height) == _RESPONSIVE_TASK_SIZES[0]:
                    await _assert_task_context_keeps_priority(app, pilot)
    finally:
        reader.close()


async def test_compact_palette_form_keeps_visible_text_cursor_and_run_after_resize(
    connection: Connection,
    database: Database,
    tmp_path: Path,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="Compact request form")
    queue = CommandQueue(connection, clock)
    for submitted in queue.pending():
        queue.mark_done(submitted.id)
    reader = StateReader(database, clock)
    try:
        app = _operator_task_app(reader, project_root, task.id, queue, clock)
        async with app.run_test(size=(36, 20)) as pilot:
            await wait_until(clock.has_pending_sleep, timeout=UI_WAIT_SECONDS)
            palette = await _open_action_popup(app, pilot, "submit")
            await wait_until(
                lambda: palette.query_one("#field-request").display, timeout=UI_WAIT_SECONDS
            )
            request = palette.query_one("#request", TextArea)
            request.text = ""
            request.focus()
            await pilot.press(*"visible request text")
            await pilot.press("end", "enter", *"second line")
            await pilot.pause()
            assert request.text == "visible request text\nsecond line"
            _assert_text_area_cursor_is_visible(request)
            assert "second line" in _visible_text_area_text(request)
            submit = palette.query_one("#palette-submit", Button)
            assert submit.region.y >= 0
            assert submit.region.bottom <= app.size.height
            await pilot.resize_terminal(48, 14)
            await pilot.pause()
            assert request.text == "visible request text\nsecond line"
            _assert_text_area_cursor_is_visible(request)
            assert "second line" in _visible_text_area_text(request)
            assert submit.region.y >= 0
            assert submit.region.bottom <= app.size.height
    finally:
        reader.close()


async def test_task_evidence_filter_and_reading_position_survive_resize_and_refresh(
    connection: Connection,
    database: Database,
    tmp_path: Path,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="Evidence reading position")
    queue = CommandQueue(connection, clock)
    reader = StateReader(database, clock)
    try:
        app = _operator_task_app(reader, project_root, task.id, queue, clock)
        async with app.run_test(size=(36, 20)) as pilot:
            await wait_until(
                lambda: task.title in _text(app.query_one(f"#{TASK_DETAIL_IDENTITY}", Static)),
                timeout=UI_WAIT_SECONDS,
            )
            await _open_task_view(app, pilot, TASK_VIEW_AGENT)
            detail_screen = app.screen
            detail_screen.query_one(f"#{TASK_DETAIL_AGENT_LIST}").focus()
            await pilot.press("f")
            bar = detail_screen.query_one(f"#{AGENT_TIMELINE_IDS.filters}", TimelineFilterBar)
            detail_body = detail_screen.query_one(f"#{TASK_DETAIL_BODY}", VerticalScroll)

            def filter_visible() -> bool:
                return (
                    bar.region.x >= 0
                    and bar.region.right <= app.size.width
                    and bar.region.y >= detail_body.region.y
                    and bar.region.bottom <= detail_body.region.bottom
                    and bar.region.y >= 0
                    and bar.region.bottom <= app.size.height
                )

            await wait_until(lambda: app.focused is bar, timeout=UI_WAIT_SECONDS)
            await pilot.press("r")
            await pilot.wait_for_scheduled_animations()
            await wait_until(filter_visible, timeout=UI_WAIT_SECONDS)
            await pilot.resize_terminal(48, 14)
            await pilot.wait_for_scheduled_animations()
            await wait_until(filter_visible, timeout=UI_WAIT_SECONDS)
            assert app.focused is bar

            TaskStore(connection, clock).set_holds(task.id, frozenset({Hold.PAUSED}))
            await wait_until(clock.has_pending_sleep, timeout=UI_WAIT_SECONDS)
            clock.advance(DASHBOARD_CHANGE_POLL_SECONDS)
            ownership = detail_screen.query_one(f"#{TASK_DETAIL_OWNERSHIP}", Static)
            await wait_until(
                lambda: Hold.PAUSED.value in _text(ownership),
                timeout=UI_WAIT_SECONDS,
            )
            assert app.focused is bar
            assert bar.active
            await wait_until(filter_visible, timeout=UI_WAIT_SECONDS)

            bar.focus()
            await pilot.wait_for_scheduled_animations()
            await pilot.resize_terminal(36, 20)
            await pilot.wait_for_scheduled_animations()
            await wait_until(filter_visible, timeout=UI_WAIT_SECONDS)
            assert app.focused is bar
            assert bar.active
    finally:
        reader.close()


async def test_task_detail_refreshes_durable_ownership_after_palette_closes(
    connection: Connection,
    database: Database,
    tmp_path: Path,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="Ownership changes behind palette")
    queue = CommandQueue(connection, clock)
    for submitted in queue.pending():
        queue.mark_done(submitted.id)
    reader = StateReader(database, clock)
    try:
        app = _operator_task_app(reader, project_root, task.id, queue, clock)
        async with app.run_test(size=(36, 20)) as pilot:
            dashboard_screen = app.screen
            identity = dashboard_screen.query_one(f"#{TASK_DETAIL_IDENTITY}", Static)
            ownership = dashboard_screen.query_one(f"#{TASK_DETAIL_OWNERSHIP}", Static)
            await wait_until(lambda: task.title in _text(identity), timeout=UI_WAIT_SECONDS)
            await wait_until(clock.has_pending_sleep, timeout=UI_WAIT_SECONDS)

            await pilot.press("ctrl+p")
            await wait_until(
                lambda: isinstance(app.screen, ActionsListScreen), timeout=UI_WAIT_SECONDS
            )
            TaskStore(connection, clock).set_holds(task.id, frozenset({Hold.PAUSED}))
            assert Hold.PAUSED.value not in _text(ownership)

            await pilot.press("escape")
            await wait_until(lambda: app.screen is dashboard_screen, timeout=UI_WAIT_SECONDS)
            await wait_until(
                lambda: Hold.PAUSED.value in _text(ownership),
                timeout=UI_WAIT_SECONDS,
            )
            identity = dashboard_screen.query_one(f"#{TASK_DETAIL_IDENTITY}", Static)
            assert task.id in _text(identity)
            assert task.title in _text(identity)
            assert "queued" in _text(identity).casefold()
            assert Hold.PAUSED.value in _text(ownership)
            await _open_task_view(app, pilot, TASK_VIEW_CONTEXT)
            detail_screen = app.screen
            assert "Project attention" not in _text(
                detail_screen.query_one(f"#{TASK_DETAIL_CONTEXT_INFO}", Static)
            )
            await pilot.press("escape")
            await wait_until(lambda: app.screen is dashboard_screen, timeout=UI_WAIT_SECONDS)
            await _open_task_view(app, pilot, TASK_VIEW_RELATIONSHIPS)
            detail_screen = app.screen
            body = detail_screen.query_one(f"#{TASK_DETAIL_BODY}", VerticalScroll)
            blocker = detail_screen.query_one(f"#{TASK_DETAIL_BLOCKER}", Static)
            body.scroll_to_widget(blocker, animate=False)
            await pilot.pause()
            assert blocker.display
            assert "Pause hold" in _text(blocker)
            assert blocker.region.y >= body.region.y
            assert blocker.region.bottom <= body.region.bottom
    finally:
        reader.close()


async def test_resume_waits_for_every_hand_back_and_keeps_the_other_holds_until_it_runs(
    connection: Connection,
    database: Database,
    tmp_path: Path,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task, runs, tasks, queue, leader, implementer = _seed_owned_task(connection, clock)
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
                timeout=UI_WAIT_SECONDS,
            )

            first_command = await _submit_detail_command(
                app,
                pilot,
                queue,
                "hand-back",
                role=Role.LEADER,
            )
            assert isinstance(first_command.command, HandBack)
            assert first_command.command.role is Role.LEADER
            runs.mark_live(leader.id)
            tasks.set_holds(task.id, frozenset({Hold.PAUSED, Hold.INTERVENTION}))
            queue.mark_done(first_command.id, {"role": Role.LEADER.value})
            await wait_until(
                lambda: (
                    "Recorded operator handoff Implementer" in _text(ownership)
                    and "Leader" not in _text(ownership)
                    and Hold.PAUSED.value in _text(ownership)
                    and Hold.INTERVENTION.value in _text(ownership)
                ),
                timeout=UI_WAIT_SECONDS,
            )
            palette = await _open_action_popup(app, pilot, "resume")
            await wait_until(
                lambda: palette.query_one("#palette-submit", Button).disabled,
                timeout=UI_WAIT_SECONDS,
            )
            assert palette.query_one("#palette-submit", Button).disabled
            assert "hand back all taken-over roles" in _text(
                palette.query_one("#palette-action-state", Static)
            )
            await pilot.press("escape", "escape")
            task_after_partial_hand_back = reader.get_task(task.id)
            assert task_after_partial_hand_back is not None
            assert task_after_partial_hand_back.holds == frozenset({Hold.PAUSED, Hold.INTERVENTION})

            second_command = await _submit_detail_command(
                app,
                pilot,
                queue,
                "hand-back",
                role=Role.IMPLEMENTER,
            )
            assert isinstance(second_command.command, HandBack)
            assert second_command.command.role is Role.IMPLEMENTER
            runs.mark_live(implementer.id)
            queue.mark_done(second_command.id, {"role": Role.IMPLEMENTER.value})
            await wait_until(
                lambda: (
                    "Recorded operator handoff" not in _text(ownership)
                    and Hold.PAUSED.value in _text(ownership)
                    and Hold.INTERVENTION.value in _text(ownership)
                ),
                timeout=UI_WAIT_SECONDS,
            )
            resume_command = await _submit_detail_command(app, pilot, queue, "resume")
            assert isinstance(resume_command.command, Resume)
            assert resume_command.command.task_id == task.id
            task_before_resume = tasks.get(task.id)
            assert task_before_resume is not None
            assert task_before_resume.holds == {Hold.PAUSED, Hold.INTERVENTION}

            tasks.set_holds(task.id, frozenset())
            queue.mark_done(resume_command.id)
            await wait_until(lambda: not _text(ownership), timeout=UI_WAIT_SECONDS)
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
            await wait_until(
                lambda: Hold.INTERVENTION.value in _text(ownership), timeout=UI_WAIT_SECONDS
            )
            palette = await _open_action_popup(app, pilot, "resume")
            await wait_until(
                lambda: not palette.query_one("#palette-submit", Button).disabled,
                timeout=UI_WAIT_SECONDS,
            )
            assert not palette.query_one("#palette-submit", Button).disabled
            await pilot.click("#palette-submit")
            await wait_until(lambda: len(queue.pending()) == 1, timeout=UI_WAIT_SECONDS)
            queued = queue.pending()[0]
            assert isinstance(queued.command, Resume)
            assert queued.command.task_id == task.id
    finally:
        reader.close()


async def test_fixed_task_view_shows_no_project_widgets_and_keeps_workspace_reachable(
    connection: Connection,
    database: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    focused = seed_task(connection, clock, title="Focused task", mode=Mode.IMPLEMENT)
    EscalationStore(connection, clock).open(
        focused.id,
        step=None,
        reason=EscalationReason.DECISION_REQUIRED,
        headline="Attention",
        packet_path="unused.packet",
        options=(),
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
        async with app.run_test(size=(36, 24)) as pilot:
            identity = app.query_one("#task-detail-identity", Static)
            await wait_until(lambda: focused.title in _text(identity), timeout=UI_WAIT_SECONDS)
            assert identity.region.width > 0
            assert focused.id in _text(identity)
            briefing = plain_text(app.query_one(f"#{TASK_DETAIL_BRIEFING}", Static).content)
            assert "Global attention" not in briefing
            assert not app.query("#dialogue-table")
            assert not app.query("#usage")
            assert not app.query("#task-view-extension-panel")
            assert len(app.query("#task-browser-list")) == 0
            await _open_task_view(app, pilot, TASK_VIEW_CONTEXT)
            assert "Project attention" not in _text(
                app.screen.query_one(f"#{TASK_DETAIL_CONTEXT_INFO}", Static)
            )
            await pilot.press("escape")
            await _open_task_view(app, pilot, TASK_VIEW_WORKSPACE)
            assert app.screen.query_one("#task-detail-workspace", Static).region.width > 0
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
        assert CommandQueue(connection, context.clock).pending() == context.pending_before
        await context.tmux.kill_window(context.window_current)
        assert await context.navigation.task_window(task.id) == (
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
        windows_after = await tmux_server.run(
            "list-windows",
            "-t",
            context.session_target,
            "-F",
            TMUX_WINDOW_ID_FORMAT,
        )
        assert windows_after.stdout == windows_before.stdout
    finally:
        reader.close()


@dataclass(frozen=True)
class _SectionCase:
    section: DetailsSection
    view: str
    by_click: bool


_SECTION_CASES = [
    _SectionCase(DetailsSection.REQUEST, TASK_VIEW_REQUEST, True),
    _SectionCase(DetailsSection.RESULTS, TASK_VIEW_RESULTS, False),
    _SectionCase(DetailsSection.DECISIONS, TASK_VIEW_DECISIONS, True),
    _SectionCase(DetailsSection.EVENTS, TASK_VIEW_EVIDENCE, False),
    _SectionCase(DetailsSection.AGENT_ACTIVITY, TASK_VIEW_AGENT, True),
    _SectionCase(DetailsSection.USAGE, TASK_VIEW_USAGE, False),
    _SectionCase(DetailsSection.BLOCKERS, TASK_VIEW_RELATIONSHIPS, True),
    _SectionCase(DetailsSection.WORKSPACE, TASK_VIEW_WORKSPACE, False),
    _SectionCase(DetailsSection.SETTINGS, TASK_VIEW_CONTEXT, False),
]


async def test_each_fold_opens_by_key_or_click_without_creating_commands(
    connection: Connection,
    database: Database,
    tmp_path: Path,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="Fold target", priority=5)
    reader = StateReader(database, SystemClock())
    queue = CommandQueue(connection, clock)
    pending_before = queue.pending()

    try:
        app = _operator_task_app(reader, project_root, task.id, queue, clock)
        async with app.run_test(size=(100, 40)) as pilot:
            await wait_until(
                lambda: task.title in _text(app.query_one(f"#{TASK_DETAIL_IDENTITY}", Static)),
                timeout=UI_WAIT_SECONDS,
            )
            base = app.screen
            panel = app.query_one("#task-detail-panel", TaskDetailPanel)
            for case in (
                *reversed([c for c in _SECTION_CASES if c.by_click]),
                *[c for c in _SECTION_CASES if not c.by_click],
            ):
                await _open_fold(app, pilot, panel, case)
                assert app.screen is base
            assert queue.pending() == pending_before
    finally:
        reader.close()


async def _open_fold(
    app: DashboardApp, pilot: Pilot[None], panel: TaskDetailPanel, case: _SectionCase
) -> None:
    if case.by_click:
        header = (
            panel.query_one(DetailFolds).all_folds[list(DetailsSection).index(case.section)].header
        )
        header.scroll_visible(animate=False, immediate=True)
        await wait_until(
            lambda: (
                header.region in app.screen.region
                and app.get_widget_at(header.region.x, header.region.y)[0] is header
            ),
            timeout=UI_WAIT_SECONDS,
        )
        await pilot.click(header)
    else:
        await choose_details_entry(pilot, case.view)
    await wait_until(lambda: case.view in panel.open_views, timeout=UI_WAIT_SECONDS)


async def test_escape_steps_from_home_inspector_fold_list_to_header_to_cleared_selection(
    connection: Connection,
    database: Database,
    tmp_path: Path,
) -> None:
    project_root = write_minimal_project(tmp_path)
    task = seed_task(connection, SystemClock(), title="Escape levels target")
    reader = StateReader(database, SystemClock())
    try:
        app = _dashboard_app(
            reader, project_root, focus_task=None, widget_names=TASK_DETAIL_WIDGETS
        )
        async with app.run_test(size=(120, 40)) as pilot:
            base = await _open_escape_base(app, pilot, task.id)
            panel = app.query_one("#task-detail-panel", TaskDetailPanel)
            await choose_details_entry(pilot, TASK_VIEW_EVIDENCE)
            await wait_until(
                lambda: isinstance(app.focused, RowList) and panel in app.focused.ancestors,
                timeout=UI_WAIT_SECONDS,
            )
            await pilot.press("escape")
            await wait_until(lambda: isinstance(app.focused, FoldHeader), timeout=UI_WAIT_SECONDS)
            assert app.screen is base
            assert TASK_VIEW_EVIDENCE in panel.open_views

            await pilot.press("escape")
            await wait_until(
                lambda: task.id not in _text(app.query_one(f"#{TASK_DETAIL_IDENTITY}", Static)),
                timeout=UI_WAIT_SECONDS,
            )
            assert app.screen is base
    finally:
        reader.close()


async def _open_escape_base(app: DashboardApp, pilot: Pilot[None], task_id: str) -> Screen[object]:
    row = f"#task-row-{task_id}"
    await wait_until(lambda: bool(app.query(row)), timeout=UI_WAIT_SECONDS)
    task_list = cast("ListView", app.query_one(row).parent)
    task_list.focus()
    task_list.index = tuple(task_list.children).index(app.query_one(row))
    await wait_until(
        lambda: task_id in _text(app.query_one(f"#{TASK_DETAIL_IDENTITY}", Static)),
        timeout=UI_WAIT_SECONDS,
    )
    await focus_inspector_details(pilot)
    return app.screen
