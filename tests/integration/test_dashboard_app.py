from __future__ import annotations

import errno
import json
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from rich.cells import cell_len
from textual.containers import Horizontal, VerticalScroll
from textual.geometry import Region
from textual.widgets import (
    Button,
    DataTable,
    Input,
    Label,
    ListItem,
    ListView,
    Select,
    Static,
    TextArea,
)

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
    role_dir,
)
from shikiru.clock import SystemClock
from shikiru.config.loader import load_settings
from shikiru.dashboard.app import DashboardApp, WidgetPluginError
from shikiru.dashboard.contract import (
    WIDGET_ENTRY_POINT_GROUP,
    DiagnosticSampleState,
    PaletteRequest,
    ResourceView,
)
from shikiru.dashboard.detail import (
    TASK_DETAIL_BLOCKER,
    TASK_DETAIL_CRITERIA,
    TASK_DETAIL_EVIDENCE,
    TASK_DETAIL_EVIDENCE_INLINE_BUTTON,
    TASK_DETAIL_FACTS,
    TASK_DETAIL_IDENTITY,
    TASK_DETAIL_OWNERSHIP,
    TASK_DETAIL_PROGRESS,
    TASK_DETAIL_REPORT,
    TASK_DETAIL_REQUEST,
    TASK_DETAIL_SESSIONS,
    TASK_DETAIL_SIGNAL,
    TASK_DETAIL_SITUATION,
    TASK_DETAIL_TIMELINE_ENTRY,
    TASK_DETAIL_TIMELINE_TITLE,
    TASK_DETAIL_USAGE_INLINE_BUTTON,
    TASK_DETAIL_VERDICT,
    TASK_DETAIL_WORKSPACE,
    TASK_VIEW_EVIDENCE,
    TASK_VIEW_SUMMARY,
    TASK_VIEW_USAGE,
    TaskDetailPanel,
    TaskDetailScreen,
)
from shikiru.dashboard.palette import PaletteScreen
from shikiru.domain.commands import Prioritize, Stop, Submit
from shikiru.domain.escalation import EscalationStatus, options_for
from shikiru.domain.events import EventKind
from shikiru.domain.reports import (
    CURRENT_TURN_LINK_NAME,
    REPORT_FILENAME,
    REPORT_KIND_FILENAME,
    ImplementationReport,
    ReportKind,
    TriageReport,
)
from shikiru.domain.reports import (
    TestRun as AgentTestRun,
)
from shikiru.domain.scheduling import WaitReason
from shikiru.domain.steps import ACTING_ROLE, Step, Transition, first_step, next_step
from shikiru.domain.tasks import EscalationReason, Hold, Mode, Role, Status, Task
from shikiru.operator.commands import OperatorCommands
from shikiru.operator.queries import OperatorQueries
from shikiru.paths import run_dir, turns_dir
from shikiru.plugins.widgets.escalations import EscalationsPanel
from shikiru.plugins.widgets.strips import StripsPanel
from shikiru.store.commands import CommandQueue
from shikiru.store.escalations import EscalationStore
from shikiru.store.events import EventLog
from shikiru.store.reader import StateReader
from shikiru.store.runs import RunStore
from shikiru.store.tasks import TaskStore
from shikiru.telemetry.resources import ResourceSampler
from shikiru.view_log import view_log_path
from tests.support.clock import FakeClock
from tests.support.config import write_minimal_project
from tests.support.dashboard import StubServices, ignore_palette_action, state_views
from tests.support.entry_points import FakeDistribution, ambiguous_entry_points
from tests.support.render import plain_text
from tests.support.store import seed_task
from tests.support.waiting import wait_until

_TEST_START = datetime(2026, 1, 1, tzinfo=UTC)
_APP_SHUTDOWN_TIMEOUT_SECONDS = 2
_DASHBOARD_WAIT_TIMEOUT_SECONDS = 2
COMMAND_HISTORY_TEST_LIMIT = 10
_EXTERNAL_WIDGET_NAME = "outside-widget"
_WIDE_HOME_SIZE = (120, 40)
_STANDARD_HOME_SIZE = (100, 35)
_COMPACT_HOME_SIZE = (48, 20)
_NARROW_TASK_SIZE = (36, 20)
_COMPACT_TASK_SIZES = (_COMPACT_HOME_SIZE, _NARROW_TASK_SIZE)
_TINY_HOME_SIZE = (20, 8)
_HOME_REVIEW_SIZES = (_STANDARD_HOME_SIZE, _WIDE_HOME_SIZE, (80, 24), _COMPACT_HOME_SIZE)
_MIN_WIDE_TASK_LIST_ROWS = 9
_API_INPUT_TOKENS = 1_000
_API_OUTPUT_TOKENS = 500
_SUBSCRIPTION_INPUT_TOKENS = 400
_SUBSCRIPTION_OUTPUT_TOKENS = 200
_UNREPORTED_INPUT_TOKENS = 200
_UNREPORTED_OUTPUT_TOKENS = 100
_ESTIMATED_INPUT_TOKENS = 200
_ESTIMATED_OUTPUT_TOKENS = 100
_API_BILLED_COST_USD = 0.42
_API_EQUIVALENT_ESTIMATE_USD = 0.11
_TASK99_TRIAGE_DURATION_SECONDS = 30
_TASK99_IMPLEMENT_DURATION_SECONDS = 90
_TASK99_VERIFY_DURATION_SECONDS = 180
_TASK99_SIGNAL_AGE_SECONDS = 59
_TASK99_AGE_BOUNDARY_ADVANCE_SECONDS = 1
_TASK99_TIMELINE_REFRESH_SECONDS = 1
_TASK99_API_INPUT_TOKENS = 1_731
_TASK99_API_OUTPUT_TOKENS = 284
_TASK99_API_CACHE_READ_TOKENS = 91
_TASK99_API_CACHE_WRITE_TOKENS = 17
_TASK99_API_COST_USD = 0.37
_TASK99_SUBSCRIPTION_INPUT_TOKENS = 683
_TASK99_SUBSCRIPTION_OUTPUT_TOKENS = 149
_TASK99_SUBSCRIPTION_CACHE_READ_TOKENS = 37
_TASK99_SUBSCRIPTION_CACHE_WRITE_TOKENS = 8
_TASK99_SELECTED_TITLE = "Verify compact task"
_TASK99_LONG_TITLE = "A deliberately long recorded task title that wraps on compact Home"
_TASK99_SIGNAL_ROW = "ToolStarted"
_TASK99_SIGNAL_NAME = "pytest"
_TASK99_CURRENT_TURN = "03-verify-i1"
_TASK99_IMPLEMENT_TURN = "02-implement-i1"
_TASK99_TRIAGE_TURN = "01-triage-i0"
_TASK99_REVIEW_DIR = Path("build/review/task99")


class _ExternalWidget:
    name = _EXTERNAL_WIDGET_NAME

    def create(self, context: WidgetContext) -> Widget:
        del context
        return Static(id="external-widget-panel")


class _ExternalEntryPoint:
    name = _EXTERNAL_WIDGET_NAME
    dist = FakeDistribution("test-extension")

    def load(self) -> type[_ExternalWidget]:
        return _ExternalWidget


class _InvalidPlacementWidget:
    name = "invalid-placement-widget"
    placement = "unmapped"

    def create(self, context: WidgetContext) -> Widget:
        del context
        return Static(id="invalid-placement-widget-panel")


class _InvalidPlacementEntryPoint:
    name = _InvalidPlacementWidget.name
    dist = FakeDistribution("invalid-placement-extension")

    def load(self) -> type[_InvalidPlacementWidget]:
        return _InvalidPlacementWidget


def _rendered_text(widget: Static) -> str:
    return cast("Text", widget.render()).plain


def _rendered_widget_lines(widget: Static) -> tuple[str, ...]:
    lines = widget.render_lines(Region(0, 0, widget.region.width, widget.region.height))
    return tuple(line.text.rstrip() for line in lines if line.text.strip())


def _resource_groups_are_right_aligned(widget: Static) -> bool:
    lines = tuple(line for line in _rendered_widget_lines(widget) if "Shikiru + agents" in line)
    if len(lines) != 1:
        return False
    line = lines[0]
    instance_start = line.find("Shikiru + agents")
    if instance_start < 0:
        return False
    return cell_len(line[:instance_start]) + cell_len(line[instance_start:]) == widget.region.width


_TASK_DETAIL_SECTION_IDS = (
    TASK_DETAIL_OWNERSHIP,
    TASK_DETAIL_SITUATION,
    TASK_DETAIL_BLOCKER,
    TASK_DETAIL_PROGRESS,
    TASK_DETAIL_REQUEST,
    TASK_DETAIL_CRITERIA,
    TASK_DETAIL_REPORT,
    TASK_DETAIL_VERDICT,
    TASK_DETAIL_EVIDENCE,
    TASK_DETAIL_FACTS,
    TASK_DETAIL_WORKSPACE,
    TASK_DETAIL_SESSIONS,
)


def _rendered_task_detail_sections(app: DashboardApp) -> tuple[str, ...]:
    return tuple(
        plain_text(app.query_one(f"#{section_id}", Static).render())
        for section_id in _TASK_DETAIL_SECTION_IDS
    )


def _task99_widget_text(widget: Static) -> str:
    return "\n".join(_rendered_widget_lines(widget))


def _seed_paused_escalated_task(connection: Connection) -> str:
    clock = SystemClock()
    task = seed_task(connection, clock, title="Paused dashboard task", mode=Mode.IMPLEMENT)
    tasks = TaskStore(connection, clock)
    tasks.apply_transition(
        task.id,
        Transition.stay(task, step=None, status=Status.ESCALATED),
    )
    tasks.set_holds(task.id, frozenset({Hold.PAUSED}))
    return task.id


def _append_task99_stage_start(events: EventLog, task: Task, step: Step) -> None:
    events.append(
        EventKind.STEP_STARTED,
        task_id=task.id,
        message=f"{step.value} stage started",
        data={
            "step": step.value,
            "role": ACTING_ROLE[step].value,
            "iteration": task.iteration,
        },
    )


def _write_task99_report_turn(
    project_dir: Path,
    task_id: str,
    turn: str,
    kind: ReportKind,
    report: TriageReport | ImplementationReport,
) -> Path:
    turn_dir = turns_dir(project_dir, task_id) / turn
    turn_dir.mkdir(parents=True, exist_ok=True)
    (turn_dir / REPORT_KIND_FILENAME).write_text(kind.value, encoding="utf-8")
    (turn_dir / REPORT_FILENAME).write_text(report.model_dump_json(), encoding="utf-8")
    return turn_dir


def _write_task99_signal(
    project_dir: Path,
    task_id: str,
    role: Role,
    *,
    turn: str,
    observed_at: datetime,
    signal: str,
    name: str,
) -> None:
    path = view_log_path(run_dir(project_dir, task_id), role.value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"ts": observed_at.isoformat(), "turn": turn, "signal": signal, "name": name})
        + "\n",
        encoding="utf-8",
    )


def _record_task99_triage(
    connection: Connection,
    project_dir: Path,
    clock: FakeClock,
    settings: Settings,
) -> Task:
    tasks = TaskStore(connection, clock)
    events = EventLog(connection, clock)
    task = seed_task(connection, clock, title=_TASK99_SELECTED_TITLE)
    events.append(EventKind.TASK_QUEUED, task_id=task.id, message="Task submitted")
    tasks.apply_transition(task.id, first_step(task))
    _append_task99_stage_start(events, task, Step.TRIAGE)
    triage = TriageReport(
        title=task.title,
        mode=Mode.IMPLEMENT,
        complexity="normal",
        acceptance_criteria=["Recorded implementation passes verification"],
        references=[],
    )
    _write_task99_report_turn(project_dir, task.id, _TASK99_TRIAGE_TURN, ReportKind.TRIAGE, triage)

    clock.advance(_TASK99_TRIAGE_DURATION_SECONDS)
    task = tasks.require(task.id)
    tasks.apply_transition(task.id, next_step(task, triage, settings))
    return tasks.require(task.id)


def _record_task99_implementation(
    connection: Connection,
    project_dir: Path,
    clock: FakeClock,
    settings: Settings,
    task: Task,
) -> tuple[Task, ImplementationReport]:
    tasks = TaskStore(connection, clock)
    events = EventLog(connection, clock)
    runs = RunStore(connection, clock)
    _append_task99_stage_start(events, task, Step.IMPLEMENT)
    implementer = runs.start(
        task.id,
        Role.IMPLEMENTER,
        Rung("codex", "gpt-6-luna", "low"),
        billing_declaration=BillingDeclaration(
            BILLING_MODE_API,
            BILLING_SOURCE_OPERATOR_CONFIG,
        ),
    )
    implementer_turn_data = {
        "role": Role.IMPLEMENTER.value,
        "agent": "codex",
        "model": "gpt-6-luna",
        "effort": "low",
        "run_id": implementer.id,
        "turn": _TASK99_IMPLEMENT_TURN,
        "step": Step.IMPLEMENT.value,
        "iteration": task.iteration,
    }
    events.append(
        EventKind.TURN_STARTED,
        task_id=task.id,
        message="Implementer started recorded implementation",
        data=implementer_turn_data,
    )
    runs.record_usage(
        implementer.id,
        Usage(
            _TASK99_API_INPUT_TOKENS,
            _TASK99_API_OUTPUT_TOKENS,
            _TASK99_API_CACHE_READ_TOKENS,
            _TASK99_API_CACHE_WRITE_TOKENS,
            _TASK99_API_COST_USD,
            COST_BASIS_BILLED,
            None,
        ),
    )
    implementer_report = ImplementationReport(
        status="completed",
        summary="Checks pass; reviewing the recorded report.",
        commits=[],
        tests=[AgentTestRun(name="focused checks", result="passed")],
        trackers_updated=False,
    )
    _write_task99_report_turn(
        project_dir,
        task.id,
        _TASK99_IMPLEMENT_TURN,
        ReportKind.IMPLEMENTATION,
        implementer_report,
    )

    clock.advance(_TASK99_IMPLEMENT_DURATION_SECONDS)
    events.append(
        EventKind.TURN_COMPLETED,
        task_id=task.id,
        message="Implementer completed the recorded implementation",
        data={
            **implementer_turn_data,
            "usage_delta": {
                "input_tokens": _TASK99_API_INPUT_TOKENS,
                "output_tokens": _TASK99_API_OUTPUT_TOKENS,
                "cache_read_tokens": _TASK99_API_CACHE_READ_TOKENS,
                "cache_write_tokens": _TASK99_API_CACHE_WRITE_TOKENS,
                "cost_usd": _TASK99_API_COST_USD,
            },
        },
    )
    runs.close(implementer.id)
    task = tasks.require(task.id)
    tasks.apply_transition(task.id, next_step(task, implementer_report, settings))
    return tasks.require(task.id), implementer_report


def _record_task99_verification(
    connection: Connection,
    project_dir: Path,
    clock: FakeClock,
    task: Task,
) -> None:
    events = EventLog(connection, clock)
    runs = RunStore(connection, clock)
    _append_task99_stage_start(events, task, Step.VERIFY)
    verify_turn = turns_dir(project_dir, task.id) / _TASK99_CURRENT_TURN
    verify_turn.mkdir(parents=True)
    leader = runs.start(
        task.id,
        Role.LEADER,
        Rung("claude", "sonnet", "medium"),
        billing_declaration=BillingDeclaration(
            BILLING_MODE_SUBSCRIPTION,
            BILLING_SOURCE_OPERATOR_CONFIG,
        ),
    )
    runs.set_session(leader.id, "task99-current-leader-session", "resume task99")
    runs.record_usage(
        leader.id,
        Usage(
            _TASK99_SUBSCRIPTION_INPUT_TOKENS,
            _TASK99_SUBSCRIPTION_OUTPUT_TOKENS,
            _TASK99_SUBSCRIPTION_CACHE_READ_TOKENS,
            _TASK99_SUBSCRIPTION_CACHE_WRITE_TOKENS,
            None,
            COST_BASIS_UNKNOWN,
            None,
        ),
    )
    events.append(
        EventKind.TURN_STARTED,
        task_id=task.id,
        message="Leader started verification",
        data={
            "role": Role.LEADER.value,
            "agent": "claude",
            "model": "sonnet",
            "effort": "medium",
            "run_id": leader.id,
            "turn": _TASK99_CURRENT_TURN,
            "step": Step.VERIFY.value,
            "iteration": task.iteration,
        },
    )
    _write_task99_signal(
        project_dir,
        task.id,
        Role.IMPLEMENTER,
        turn=_TASK99_IMPLEMENT_TURN,
        observed_at=clock.now() - timedelta(seconds=1),
        signal="ToolFinished",
        name="pytest",
    )
    leader_role_dir = role_dir(run_dir(project_dir, task.id), Role.LEADER.value)
    leader_role_dir.mkdir(parents=True, exist_ok=True)
    (leader_role_dir / CURRENT_TURN_LINK_NAME).symlink_to(verify_turn, target_is_directory=True)

    clock.advance(_TASK99_VERIFY_DURATION_SECONDS - _TASK99_SIGNAL_AGE_SECONDS)
    _write_task99_signal(
        project_dir,
        task.id,
        Role.LEADER,
        turn=_TASK99_CURRENT_TURN,
        observed_at=clock.now(),
        signal=_TASK99_SIGNAL_ROW,
        name=_TASK99_SIGNAL_NAME,
    )
    clock.advance(_TASK99_SIGNAL_AGE_SECONDS)


def _record_task99_other_tasks(
    connection: Connection,
    clock: FakeClock,
    verifying_task_id: str,
) -> tuple[str, str]:
    tasks = TaskStore(connection, clock)
    runs = RunStore(connection, clock)
    events = EventLog(connection, clock)
    decision = seed_task(
        connection,
        clock,
        title="Choose recovery",
        mode=Mode.IMPLEMENT,
    )
    decision = tasks.require(decision.id)
    tasks.apply_transition(
        decision.id,
        Transition.stay(decision, step=Step.VERIFY, status=Status.ESCALATED),
    )
    EscalationStore(connection, clock).open(
        decision.id,
        step=Step.VERIFY,
        reason=EscalationReason.DECISION_REQUIRED,
        headline="Choose whether the task should continue",
        packet_path=".shikiru/escalations/task99-decision.md",
        options=options_for(EscalationReason.DECISION_REQUIRED),
    )

    waiting = seed_task(connection, clock, title="Wait dependency")
    tasks.set_depends_on(waiting.id, (verifying_task_id,))
    tasks.set_wait_reason(waiting.id, WaitReason.DEPENDENCY)
    paused = seed_task(connection, clock, title="Paused follow-up")
    tasks.set_holds(paused.id, frozenset({Hold.PAUSED}))
    owned = seed_task(connection, clock, title="Owned follow-up")
    tasks.apply_transition(owned.id, first_step(owned))
    owned_run = runs.start(owned.id, Role.LEADER, Rung("claude", "sonnet", "medium"))
    runs.mark_taken_over(owned_run.id)
    tasks.set_holds(owned.id, frozenset({Hold.INTERVENTION}))
    seed_task(connection, clock, title="Queued follow-up")
    recent = seed_task(connection, clock, title="Recent Done")
    tasks.apply_transition(
        recent.id,
        Transition.stay(recent, step=None, status=Status.DONE),
    )
    events.append(EventKind.TASK_DONE, task_id=recent.id, message="Task completed")
    return decision.id, recent.id


def _record_task99_population(
    connection: Connection,
    project_dir: Path,
    clock: FakeClock,
    settings: Settings,
) -> tuple[str, str, str]:
    task = _record_task99_triage(connection, project_dir, clock, settings)
    task, _ = _record_task99_implementation(connection, project_dir, clock, settings, task)
    _record_task99_verification(connection, project_dir, clock, task)
    decision_id, recent_id = _record_task99_other_tasks(connection, clock, task.id)
    queue = CommandQueue(connection, clock)
    for queued in queue.pending():
        assert isinstance(queued.command, Submit)
        assert queued.command.task_id is not None
        queue.mark_done(queued.id, {"task_id": queued.command.task_id})
    return task.id, decision_id, recent_id


def _capture_task99(app: DashboardApp, name: str) -> Path:
    _TASK99_REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    output_kind = (
        "color"
        if os.environ.get("TERM") == "xterm-256color" and "NO_COLOR" not in os.environ
        else "mono"
    )
    path = _TASK99_REVIEW_DIR / name.replace("-color.svg", f"-{output_kind}.svg")
    path.write_text(app.export_screenshot(), encoding="utf-8")
    return path


def _task99_settings(project_dir: Path) -> Settings:
    settings = load_settings(project_dir)
    dashboard = settings.dashboard.model_copy(
        update={
            "animations": False,
            "timeline_refresh_seconds": _TASK99_TIMELINE_REFRESH_SECONDS,
        }
    )
    return settings.model_copy(update={"dashboard": dashboard})


async def _select_home_task(app: DashboardApp, task_id: str) -> None:
    task_list = app.query_one("#task-browser-list", ListView)
    row = app.query_one(f"#task-row-{task_id}", ListItem)
    index = tuple(task_list.children).index(row)
    task_list.focus()
    task_list.index = index
    await wait_until(
        lambda: (
            task_list.highlighted_child is row
            and task_id in _task99_widget_text(app.query_one(f"#{TASK_DETAIL_IDENTITY}", Static))
        ),
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )


async def _capture_task99_home_size(
    reader: StateReader,
    *,
    project_dir: Path,
    clock: FakeClock,
    settings: Settings,
    selected_task_id: str,
    decision_task_id: str,
    size: tuple[int, int],
) -> None:
    widget_names = ["header", "queue", "escalations", "usage", "resources"]
    app = _app(
        reader,
        project_dir,
        widget_names=widget_names,
        services=StubServices(engine_running=False),
        clock=clock,
        settings=settings,
    )
    async with app.run_test(size=size):
        await wait_until(
            lambda: bool(app.query(f"#task-row-{selected_task_id}")),
            timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
        )
        await _select_home_task(app, selected_task_id)
        identity = app.query_one(f"#{TASK_DETAIL_IDENTITY}", Static)
        progress = app.query_one(f"#{TASK_DETAIL_PROGRESS}", Static)
        signal = app.query_one(f"#{TASK_DETAIL_SIGNAL}", Static)
        sessions = app.query_one(f"#{TASK_DETAIL_SESSIONS}", Static)
        await wait_until(
            lambda: _TASK99_SELECTED_TITLE in _task99_widget_text(identity),
            timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
        )
        assert "Verify" in _task99_widget_text(progress), _task99_widget_text(progress)
        assert "Verifying" in _task99_widget_text(identity), _task99_widget_text(identity)
        assert _TASK99_SIGNAL_ROW in _task99_widget_text(signal), _task99_widget_text(signal)
        assert f"{Role.LEADER.value}/{_TASK99_CURRENT_TURN}" in _task99_widget_text(signal)
        assert "Leader" in _task99_widget_text(sessions), _task99_widget_text(sessions)
        detail_text = "\n".join(
            _task99_widget_text(app.query_one(f"#{identifier}", Static))
            for identifier in (
                TASK_DETAIL_IDENTITY,
                TASK_DETAIL_PROGRESS,
                TASK_DETAIL_SITUATION,
                TASK_DETAIL_SIGNAL,
                "task-detail-task-usage",
                TASK_DETAIL_SESSIONS,
            )
        )
        assert selected_task_id in detail_text
        assert "Implementer" in detail_text
        assert "Used" in detail_text
        assert "Current" in detail_text
        assert "gpt-6-luna" in detail_text
        assert "sonnet" in detail_text
        attention = app.query_one("#escalations", EscalationsPanel)
        attention_row = attention.query_one("#escalation-panels", VerticalScroll).query_one(Static)
        assert decision_task_id in _task99_widget_text(attention_row)
        assert _TASK99_SELECTED_TITLE in _task99_widget_text(identity)
        _capture_task99(app, f"home-selected-{size[0]}x{size[1]}-color.svg")


async def _capture_task99_compact_home_route(
    reader: StateReader,
    *,
    project_dir: Path,
    clock: FakeClock,
    settings: Settings,
    selected_task_id: str,
    size: tuple[int, int],
) -> None:
    app = _app(
        reader,
        project_dir,
        widget_names=["header", "queue", "escalations", "usage", "resources"],
        services=StubServices(engine_running=False),
        clock=clock,
        settings=settings,
    )
    async with app.run_test(size=size) as pilot:
        await wait_until(
            lambda: bool(app.query(f"#task-row-{selected_task_id}")),
            timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
        )
        task_list = app.query_one("#task-browser-list", ListView)
        row = app.query_one(f"#task-row-{selected_task_id}", ListItem)
        task_list.focus()
        task_list.index = tuple(task_list.children).index(row)
        await wait_until(
            lambda: task_list.highlighted_child is row,
            timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
        )
        _capture_task99(app, f"home-list-selected-{size[0]}x{size[1]}-color.svg")
        home_screen = app.screen
        await pilot.press("enter")
        await wait_until(
            lambda: isinstance(app.screen, TaskDetailScreen),
            timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
        )
        detail = app.screen.query_one("#task-detail-panel", TaskDetailPanel)
        identity = app.screen.query_one(f"#{TASK_DETAIL_IDENTITY}", Static)
        progress = app.screen.query_one(f"#{TASK_DETAIL_PROGRESS}", Static)
        signal = app.screen.query_one(f"#{TASK_DETAIL_SIGNAL}", Static)
        expected_identity = "Verify" if size == _NARROW_TASK_SIZE else _TASK99_SELECTED_TITLE
        await wait_until(
            lambda: (
                expected_identity in _task99_widget_text(identity)
                and "Verify" in _task99_widget_text(progress)
                and _TASK99_SIGNAL_ROW in _task99_widget_text(signal)
            ),
            timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
        )
        assert detail.active_view == TASK_VIEW_SUMMARY
        _capture_task99(app, f"home-enter-detail-{size[0]}x{size[1]}-color.svg")
        await pilot.press("escape")
        await wait_until(
            lambda: app.screen is home_screen,
            timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
        )


async def _capture_task99_detail_size(
    reader: StateReader,
    *,
    project_dir: Path,
    clock: FakeClock,
    settings: Settings,
    selected_task_id: str,
    size: tuple[int, int],
) -> None:
    screen_clock = FakeClock(clock.now())
    app = _app(
        reader,
        project_dir,
        focus_task=selected_task_id,
        widget_names=["header", "queue", "escalations", "usage", "resources"],
        services=StubServices(engine_running=False),
        clock=screen_clock,
        settings=settings,
    )
    async with app.run_test(size=size) as pilot:
        panel = app.query_one("#task-detail-panel", TaskDetailPanel)
        identity = app.query_one(f"#{TASK_DETAIL_IDENTITY}", Static)
        expected_identity = "Verify" if size == _NARROW_TASK_SIZE else _TASK99_SELECTED_TITLE
        await wait_until(
            lambda: expected_identity in _task99_widget_text(identity),
            timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
        )
        _capture_task99(app, f"dash-task-{size[0]}x{size[1]}-color.svg")
        if size in _COMPACT_TASK_SIZES:
            await _assert_task99_live_age_and_routes(
                app,
                pilot,
                panel,
                screen_clock,
                size=size,
            )


async def _assert_task99_live_age_and_routes(
    app: DashboardApp,
    pilot: Pilot[None],
    panel: TaskDetailPanel,
    clock: FakeClock,
    *,
    size: tuple[int, int],
) -> None:
    signal = app.query_one(f"#{TASK_DETAIL_SIGNAL}", Static)
    await wait_until(
        lambda: "59s ago" in _task99_widget_text(signal),
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    if size == _COMPACT_HOME_SIZE:
        await wait_until(clock.has_pending_sleep, timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS)
        clock.advance(_TASK99_AGE_BOUNDARY_ADVANCE_SECONDS)
        await wait_until(
            lambda: "1m ago" in _task99_widget_text(signal),
            timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
        )

    evidence_button = panel.query_one(f"#{TASK_DETAIL_EVIDENCE_INLINE_BUTTON}", Button)
    evidence_button.focus()
    await wait_until(
        lambda: evidence_button.region.bottom <= app.size.height,
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    await pilot.press("enter")
    await wait_until(
        lambda: (
            panel.active_view == TASK_VIEW_EVIDENCE
            and "Evidence"
            in _task99_widget_text(app.query_one(f"#{TASK_DETAIL_TIMELINE_TITLE}", Static))
        ),
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    timeline_entry = cast("Select[str]", app.query_one(f"#{TASK_DETAIL_TIMELINE_ENTRY}", Select))
    await wait_until(
        lambda: timeline_entry.value != Select.BLANK,
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    _capture_task99(app, f"task-evidence-{size[0]}x{size[1]}-color.svg")
    await pilot.press("escape")
    await wait_until(
        lambda: panel.active_view == TASK_VIEW_SUMMARY,
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    usage_button = panel.query_one(f"#{TASK_DETAIL_USAGE_INLINE_BUTTON}", Button)
    usage_button.focus()
    await wait_until(
        lambda: usage_button.region.bottom <= app.size.height,
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    await pilot.press("enter")
    await wait_until(
        lambda: (
            panel.active_view == TASK_VIEW_USAGE
            and "API" in _task99_widget_text(app.query_one(f"#{TASK_DETAIL_SESSIONS}", Static))
        ),
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    _capture_task99(app, f"task-usage-{size[0]}x{size[1]}-color.svg")


async def test_task99_native_compact_inspector_matches_recorded_workflow(
    database: Database,
    connection: Connection,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(_TEST_START)
    settings = _task99_settings(project_dir)
    selected_task_id, decision_task_id, recent_task_id = _record_task99_population(
        connection,
        project_dir,
        clock,
        settings,
    )
    reader = StateReader(database, clock)
    try:
        for size in (_STANDARD_HOME_SIZE, _WIDE_HOME_SIZE):
            await _capture_task99_home_size(
                reader,
                project_dir=project_dir,
                clock=clock,
                settings=settings,
                selected_task_id=selected_task_id,
                decision_task_id=decision_task_id,
                size=size,
            )
        for size in _COMPACT_TASK_SIZES:
            await _capture_task99_compact_home_route(
                reader,
                project_dir=project_dir,
                clock=clock,
                settings=settings,
                selected_task_id=selected_task_id,
                size=size,
            )
        for size in (_STANDARD_HOME_SIZE, _WIDE_HOME_SIZE, *_COMPACT_TASK_SIZES):
            await _capture_task99_detail_size(
                reader,
                project_dir=project_dir,
                clock=clock,
                settings=settings,
                selected_task_id=selected_task_id,
                size=size,
            )
        recent = TaskStore(connection, clock).require(recent_task_id)
        TaskStore(connection, clock).apply_transition(
            recent.id,
            replace(
                Transition.stay(recent, step=None, status=Status.DONE),
                title=_TASK99_LONG_TITLE,
            ),
        )
        wrap_app = _app(
            reader,
            project_dir,
            widget_names=["header", "queue", "escalations", "usage", "resources"],
            services=StubServices(engine_running=False),
            clock=clock,
            settings=settings,
        )
        async with wrap_app.run_test(size=_STANDARD_HOME_SIZE):
            await wait_until(
                lambda: bool(wrap_app.query(f"#task-row-{recent_task_id}")),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            await _select_home_task(wrap_app, recent_task_id)
            _capture_task99(wrap_app, "home-long-title-wrap-100x35-color.svg")
    finally:
        reader.close()


if TYPE_CHECKING:
    from rich.text import Text
    from textual.pilot import Pilot
    from textual.widget import Widget

    from shikiru.clock import Clock
    from shikiru.config.settings import Settings
    from shikiru.dashboard.contract import OpenPalette, OpenPaletteContext, WidgetContext
    from shikiru.store.database import Database
    from tests.support.store import Connection


async def _unexpected_pane_selection(pane_id: str) -> None:
    del pane_id
    raise AssertionError


def _palette_screen_for_app(
    reader: StateReader,
    connection: Connection,
    project_dir: Path,
    clock: Clock,
    request: PaletteRequest | None = None,
) -> PaletteScreen:
    settings = load_settings(project_dir)
    services = StubServices()
    queue = CommandQueue(connection, clock)
    commands = OperatorCommands(queue, clock, engine_up=services.engine_up)
    queries = OperatorQueries(
        reader,
        project=settings.project.name,
        project_root=project_dir,
        engine_up=services.engine_up,
        max_parallel=settings.orchestration.max_parallel,
        clock=clock,
    )
    return PaletteScreen(
        commands,
        queries,
        _unexpected_pane_selection,
        on_change=reader.watch,
        initial_context=request,
    )


def _app(
    reader: StateReader,
    project_dir: Path,
    *,
    widget_names: list[str],
    services: StubServices | None = None,
    open_palette: OpenPalette = ignore_palette_action,
    open_palette_context: OpenPaletteContext | None = None,
    focus_task: str | None = None,
    clock: Clock | None = None,
    settings: Settings | None = None,
) -> DashboardApp:
    settings = settings or load_settings(project_dir)
    clock = clock or SystemClock()
    return DashboardApp(
        focus_task=focus_task,
        views=state_views(
            reader,
            project_dir,
            clock,
            services or StubServices(),
            focus_task=focus_task,
            settings=settings,
        ),
        on_change=reader.watch,
        clock=clock,
        motion_interval_seconds=(
            _motion_interval(project_dir) if settings.dashboard.animations else None
        ),
        resources_refresh_seconds=settings.orchestration.resources.sample_seconds,
        docker_refresh_seconds=settings.dashboard.docker_refresh_seconds,
        open_palette=open_palette,
        widget_names=widget_names,
        open_palette_context=open_palette_context,
        timeline_refresh_seconds=settings.dashboard.timeline_refresh_seconds,
    )


def _motion_interval(project_dir: Path) -> float:
    dashboard = load_settings(project_dir).dashboard
    return 1 / dashboard.motion.refresh_hz


async def _choose_focus_view(
    app: DashboardApp,
    pilot: Pilot[None],
    label: str,
    *,
    mouse: bool = False,
) -> None:
    if not app.screen.query("#focus-views-list"):
        await pilot.press("v")
        await wait_until(lambda: bool(app.screen.query("#focus-views-list")), timeout=2)
    views_screen = app.screen
    view_list = app.screen.query_one("#focus-views-list", ListView)
    items = [item for item in view_list.children if isinstance(item, ListItem)]
    selected = next(
        index
        for index, item in enumerate(items)
        if plain_text(item.query_one(Label).render()) == label
    )
    if mouse:
        await pilot.click(items[selected])
    else:
        view_list.focus()
        view_list.index = selected
        await pilot.press("enter")
    await wait_until(lambda: app.screen is not views_screen, timeout=2)


def _open_report_writer(writer_descriptors: dict[Path, int], report_path: Path) -> bool:
    if report_path in writer_descriptors:
        return True
    try:
        writer_descriptors[report_path] = os.open(report_path, os.O_WRONLY | os.O_NONBLOCK)
    except OSError as error:
        if error.errno == errno.ENXIO:
            return False
        raise
    return True


def _finish_report_read(
    report_path: Path,
    writer_descriptors: dict[Path, int],
    report_text: str,
) -> None:
    descriptor = writer_descriptors.pop(report_path, None)
    if descriptor is None:
        try:
            descriptor = os.open(report_path, os.O_WRONLY | os.O_NONBLOCK)
        except OSError as error:
            if error.errno == errno.ENXIO:
                return
            raise
    os.write(descriptor, report_text.encode())
    os.close(descriptor)


async def test_dashboard_app_rejects_an_unregistered_widget_name(
    database: Database, connection: Connection, tmp_path: Path
) -> None:
    del connection
    project_dir = write_minimal_project(tmp_path)
    reader = StateReader(database, SystemClock())
    try:
        app = _app(reader, project_dir, widget_names=["no-such-widget"])
        with pytest.raises(WidgetPluginError):
            async with app.run_test():
                pass
    finally:
        reader.close()


async def test_dashboard_app_names_a_widget_with_an_unsupported_placement(
    database: Database, connection: Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del connection
    entry_points = registry_module.entry_points

    def entry_points_with_extension(*, group: str) -> list[object]:
        registered = entry_points(group=group)
        if group == WIDGET_ENTRY_POINT_GROUP:
            return [*registered, _InvalidPlacementEntryPoint()]
        return list(registered)

    monkeypatch.setattr(registry_module, "entry_points", entry_points_with_extension)
    project_dir = write_minimal_project(tmp_path)
    reader = StateReader(database, SystemClock())
    try:
        app = _app(reader, project_dir, widget_names=[_InvalidPlacementWidget.name])
        with pytest.raises(WidgetPluginError, match=_InvalidPlacementWidget.name):
            async with app.run_test():
                pass
    finally:
        reader.close()


async def test_dashboard_mounts_the_default_widgets(
    database: Database, connection: Connection, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = SystemClock()
    task = seed_task(connection, clock, title="Visible task", mode=Mode.IMPLEMENT)
    TaskStore(connection, clock).set_slot(task.id, 0)
    TaskStore(connection, clock).apply_transition(task.id, first_step(task))
    EventLog(connection, clock).append(
        EventKind.ENGINE_FAULT,
        task_id=task.id,
        message="resource sample failed",
        data={
            "scope": "resource sample",
            "error": "host sample failed",
            "recoverable": True,
            "repeat": 2,
        },
    )
    reader = StateReader(database, SystemClock())
    settings = load_settings(project_dir)
    try:
        app = _app(reader, project_dir, widget_names=settings.dashboard.widgets)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            header = plain_text(app.query_one("#header", Static).content)
            assert "Engine stopped" in header
            assert "host sample failed" not in header
            assert "Running 1" in header
            assert "Waiting 0" in header
            assert app.query_one("#usage", Static).region.width > 0
            assert app.query_one("#resources", Static).region.width > 0
            queue_panel = app.query_one("#queue")
            detail_panel = app.query_one("#task-detail-panel")
            assert queue_panel.region.right <= detail_panel.region.x
            assert task.id in _rendered_text(app.query_one("#task-detail-identity", Static))

            home_screen = app.screen
            attention = app.query_one("#escalations", EscalationsPanel)
            await wait_until(lambda: attention.display, timeout=2)
            await _choose_focus_view(app, pilot, "Attention · decisions, stalls and faults")
            attention_row = attention.query_one("#escalation-panels", VerticalScroll).query_one(
                Static
            )
            await wait_until(
                lambda: (
                    app.focused is attention
                    and "engine fault" in _rendered_text(attention_row)
                    and task.id in _rendered_text(attention_row)
                ),
                timeout=2,
            )

            await _choose_focus_view(app, pilot, "Diagnostics · Faults")
            faults = app.screen.query_one("#faults", Static)
            await wait_until(lambda: "host sample failed" in plain_text(faults.content), timeout=2)
            assert "repeats 2" in plain_text(faults.content)
            await pilot.press("escape")
            await wait_until(lambda: app.screen is home_screen, timeout=2)
    finally:
        reader.close()


@pytest.mark.parametrize("size", _HOME_REVIEW_SIZES)
async def test_home_status_keeps_stopped_engine_fault_context_without_escalations_plugin(
    database: Database,
    connection: Connection,
    tmp_path: Path,
    size: tuple[int, int],
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = SystemClock()
    task = seed_task(connection, clock, title="Fault remains visible")
    EventLog(connection, clock).append(
        EventKind.ENGINE_FAULT,
        task_id=task.id,
        message="recorded host sampling failure",
        data={
            "scope": "resource sample",
            "error": "host sample failed",
            "recoverable": True,
            "repeat": 2,
        },
    )
    reader = StateReader(database, SystemClock())
    try:
        app = _app(reader, project_dir, widget_names=["header"])
        async with app.run_test(size=size):
            header = app.query_one("#header", Static)
            status = app.query_one("#escalations", Static)
            await wait_until(
                lambda: (
                    "Engine stopped" in " ".join(_rendered_widget_lines(header))
                    and "host sample failed" in _rendered_text(status)
                ),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            assert "host sample failed" not in _rendered_text(header)
            visible_status = " ".join(" ".join(_rendered_widget_lines(status)).split())
            assert "host sample failed" in visible_status
            assert "Engine stopped" in visible_status
    finally:
        reader.close()


@pytest.mark.parametrize("size", _HOME_REVIEW_SIZES)
async def test_home_attention_tracks_owned_held_and_engine_state_changes(
    database: Database,
    connection: Connection,
    tmp_path: Path,
    size: tuple[int, int],
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = SystemClock()
    services = StubServices(engine_running=True)
    paused = seed_task(connection, clock, title="Paused task")
    owned = seed_task(connection, clock, title="Owned task")
    runs = RunStore(connection, clock)
    run = runs.start(owned.id, Role.LEADER, Rung("codex", "gpt-6-luna", "high"))
    runs.mark_taken_over(run.id)
    tasks = TaskStore(connection, clock)
    tasks.set_holds(paused.id, frozenset({Hold.PAUSED}))
    tasks.set_holds(owned.id, frozenset({Hold.INTERVENTION}))
    reader = StateReader(database, clock)
    try:
        app = _app(reader, project_dir, widget_names=["queue", "escalations"], services=services)
        async with app.run_test(size=size) as pilot:
            attention = app.query_one("#escalations", EscalationsPanel)
            await wait_until(
                lambda: (
                    "Operator owns" in _rendered_text(_focus_attention_row(attention))
                    and "intervention" in _rendered_text(_focus_attention_row(attention))
                    and "paused" in _rendered_text(_focus_attention_row(attention))
                ),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            visible_status = " ".join(_rendered_widget_lines(_focus_attention_row(attention)))
            assert "Operator owns" in visible_status
            assert "intervention" in visible_status
            assert "paused" in visible_status
            assert owned.id in visible_status
            assert paused.id in visible_status
            assert "automation held" not in visible_status
            attention.activate_attention()
            app.query_one("#task-browser-list", ListView).focus()
            assert attention.display

            services.engine_running = False
            EventLog(connection, clock).append(EventKind.ENGINE_STOPPED, message="stopped")

            def stopped_state_visible() -> bool:
                visible = " ".join(_rendered_widget_lines(_focus_attention_row(attention)))
                return all(
                    value in visible
                    for value in (
                        "Engine stopped",
                        owned.id,
                        paused.id,
                        "intervention",
                        "paused",
                    )
                )

            await wait_until(stopped_state_visible, timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS)
            visible_status = " ".join(_rendered_widget_lines(_focus_attention_row(attention)))
            assert "intervention" in visible_status
            assert "paused" in visible_status
            assert owned.id in visible_status
            assert paused.id in visible_status
            assert "automation held" not in visible_status
            assert "Engine stopped" in visible_status
            assert "Automation continues" not in _rendered_text(_focus_attention_row(attention))
            assert attention.display
            if size == _COMPACT_HOME_SIZE:
                panels = attention.query_one("#escalation-panels", VerticalScroll)
                attention.focus()
                await wait_until(lambda: panels.max_scroll_y > 0, timeout=2)
                await pilot.press("down")
                await wait_until(lambda: panels.scroll_y > 0, timeout=2)
    finally:
        reader.close()


def _focus_attention_row(attention: EscalationsPanel) -> Static:
    return attention.query_one("#escalation-panels", VerticalScroll).query_one(Static)


def _seed_primary_usage(connection: Connection, clock: SystemClock) -> tuple[str, RunStore]:
    primary = seed_task(
        connection,
        clock,
        title="Visible attention and usage",
        mode=Mode.IMPLEMENT,
    )
    TaskStore(connection, clock).apply_transition(
        primary.id,
        Transition.stay(primary, step=Step.IMPLEMENT, status=Status.RUNNING),
    )
    runs = RunStore(connection, clock)
    _record_mixed_billing(runs, primary.id)
    return primary.id, runs


def _record_mixed_billing(runs: RunStore, task_id: str) -> None:
    api_run = runs.start(
        task_id,
        Role.IMPLEMENTER,
        Rung("codex", "gpt-6", "high"),
        billing_declaration=BillingDeclaration(
            BILLING_MODE_API,
            BILLING_SOURCE_OPERATOR_CONFIG,
        ),
    )
    runs.record_usage(
        api_run.id,
        Usage(
            _API_INPUT_TOKENS,
            _API_OUTPUT_TOKENS,
            0,
            0,
            _API_BILLED_COST_USD,
            COST_BASIS_BILLED,
            None,
        ),
    )
    subscription_run = runs.start(
        task_id,
        Role.LEADER,
        Rung("claude", "sonnet", "medium"),
        billing_declaration=BillingDeclaration(
            BILLING_MODE_SUBSCRIPTION,
            BILLING_SOURCE_OPERATOR_CONFIG,
        ),
    )
    runs.record_usage(
        subscription_run.id,
        Usage(
            _SUBSCRIPTION_INPUT_TOKENS,
            _SUBSCRIPTION_OUTPUT_TOKENS,
            0,
            0,
            None,
            COST_BASIS_UNKNOWN,
            None,
        ),
    )
    unreported_run = runs.start(
        task_id,
        Role.HELPER,
        Rung("codex", "gpt-6-luna", "medium"),
        billing_declaration=BillingDeclaration(
            BILLING_MODE_API,
            BILLING_SOURCE_OPERATOR_CONFIG,
        ),
    )
    runs.record_usage(
        unreported_run.id,
        Usage(
            _UNREPORTED_INPUT_TOKENS,
            _UNREPORTED_OUTPUT_TOKENS,
            0,
            0,
            None,
            COST_BASIS_UNKNOWN,
            None,
        ),
    )
    estimated_run = runs.start(
        task_id,
        Role.IMPLEMENTER,
        Rung("codex", "gpt-6-luna", "medium"),
        billing_declaration=BillingDeclaration(
            BILLING_MODE_API,
            BILLING_SOURCE_OPERATOR_CONFIG,
        ),
    )
    runs.record_usage(
        estimated_run.id,
        Usage(
            _ESTIMATED_INPUT_TOKENS,
            _ESTIMATED_OUTPUT_TOKENS,
            0,
            0,
            _API_EQUIVALENT_ESTIMATE_USD,
            COST_BASIS_API_EQUIVALENT,
            None,
        ),
    )


def _seed_home_task_rows(
    connection: Connection,
    clock: SystemClock,
    primary_task_id: str,
    runs: RunStore,
) -> tuple[tuple[str, ...], str, str, str]:
    tasks = TaskStore(connection, clock)
    verifying = seed_task(connection, clock, title="Verify the implementation", mode=Mode.IMPLEMENT)
    tasks.apply_transition(
        verifying.id,
        Transition.stay(verifying, step=Step.VERIFY, status=Status.RUNNING),
    )
    waiting = seed_task(connection, clock, title="Wait for dependency", mode=Mode.IMPLEMENT)
    tasks.set_depends_on(waiting.id, (primary_task_id,))
    tasks.set_wait_reason(waiting.id, WaitReason.DEPENDENCY)
    paused = seed_task(connection, clock, title="Paused follow-up", mode=Mode.IMPLEMENT)
    tasks.set_holds(paused.id, frozenset({Hold.PAUSED}))
    owned = seed_task(connection, clock, title="Operator-owned follow-up", mode=Mode.IMPLEMENT)
    owned_run = runs.start(
        owned.id,
        Role.LEADER,
        Rung("claude", "sonnet", "medium"),
        billing_declaration=BillingDeclaration(
            BILLING_MODE_SUBSCRIPTION,
            BILLING_SOURCE_OPERATOR_CONFIG,
        ),
    )
    runs.mark_taken_over(owned_run.id)
    tasks.set_holds(owned.id, frozenset({Hold.INTERVENTION}))
    queued = seed_task(connection, clock, title="Queued follow-up")
    completed = seed_task(connection, clock, title="Completed follow-up")
    tasks.apply_transition(
        completed.id,
        Transition.stay(completed, step=None, status=Status.DONE),
    )
    active_task_ids = (primary_task_id, verifying.id, waiting.id, paused.id, owned.id, queued.id)
    return active_task_ids, completed.id, paused.id, owned.id


def _home_review_services(
    project_dir: Path,
    clock: SystemClock,
    *,
    engine_pid: int | None = None,
) -> StubServices:
    settings = load_settings(project_dir)
    resource_snapshot = ResourceSampler(
        clock,
        settings.orchestration.resources,
    ).sample_process_trees(engine_pid, ())
    services = StubServices(engine_running=True)
    services.resource_observation = ResourceView(
        host_cpu_pct=resource_snapshot.host_cpu_pct,
        host_ram_used_pct=resource_snapshot.host_ram_used_pct,
        free_ram_gb=resource_snapshot.free_ram_gb,
        instance_cpu_pct=resource_snapshot.instance_cpu_pct,
        instance_rss_gb=resource_snapshot.instance_rss_gb,
        sampled_at=resource_snapshot.sampled_at,
        state=(
            DiagnosticSampleState.AVAILABLE
            if resource_snapshot.instance_cpu_pct is not None
            else DiagnosticSampleState.EMPTY
        ),
        host_ram_used_bytes=resource_snapshot.host_ram_used_bytes,
        host_ram_total_bytes=resource_snapshot.host_ram_total_bytes,
    )
    return services


async def _assert_mixed_home_readouts(app: DashboardApp) -> None:
    readouts = app.query_one("#focus-readouts", VerticalScroll)
    identity = app.query_one("#header", Static)
    usage = app.query_one("#usage", Static)
    resources = app.query_one("#resources", Static)
    attention = app.query_one("#escalations", EscalationsPanel)
    attention_rows = attention.query_one("#escalation-panels", VerticalScroll)
    await wait_until(
        lambda: (
            "2.7k tok" in " ".join(_rendered_widget_lines(usage))
            and attention.display
            and "Operator owns" in _rendered_text(attention_rows.query_one(Static))
            and "paused" in _rendered_text(attention_rows.query_one(Static))
        ),
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    primary = (identity, usage, resources)
    assert readouts.scroll_y == 0
    assert all(widget.is_on_screen for widget in primary)
    assert all(widget.region.y >= readouts.region.y for widget in primary)
    assert all(widget.region.bottom <= readouts.region.bottom for widget in primary)
    assert attention.region.bottom <= app.query_one("#focus-main", Horizontal).region.y
    assert app.query_one("#task-browser-list", ListView).region.height >= _MIN_WIDE_TASK_LIST_ROWS

    identity_text = " ".join(_rendered_widget_lines(identity))
    usage_text = " ".join(_rendered_widget_lines(usage))
    resource_text = " ".join(_rendered_widget_lines(resources))
    attention_text = " ".join(_rendered_widget_lines(attention_rows.query_one(Static)))
    assert "Engine running" in identity_text
    assert "2.7k tok" in usage_text
    assert "API billed $0.42" in usage_text
    assert "API unreported 2" in usage_text
    assert "API-equiv. est. $0.11" not in usage_text
    assert "1 subscription agent" in usage_text
    assert usage.region.height == 1
    assert "Host" in resource_text
    assert "Shikiru + agents" in resource_text
    assert "process tree empty" in resource_text
    assert _resource_groups_are_right_aligned(resources), (
        _rendered_widget_lines(resources),
        resources.region.width,
        resources.size.width,
    )
    assert "Operator owns" in attention_text


def _capture_home_screenshot(app: DashboardApp, size: tuple[int, int]) -> None:
    capture_dir = Path("build/review/task98")
    capture_dir.mkdir(parents=True, exist_ok=True)
    color_mode = "mono" if os.environ.get("NO_COLOR") else "color"
    capture_path = capture_dir / (
        f"mixed-home-{size[0]}x{size[1]}-{color_mode}-selected-second.svg"
    )
    capture_path.write_text(app.export_screenshot(), encoding="utf-8")


async def _assert_mixed_home_task_rows(
    app: DashboardApp,
    row_ids: tuple[tuple[str, ...], str, str, str],
    size: tuple[int, int],
) -> None:
    active_task_ids, completed_task_id, paused_task_id, owned_task_id = row_ids
    task_list = app.query_one("#task-browser-list", ListView)
    await wait_until(
        lambda: task_list.children[1].region.y > task_list.children[0].region.bottom,
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    children = tuple(task_list.children)
    recent_heading = app.query_one("#task-browser-recent-heading", ListItem)
    recent_task = app.query_one(f"#task-row-{completed_task_id}", ListItem)
    assert recent_heading.disabled
    assert children.index(recent_heading) < children.index(recent_task)
    item_ids = {item.id for item in children}
    assert all(f"task-row-{task_id}" in item_ids for task_id in active_task_ids)
    assert all(item.region.overlaps(task_list.scrollable_content_region) for item in children)
    assert recent_task.region.bottom <= task_list.scrollable_content_region.bottom
    selected_item = app.query_one(f"#task-row-{active_task_ids[1]}", ListItem)
    assert task_list.highlighted_child is selected_item
    edge_lines = tuple(
        strip.text
        for strip in selected_item.render_lines(
            Region(0, 0, selected_item.region.width, selected_item.region.height)
        )
    )
    assert edge_lines[0][0] == "┌"
    assert edge_lines[0][-1] == "┐"
    assert edge_lines[-1][0] == "└"
    assert edge_lines[-1][-1] == "┘"
    assert all(line.startswith("│") and line.endswith("│") for line in edge_lines[1:-1])
    _capture_home_screenshot(app, size)
    attention = app.query_one("#escalations", EscalationsPanel)
    attention_row = attention.query_one("#escalation-panels", VerticalScroll).query_one(Static)
    attention_text = " ".join(_rendered_widget_lines(attention_row))
    assert "Operator owns" in attention_text
    assert owned_task_id in attention_text
    assert paused_task_id in attention_text


@pytest.mark.parametrize("size", [_STANDARD_HOME_SIZE, _WIDE_HOME_SIZE])
async def test_wide_home_keeps_usage_resources_and_attention_visible_above_the_task_list(
    database: Database,
    connection: Connection,
    tmp_path: Path,
    size: tuple[int, int],
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = SystemClock()
    primary_task_id, runs = _seed_primary_usage(connection, clock)
    active_task_ids, completed_task_id, paused_task_id, owned_task_id = _seed_home_task_rows(
        connection,
        clock,
        primary_task_id,
        runs,
    )
    services = _home_review_services(project_dir, clock)
    reader = StateReader(database, clock)
    try:
        app = _app(
            reader,
            project_dir,
            widget_names=["header", "queue", "escalations", "usage", "resources"],
            services=services,
        )
        async with app.run_test(size=size) as pilot:
            await _assert_mixed_home_readouts(app)
            task_list = app.query_one("#task-browser-list", ListView)
            task_list.focus()
            await pilot.press("down")
            await wait_until(
                lambda: (
                    task_list.index == 1
                    and task_list.highlighted_child is not None
                    and task_list.highlighted_child.id == f"task-row-{active_task_ids[1]}"
                ),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            await _assert_mixed_home_task_rows(
                app,
                (active_task_ids, completed_task_id, paused_task_id, owned_task_id),
                size,
            )
    finally:
        reader.close()


@pytest.mark.parametrize("populated", [False, True])
async def test_resource_groups_stay_aligned_after_resize_for_observed_process_trees(
    database: Database,
    connection: Connection,
    tmp_path: Path,
    populated: bool,
) -> None:
    del connection
    project_dir = write_minimal_project(tmp_path)
    clock = SystemClock()
    reader = StateReader(database, clock)
    services = _home_review_services(
        project_dir,
        clock,
        engine_pid=os.getpid() if populated else None,
    )
    try:
        app = _app(reader, project_dir, widget_names=["resources"], services=services)
        async with app.run_test(size=_WIDE_HOME_SIZE) as pilot:
            resources = app.query_one("#resources", Static)
            expected_group = "Shikiru + agents CPU" if populated else "process tree empty"
            await wait_until(
                lambda: (
                    expected_group in _rendered_text(resources)
                    and _resource_groups_are_right_aligned(resources)
                ),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            initial_width = resources.region.width
            await pilot.resize_terminal(*_STANDARD_HOME_SIZE)
            await wait_until(
                lambda: (
                    resources.region.width != initial_width
                    and expected_group in _rendered_text(resources)
                    and _resource_groups_are_right_aligned(resources)
                ),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
    finally:
        reader.close()


async def test_focus_keeps_selected_task_and_reading_position_after_reorder(
    database: Database, connection: Connection, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = SystemClock()
    tasks = [
        seed_task(connection, clock, title=f"Queued task {index}", priority=0)
        for index in range(21)
    ]
    reader = StateReader(database, clock)
    try:
        app = _app(reader, project_dir, widget_names=["queue"])
        async with app.run_test(size=(120, 40)) as pilot:
            list_view = app.query_one("#task-browser-list", ListView)
            await wait_until(lambda: len(list_view.children) == len(tasks), timeout=2)
            list_view.scroll_to(y=8, animate=False)
            list_view.index = 7
            selected = tasks[7]
            identity = app.query_one("#task-detail-identity", Static)
            await wait_until(lambda: selected.title in _rendered_text(identity), timeout=2)
            await pilot.pause()

            def visible_anchor() -> tuple[str, int]:
                visible = [
                    item
                    for item in list_view.children
                    if item.region.overlaps(list_view.scrollable_content_region)
                ]
                anchor = min(visible, key=lambda item: item.region.y)
                return (
                    anchor.id or "",
                    anchor.region.y - list_view.scrollable_content_region.y,
                )

            anchor_before = visible_anchor()
            moved = tasks[5]
            writer = database.connect()
            try:
                TaskStore(writer, clock).set_priority(moved.id, 9)
            finally:
                writer.close()
            await wait_until(
                lambda: (
                    list_view.children[0].id == f"task-row-{moved.id}"
                    and list_view.index == 7
                    and selected.title in _rendered_text(identity)
                ),
                timeout=2,
            )
            await wait_until(lambda: visible_anchor() == anchor_before, timeout=2)
            anchor_after = visible_anchor()
            assert anchor_after == anchor_before, (
                anchor_before,
                anchor_after,
                list_view.scroll_y,
                list_view.scroll_target_y,
                [
                    (
                        item.id,
                        item.region.y - list_view.scrollable_content_region.y,
                        item.region.height,
                    )
                    for item in list_view.children[:8]
                ],
            )
    finally:
        reader.close()


async def test_home_task_list_keeps_a_recent_completed_history_row(
    database: Database,
    connection: Connection,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = SystemClock()
    active = seed_task(connection, clock, title="Active request")
    completed = seed_task(connection, clock, title="Completed request")
    TaskStore(connection, clock).apply_transition(
        completed.id,
        Transition.stay(completed, step=None, status=Status.DONE),
    )
    reader = StateReader(database, clock)
    try:
        app = _app(reader, project_dir, widget_names=["queue"])
        async with app.run_test(size=_WIDE_HOME_SIZE):
            task_list = app.query_one("#task-browser-list", ListView)
            await wait_until(
                lambda: any(item.id == f"task-row-{completed.id}" for item in task_list.children),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            rows = tuple(
                _rendered_text(item.query_one(Static))
                for item in task_list.children
                if isinstance(item, ListItem)
            )
            active_row = next(index for index, row in enumerate(rows) if active.id in row)
            recent_row = next(index for index, row in enumerate(rows) if completed.id in row)
            recent_heading = next(
                index
                for index, item in enumerate(task_list.children)
                if item.id == "task-browser-recent-heading"
            )
            assert active_row < recent_row
            assert active_row < recent_heading < recent_row
            assert task_list.children[recent_heading].disabled
            assert rows[recent_row].startswith("✓ ")
            assert "done" in rows[recent_row]
    finally:
        reader.close()


@pytest.mark.parametrize("size", _HOME_REVIEW_SIZES)
async def test_home_quiet_status_reports_running_unblocked_engine(
    database: Database,
    connection: Connection,
    tmp_path: Path,
    size: tuple[int, int],
) -> None:
    del connection
    project_dir = write_minimal_project(tmp_path)
    services = StubServices(engine_running=True)
    reader = StateReader(database, SystemClock())
    try:
        app = _app(reader, project_dir, widget_names=["queue", "escalations"], services=services)
        async with app.run_test(size=size):
            attention = app.query_one("#escalations", EscalationsPanel)
            status = _focus_attention_row(attention)
            await wait_until(
                lambda: "Automation continues" in _rendered_text(status),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            visible_status = " ".join(_rendered_widget_lines(status))
            assert "No decisions needed" in visible_status
            assert "Automation continues" in visible_status
    finally:
        reader.close()


async def test_focus_operational_counts_and_contextual_task_filters(
    database: Database, connection: Connection, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = SystemClock()
    active = seed_task(connection, clock, title="Active focus task", mode=Mode.IMPLEMENT)
    TaskStore(connection, clock).set_slot(active.id, 0)
    TaskStore(connection, clock).apply_transition(active.id, first_step(active))
    waiting = seed_task(connection, clock, title="Waiting focus task")
    reader = StateReader(database, SystemClock())
    try:
        app = _app(reader, project_dir, widget_names=load_settings(project_dir).dashboard.widgets)
        async with app.run_test(size=(120, 40)) as pilot:
            home_screen = app.screen
            await pilot.press("z")
            await pilot.pause()
            assert app.screen is home_screen
            assert app.query_one("#focus-main")
            assert not app.query("#dashboard-panels")

            header = app.query_one("#header", Static)
            await wait_until(
                lambda: "Running 1 · Waiting 1" in plain_text(header.content),
                timeout=2,
            )
            assert app.query_one("#escalations").display is True
            await _choose_focus_view(app, pilot, "Task filter · Waiting")
            count = app.query_one("#task-browser-count", Static)
            await wait_until(
                lambda: plain_text(count.content).startswith("TASKS · Waiting · 1 of 1"),
                timeout=2,
            )
            assert waiting.title in plain_text(
                app.query_one("#task-browser-list", ListView).children[0].query_one(Static).content
            )
            await pilot.press("/")
            search = app.query_one("#task-browser-search", Input)
            assert app.focused is search
            await pilot.press("z")
            assert search.value == "z"
            await pilot.press("v")
            assert search.value == "zv"
            await pilot.press("escape")
            await wait_until(
                lambda: search.value == "",
                timeout=2,
            )
            await pilot.press("escape")
            assert search.display is False
    finally:
        reader.close()


@pytest.mark.parametrize("size", _HOME_REVIEW_SIZES)
async def test_focus_attention_opens_durable_decision_evidence(
    database: Database, connection: Connection, tmp_path: Path, size: tuple[int, int]
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = SystemClock()
    task = seed_task(connection, clock, title="Choose retry behavior")
    TaskStore(connection, clock).apply_transition(
        task.id,
        Transition.stay(task, step=task.step, status=Status.ESCALATED),
    )
    unrelated_held = seed_task(connection, clock, title="Unrelated paused task")
    TaskStore(connection, clock).set_holds(unrelated_held.id, frozenset({Hold.PAUSED}))
    escalation = EscalationStore(connection, clock).open(
        task.id,
        step=task.step,
        reason=EscalationReason.DECISION_REQUIRED,
        headline="Choose whether to retry",
        packet_path=".shikiru/escalations/missing-packet.md",
        options=options_for(EscalationReason.DECISION_REQUIRED),
    )
    reader = StateReader(database, clock)
    try:
        app = _app(reader, project_dir, widget_names=["escalations"])
        async with app.run_test(size=size) as pilot:
            attention = app.query_one("#escalations", EscalationsPanel)
            await wait_until(lambda: attention.display, timeout=2)
            row = attention.query_one("#escalation-panels", VerticalScroll).query_one(Static)
            assert escalation.id in _rendered_text(row)
            assert "Enter inspect · r resolve · o evidence" in _rendered_text(row)
            visible_status = " ".join(_rendered_widget_lines(row))
            assert escalation.id in visible_status
            assert "Enter inspect" in visible_status
            assert "Engine stopped" in visible_status
            status_lines = _rendered_text(row).splitlines()
            assert "paused" not in status_lines[0]
            assert any(unrelated_held.id in line and "paused" in line for line in status_lines)

            await _choose_focus_view(app, pilot, "Attention · decisions, stalls and faults")
            await wait_until(lambda: app.focused is attention, timeout=2)
            await pilot.press("o")
            await wait_until(lambda: bool(app.screen.query("#escalation-packet")), timeout=2)
            packet = app.screen.query_one("#escalation-packet", Static)
            packet_text = _rendered_text(packet)
            assert escalation.id in packet_text
            assert "Choose whether to retry" in packet_text
            assert "Evidence error" in packet_text
            opened = EscalationStore(connection, clock).get(escalation.id)
            assert opened is not None
            assert opened.status is EscalationStatus.OPEN
    finally:
        reader.close()


async def test_clicking_primary_attention_opens_the_answer_form_without_dispatch(
    database: Database,
    connection: Connection,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = SystemClock()
    task = seed_task(connection, clock, title="Answer from attention")
    TaskStore(connection, clock).apply_transition(
        task.id,
        Transition.stay(task, step=task.step, status=Status.ESCALATED),
    )
    escalation = EscalationStore(connection, clock).open(
        task.id,
        step=task.step,
        reason=EscalationReason.DECISION_REQUIRED,
        headline="Choose the parser behavior",
        packet_path=".shikiru/escalations/missing-packet.md",
        options=options_for(EscalationReason.DECISION_REQUIRED),
    )
    queue = CommandQueue(connection, clock)
    for command in queue.pending():
        queue.mark_done(command.id)
    reader = StateReader(database, clock)
    app: DashboardApp | None = None

    async def open_palette_context(request: PaletteRequest) -> None:
        assert app is not None
        await app.push_screen(
            _palette_screen_for_app(reader, connection, project_dir, clock, request)
        )

    try:
        app = _app(
            reader,
            project_dir,
            widget_names=["escalations"],
            open_palette_context=open_palette_context,
        )
        async with app.run_test(size=_STANDARD_HOME_SIZE) as pilot:
            attention = app.query_one("#escalations", EscalationsPanel)
            await wait_until(lambda: attention.display, timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS)
            row = attention.query_one("#escalation-panels", VerticalScroll).query_one(Static)
            assert escalation.id in _rendered_text(row)

            await pilot.click(row)
            await wait_until(
                lambda: isinstance(app.screen, PaletteScreen),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            screen = cast("PaletteScreen", app.screen)
            assert screen.query_one("#task-id", Input).value == task.id
            assert cast("Select[str]", screen.query_one("#action", Select)).value == "resolve"
            assert (
                cast("Select[str]", screen.query_one("#escalation", Select)).value == escalation.id
            )
            assert cast("Select[str]", screen.query_one("#resolution", Select)).value == "answer"
            assert screen.query_one("#field-resolution-text").display
            assert not screen.query_one("#field-role").display
            assert queue.pending() == []

            answer = "Preserve the existing call pattern."
            screen.query_one("#resolution-text", TextArea).text = answer
            await pilot.press("escape")
            await wait_until(
                lambda: screen.query_one("#palette-menu").display,
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            assert screen.query_one("#palette-menu-action-resolve").display
            assert queue.pending() == []
            await pilot.click("#palette-menu-action-resolve")
            await wait_until(
                lambda: not screen.query_one("#palette-menu").display,
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            assert screen.query_one("#resolution-text", TextArea).text == answer
            assert cast("Select[str]", screen.query_one("#resolution", Select)).value == "answer"
            assert queue.pending() == []
    finally:
        reader.close()


async def test_palette_close_keeps_pending_command_visible_in_dashboard_history(
    database: Database,
    connection: Connection,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = SystemClock()
    reader = StateReader(database, clock)
    settings = load_settings(project_dir)
    services = StubServices()
    queue = CommandQueue(connection, clock)
    commands = OperatorCommands(queue, clock, engine_up=services.engine_up)
    queries = OperatorQueries(
        reader,
        project=settings.project.name,
        project_root=project_dir,
        engine_up=services.engine_up,
        max_parallel=settings.orchestration.max_parallel,
        clock=clock,
    )
    active_apps: list[DashboardApp] = []

    async def open_palette_context(request: PaletteRequest) -> None:
        del request
        await active_apps[0].push_screen(
            PaletteScreen(
                commands,
                queries,
                _unexpected_pane_selection,
                on_change=reader.watch,
                initial_context=PaletteRequest(action="stop"),
            )
        )

    try:
        first_app = _app(
            reader,
            project_dir,
            widget_names=[],
            open_palette_context=open_palette_context,
        )
        active_apps[:] = [first_app]
        async with first_app.run_test(size=(100, 34)) as pilot:
            dashboard_screen = first_app.screen
            await pilot.press("ctrl+p")
            await wait_until(lambda: isinstance(first_app.screen, PaletteScreen), timeout=2)
            palette = cast("PaletteScreen", first_app.screen)
            await pilot.click("#palette-submit")
            await wait_until(lambda: len(queue.pending()) == 1, timeout=2)
            pending_id = queue.pending()[0].id
            await wait_until(
                lambda: (
                    f"Pending · command {pending_id}"
                    in _rendered_text(palette.query_one("#palette-result", Static))
                ),
                timeout=2,
            )
            assert isinstance(queue.pending()[0].command, Stop)
            await pilot.press("escape")
            await wait_until(lambda: palette.query_one("#palette-menu").display, timeout=2)
            await pilot.press("escape")
            await wait_until(lambda: first_app.screen is dashboard_screen, timeout=2)
            await wait_until(
                lambda: (
                    f"#{pending_id} · Pending"
                    in _rendered_text(first_app.query_one("#workspace-command-status", Static))
                ),
                timeout=2,
            )
            await _choose_focus_view(
                first_app, pilot, "Command history · Pending, Applied and Rejected"
            )
            await wait_until(
                lambda: bool(first_app.screen.query("#command-history-page")), timeout=2
            )
            history_screen = first_app.screen
            history_copy = history_screen.query_one("#command-history-copy", Static)
            await pilot.pause()
            assert "Loading" not in _rendered_text(history_copy), type(history_screen).__name__
            assert f"#{pending_id} · Pending" in _rendered_text(history_copy)
            await pilot.press("escape")
            await wait_until(lambda: first_app.screen is dashboard_screen, timeout=2)
    finally:
        reader.close()


async def test_palette_resume_is_safe_when_home_has_no_task_detail(
    database: Database,
    connection: Connection,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = SystemClock()
    reader = StateReader(database, clock)
    active_apps: list[DashboardApp] = []

    async def open_palette(action: str) -> None:
        app = active_apps[0]
        await app.push_screen(
            _palette_screen_for_app(
                reader,
                connection,
                project_dir,
                clock,
                PaletteRequest(action=action),
            ),
            callback=lambda _: app.refresh_task_detail_after_resume(),
        )

    try:
        app = _app(reader, project_dir, widget_names=["usage"], open_palette=open_palette)
        active_apps.append(app)
        async with app.run_test(size=(100, 34)) as pilot:
            home_screen = app.screen
            app.refresh_task_detail_after_resume()
            await pilot.press("ctrl+p")
            await wait_until(lambda: isinstance(app.screen, PaletteScreen), timeout=2)
            await pilot.press("escape")
            await wait_until(lambda: app.screen is home_screen, timeout=2)
            app.refresh_task_detail_after_resume()
    finally:
        reader.close()


async def test_dashboard_restart_recovers_another_clients_rejected_command(
    database: Database,
    connection: Connection,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = SystemClock()
    reader = StateReader(database, clock)
    queue = CommandQueue(connection, clock)
    try:
        first_app = _app(reader, project_dir, widget_names=[])
        async with first_app.run_test(size=(100, 34)):
            pass

        pending = queue.append(Stop())
        other_client = database.connect()
        try:
            other_queue = CommandQueue(other_client, clock)
            rejected = other_queue.append(Prioritize(task_id="T-404", priority=4))
            rejection = {"error": "fake engine rejected the target"}
            other_queue.mark_rejected(rejected.id, rejection)
        finally:
            other_client.close()

        restarted_app = _app(reader, project_dir, widget_names=[])
        async with restarted_app.run_test(size=(100, 34)) as pilot:
            status = restarted_app.query_one("#workspace-command-status", Static)
            await wait_until(
                lambda: (
                    f"#{rejected.id}" in _rendered_text(status)
                    and "Rejected" in _rendered_text(status)
                ),
                timeout=2,
            )
            await _choose_focus_view(
                restarted_app, pilot, "Command history · Pending, Applied and Rejected"
            )
            await wait_until(
                lambda: bool(restarted_app.screen.query("#command-history-page")), timeout=2
            )
            history_screen = restarted_app.screen
            history_copy = history_screen.query_one("#command-history-copy", Static)
            await wait_until(
                lambda: (
                    f"#{pending.id} · Pending" in _rendered_text(history_copy)
                    and "original acknowledgement wait context unknown"
                    in _rendered_text(history_copy)
                ),
                timeout=2,
            )
            history_text = _rendered_text(history_copy)
            assert f"#{rejected.id} · Rejected" in history_text
            assert "T-404" in history_text
            assert rejection["error"] in history_text
    finally:
        reader.close()


async def test_command_history_pages_recover_and_refresh_an_older_pending_command(
    database: Database,
    connection: Connection,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = SystemClock()
    reader = StateReader(database, clock)
    queue = CommandQueue(connection, clock)
    pending = queue.append(Stop())
    for index in range(51):
        applied = queue.append(Stop())
        queue.mark_done(applied.id, {"effect": index})

    try:
        app = _app(reader, project_dir, widget_names=[])
        async with app.run_test(size=(100, 34)) as pilot:
            home_screen = app.screen
            await _choose_focus_view(app, pilot, "Command history · Pending, Applied and Rejected")
            await wait_until(lambda: bool(app.screen.query("#command-history-page")), timeout=2)
            history_screen = app.screen
            history_copy = history_screen.query_one("#command-history-copy", Static)
            history_scroll = history_screen.query_one("#command-history-scroll", VerticalScroll)
            page = cast("Static", history_screen.query_one("#command-history-page"))
            await wait_until(lambda: "page 1" in _rendered_text(page), timeout=2)
            assert f"#{pending.id} · Pending" not in _rendered_text(history_copy)
            assert history_scroll.max_scroll_y > 0

            await pilot.click("#command-history-next")
            await wait_until(lambda: "page 2" in _rendered_text(page), timeout=2)
            next_button = history_screen.query_one("#command-history-next", Button)
            assert not next_button.disabled, _rendered_text(page)
            await pilot.press("right")
            await wait_until(lambda: "page 3" in _rendered_text(page), timeout=2)
            assert f"#{pending.id} · Pending" in _rendered_text(history_copy), (
                _rendered_text(page),
                _rendered_text(history_copy),
            )
            page_text = _rendered_text(page)
            assert "page 3" in page_text
            assert "rows 41-52 of 52" in page_text
            history_scroll.scroll_to(y=history_scroll.max_scroll_y, animate=False)
            await pilot.pause()
            scroll_before_refresh = history_scroll.scroll_y
            focus_before_refresh = app.focused

            engine = database.connect()
            try:
                CommandQueue(engine, clock).mark_done(pending.id, {"effect": "applied once"})
            finally:
                engine.close()
            await wait_until(
                lambda: (
                    f"#{pending.id} · Applied" in _rendered_text(history_copy)
                    and "applied once" in _rendered_text(history_copy)
                ),
                timeout=2,
            )
            await pilot.pause()
            assert app.focused is focus_before_refresh
            assert history_scroll.scroll_y == scroll_before_refresh
            assert app.screen is not home_screen
    finally:
        reader.close()


@pytest.mark.parametrize("width", [36, 48])
async def test_narrow_home_keeps_keyboard_routes_reachable(
    database: Database,
    connection: Connection,
    tmp_path: Path,
    width: int,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = SystemClock()
    reader = StateReader(database, clock)
    queue = CommandQueue(connection, clock)
    for _ in range(3):
        queue.append(Stop())

    try:
        app = _app(reader, project_dir, widget_names=[])
        async with app.run_test(size=(width, 20)) as pilot:
            await pilot.pause()
            status = app.query_one("#workspace-command-status", Static)
            assert status.size.height == 1
            footer = app.query_one("#focus-footer", Static)
            assert footer.region.bottom <= 20
            assert footer.region.right <= width
            home_screen = app.screen
            await pilot.press("v")
            await wait_until(lambda: app.screen is not home_screen, timeout=2)
            await pilot.pause()
            await _choose_focus_view(app, pilot, "Command history · Pending, Applied and Rejected")
            await wait_until(lambda: bool(app.screen.query("#command-history-page")), timeout=2)
            page = cast("Static", app.screen.query_one("#command-history-page"))
            assert "all statuses" in _rendered_text(page)
    finally:
        reader.close()


async def _assert_home_resize_matrix(
    app: DashboardApp,
    pilot: Pilot[None],
    task: Task,
) -> None:
    identity = app.query_one("#task-detail-identity", Static)
    await wait_until(lambda: task.title in _rendered_text(identity), timeout=2)
    home_layout = app.query_one("#focus-main", Horizontal)
    queue_panel = app.query_one("#queue")
    detail_panel = app.query_one("#task-detail-panel")
    assert queue_panel.region.right <= detail_panel.region.x
    assert detail_panel.region.width > 0

    search = app.query_one("#task-browser-search", Input)
    await pilot.press("/")
    search.value = task.id
    search.focus()
    list_view = app.query_one("#task-browser-list", ListView)
    await wait_until(
        lambda: (
            task.title in _rendered_text(identity)
            and f"task-row-{task.id}" in {item.id for item in list_view.children}
        ),
        timeout=2,
    )
    assert list_view.index == 0
    row_text = _rendered_text(list_view.children[0].query_one(Static))
    assert task.id in row_text
    assert task.title in row_text

    for width, height in (
        _COMPACT_HOME_SIZE,
        (36, 20),
        (48, 14),
        _TINY_HOME_SIZE,
    ):
        await pilot.resize_terminal(width, height)
        await pilot.pause()
        assert app.focused is search
        assert search.value == task.id
        assert task.title in _rendered_text(identity)
        assert home_layout.region.width <= width
        assert detail_panel.display is False
        _assert_home_navigation_fits(app, width, height)
        if (width, height) == _COMPACT_HOME_SIZE:
            selected_item = app.query_one(f"#task-row-{task.id}", ListItem)
            assert selected_item.region.height <= list_view.scrollable_content_region.height
            assert selected_item.region.y >= home_layout.region.y
            assert selected_item.region.bottom <= home_layout.region.bottom


async def _assert_tiny_home_task_routes(
    app: DashboardApp,
    pilot: Pilot[None],
    task: Task,
    palette_requests: list[PaletteRequest],
) -> None:
    home_screen = app.screen
    root = app.query_one("#focus-root", VerticalScroll)
    list_view = app.query_one("#task-browser-list", ListView)
    selected_item = app.query_one(f"#task-row-{task.id}", ListItem)
    list_view.focus()
    await wait_until(
        lambda: (
            task.id in _rendered_text(selected_item.query_one(Static))
            and task.title in _rendered_text(selected_item.query_one(Static))
            and "Implementing" in _rendered_text(selected_item.query_one(Static))
        ),
        timeout=2,
    )
    assert root.max_scroll_y > 0
    root.focus()
    await pilot.press("end")
    await wait_until(lambda: root.scroll_y == root.max_scroll_y, timeout=2)
    assert selected_item.region.y >= root.scrollable_content_region.y
    assert selected_item.region.bottom <= root.scrollable_content_region.bottom, (
        selected_item.region,
        root.scrollable_content_region,
        _rendered_widget_lines(selected_item.query_one(Static)),
    )
    assert selected_item.region.height <= root.scrollable_content_region.height
    assert "Implementing" in _rendered_text(selected_item.query_one(Static))
    await pilot.press("ctrl+p")
    await wait_until(lambda: bool(palette_requests), timeout=2)
    assert palette_requests[0].task_id == task.id
    assert app.screen is home_screen
    list_view.focus()
    await pilot.press("enter")
    await wait_until(lambda: isinstance(app.screen, TaskDetailScreen), timeout=2)
    detail_identity = app.screen.query_one("#task-detail-identity", Static)
    assert task.id in _rendered_text(detail_identity)
    assert task.title in _rendered_text(detail_identity)
    await pilot.press("escape")
    await wait_until(lambda: app.screen is home_screen, timeout=2)
    assert app.focused is list_view
    assert app.query_one("#task-browser-search", Input).value == task.id
    assert list_view.index == 0


async def test_home_resizes_through_the_terminal_matrix_without_losing_selection_or_focus(
    database: Database,
    connection: Connection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    project_dir = write_minimal_project(tmp_path)
    task = seed_task(
        connection, SystemClock(), title="Responsive selected task", mode=Mode.IMPLEMENT
    )
    stored_task = TaskStore(connection, SystemClock()).require(task.id)
    TaskStore(connection, SystemClock()).apply_transition(
        task.id,
        Transition.stay(stored_task, step=Step.IMPLEMENT, status=Status.RUNNING),
    )
    reader = StateReader(database, SystemClock())

    try:
        palette_requests: list[PaletteRequest] = []

        async def open_palette_context(request: PaletteRequest) -> None:
            palette_requests.append(request)

        app = _app(
            reader,
            project_dir,
            widget_names=["queue"],
            open_palette_context=open_palette_context,
        )
        async with app.run_test(size=_WIDE_HOME_SIZE) as pilot:
            await _assert_home_resize_matrix(app, pilot, task)
            await _assert_tiny_home_task_routes(app, pilot, task, palette_requests)
    finally:
        reader.close()


async def test_home_row_click_selects_and_enter_opens_a_returnable_inspector(
    database: Database,
    connection: Connection,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = SystemClock()
    tasks = (
        seed_task(connection, clock, title="Focused route first"),
        seed_task(connection, clock, title="Focused route second"),
    )
    reader = StateReader(database, clock)
    try:
        app = _app(reader, project_dir, widget_names=["queue"])
        async with app.run_test(size=_WIDE_HOME_SIZE) as pilot:
            home = app.screen
            list_view = app.query_one("#task-browser-list", ListView)
            await wait_until(
                lambda: all(app.query(f"#task-row-{task.id}") for task in tasks),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            before_commands = reader.command_history(limit=COMMAND_HISTORY_TEST_LIMIT).total_count
            search = app.query_one("#task-browser-search", Input)
            await pilot.press("/")
            search.value = "Focused route"
            await wait_until(
                lambda: all(app.query(f"#task-row-{task.id}") for task in tasks),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            row_ids = tuple(
                item.id.removeprefix("task-row-")
                for item in list_view.children
                if item.id is not None and item.id.startswith("task-row-")
            )
            first_id, last_id = row_ids[0], row_ids[-1]
            await pilot.click(f"#task-row-{last_id}")
            await wait_until(
                lambda: (
                    last_id in _rendered_text(app.query_one(f"#{TASK_DETAIL_IDENTITY}", Static))
                ),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            assert app.screen is home
            assert search.value == "Focused route"
            assert (
                reader.command_history(limit=COMMAND_HISTORY_TEST_LIMIT).total_count
                == before_commands
            )

            list_view.focus()
            await pilot.press("down")
            await wait_until(
                lambda: (
                    first_id in _rendered_text(app.query_one(f"#{TASK_DETAIL_IDENTITY}", Static))
                ),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            await pilot.press("up")
            await wait_until(
                lambda: (
                    last_id in _rendered_text(app.query_one(f"#{TASK_DETAIL_IDENTITY}", Static))
                ),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            await pilot.press("down")
            await wait_until(
                lambda: (
                    first_id in _rendered_text(app.query_one(f"#{TASK_DETAIL_IDENTITY}", Static))
                ),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            await pilot.press("enter")
            await wait_until(
                lambda: isinstance(app.screen, TaskDetailScreen),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            detail_screen = cast("TaskDetailScreen", app.screen)
            assert detail_screen.has_class("wide-overlay")
            assert first_id in _rendered_text(
                detail_screen.query_one(f"#{TASK_DETAIL_IDENTITY}", Static)
            )
            assert (
                reader.command_history(limit=COMMAND_HISTORY_TEST_LIMIT).total_count
                == before_commands
            )

            await pilot.press("escape")
            await wait_until(
                lambda: app.screen is home,
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            assert app.focused is list_view
            assert search.value == "Focused route"
            assert first_id in _rendered_text(app.query_one(f"#{TASK_DETAIL_IDENTITY}", Static))
            assert (
                reader.command_history(limit=COMMAND_HISTORY_TEST_LIMIT).total_count
                == before_commands
            )
            await _assert_home_inline_mouse_routes(
                app,
                pilot,
                first_id,
                reader,
                before_commands,
            )
    finally:
        reader.close()


async def _assert_home_inline_mouse_routes(
    app: DashboardApp,
    pilot: Pilot[None],
    task_id: str,
    reader: StateReader,
    before_commands: int,
) -> None:
    panel = app.query_one("#task-detail-panel", TaskDetailPanel)
    assert await pilot.click(f"#{TASK_DETAIL_EVIDENCE_INLINE_BUTTON}")
    await wait_until(
        lambda: panel.active_view == TASK_VIEW_EVIDENCE,
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    assert task_id in _rendered_text(app.query_one(f"#{TASK_DETAIL_IDENTITY}", Static))
    await pilot.press("escape")
    await wait_until(
        lambda: panel.active_view == TASK_VIEW_SUMMARY,
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    assert await pilot.click(f"#{TASK_DETAIL_USAGE_INLINE_BUTTON}")
    await wait_until(
        lambda: panel.active_view == TASK_VIEW_USAGE,
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    assert task_id in _rendered_text(app.query_one(f"#{TASK_DETAIL_IDENTITY}", Static))
    assert reader.command_history(limit=COMMAND_HISTORY_TEST_LIMIT).total_count == before_commands


def _assert_home_navigation_fits(app: DashboardApp, width: int, height: int) -> None:
    footer = app.query_one("#focus-footer", Static)
    root = app.query_one("#focus-root", VerticalScroll)
    assert footer.region.x >= 0
    assert footer.region.right <= width
    assert footer.region.y >= 0
    assert not footer.display or footer.region.bottom <= height or root.max_scroll_y > 0


async def test_home_help_works_with_keyboard_without_color(
    database: Database,
    connection: Connection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    project_dir = write_minimal_project(tmp_path)
    seed_task(connection, SystemClock(), title="Help in compact Home")
    reader = StateReader(database, SystemClock())
    try:
        app = _app(reader, project_dir, widget_names=["queue"])
        async with app.run_test(size=(36, 20)) as pilot:
            await pilot.press("?")
            await pilot.pause()
            assert "Mouse: select or open a task" in _rendered_text(
                app.screen.query_one("#workspace-help-copy", Static)
            )
            await pilot.press("escape")
            await pilot.pause()
            await pilot.press("?")
            await pilot.pause()
            assert app.screen.query_one("#workspace-help-copy", Static).display
            await pilot.press("escape")
    finally:
        reader.close()


async def test_task_detail_contextual_controls_are_reachable_at_narrow_width(
    database: Database,
    connection: Connection,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    task = seed_task(connection, SystemClock(), title="Accessible task detail")
    reader = StateReader(database, SystemClock())
    palette_requests: list[PaletteRequest] = []

    async def open_palette_context(request: PaletteRequest) -> None:
        palette_requests.append(request)

    try:
        app = _app(
            reader,
            project_dir,
            widget_names=["queue"],
            open_palette_context=open_palette_context,
        )
        async with app.run_test(size=(36, 20)) as pilot:
            search = app.query_one("#task-browser-search", Input)
            search.value = task.id
            search.focus()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("enter")
            await wait_until(lambda: isinstance(app.screen, TaskDetailScreen), timeout=2)
            identity = app.screen.query_one("#task-detail-identity", Static)
            await wait_until(
                lambda: task.title in str(identity.content),
                timeout=2,
            )
            await pilot.press("u")
            sessions = app.screen.query_one(f"#{TASK_DETAIL_SESSIONS}", Static)
            await wait_until(
                lambda: "recorded usage" in plain_text(sessions.content),
                timeout=2,
            )
            detail_screen = cast("TaskDetailScreen", app.screen)
            await pilot.press("e")
            native_select = cast(
                "Select[str]", detail_screen.query_one("#task-detail-timeline-category", Select)
            )
            await wait_until(lambda: native_select.display, timeout=2)
            native_select.focus()
            await pilot.press("ctrl+p")
            assert palette_requests == []
            assert app.screen is detail_screen

            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("ctrl+p")
            assert palette_requests == []
            assert native_select.expanded
            assert app.screen is detail_screen
            await pilot.press("escape")
            await wait_until(lambda: not native_select.expanded, timeout=2)
            assert app.screen is detail_screen

            detail_screen.query_one("#task-detail-body").focus()
            await pilot.press("ctrl+p")
            assert palette_requests[-1].task_id == task.id
    finally:
        reader.close()


async def test_home_shared_detail_refreshes_after_task_detail_closes(
    database: Database,
    connection: Connection,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = SystemClock()
    task = seed_task(connection, clock, title="Home detail resumes fresh")
    reader = StateReader(database, clock)
    active_apps: list[DashboardApp] = []

    async def open_palette_context(request: PaletteRequest) -> None:
        app = active_apps[0]
        await app.push_screen(
            _palette_screen_for_app(reader, connection, project_dir, clock, request),
            callback=lambda _: app.refresh_task_detail_after_resume(),
        )

    try:
        app = _app(
            reader,
            project_dir,
            widget_names=["queue"],
            open_palette_context=open_palette_context,
        )
        active_apps.append(app)
        async with app.run_test(size=(120, 40)) as pilot:
            home_screen = app.screen
            search = home_screen.query_one("#task-browser-search", Input)
            identity = home_screen.query_one("#task-detail-identity", Static)
            ownership = home_screen.query_one(f"#{TASK_DETAIL_OWNERSHIP}", Static)
            search.value = task.id
            search.focus()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("enter")
            await wait_until(lambda: isinstance(app.screen, TaskDetailScreen), timeout=2)
            task_screen = app.screen
            task_ownership = task_screen.query_one(f"#{TASK_DETAIL_OWNERSHIP}", Static)
            await pilot.press("ctrl+p")
            await wait_until(lambda: isinstance(app.screen, PaletteScreen), timeout=2)
            TaskStore(connection, clock).set_holds(task.id, frozenset({Hold.PAUSED}))
            assert Hold.PAUSED.value not in _rendered_text(ownership)
            await pilot.press("escape")
            await wait_until(lambda: app.screen is task_screen, timeout=2)
            await wait_until(
                lambda: Hold.PAUSED.value in _rendered_text(task_ownership),
                timeout=2,
            )
            assert Hold.PAUSED.value not in _rendered_text(ownership)
            await pilot.press("escape")
            await wait_until(
                lambda: "Home detail resumes fresh" in _rendered_text(identity), timeout=2
            )
            await wait_until(
                lambda: Hold.PAUSED.value in _rendered_text(ownership),
                timeout=2,
            )
            assert search.value == task.id
    finally:
        reader.close()


async def test_home_selection_discards_detail_result_from_an_in_flight_report_read(
    database: Database,
    connection: Connection,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    first = seed_task(connection, SystemClock(), title="First delayed report")
    second = seed_task(connection, SystemClock(), title="Second delayed report")
    report_text = (
        '{"title":"Loaded report","mode":"implement","complexity":"normal",'
        '"acceptance_criteria":[],"references":[]}'
    )
    report_paths = {
        task.id: turns_dir(project_dir, task.id) / "01-triage-i0" / REPORT_FILENAME
        for task in (first, second)
    }
    for report_path in report_paths.values():
        report_path.parent.mkdir(parents=True)
        (report_path.parent / REPORT_KIND_FILENAME).write_text(ReportKind.TRIAGE.value)
        os.mkfifo(report_path)
    writer_descriptors: dict[Path, int] = {}
    reader = StateReader(database, SystemClock())
    try:
        app = _app(reader, project_dir, widget_names=["queue"])
        async with app.run_test(size=(120, 40)) as pilot:
            identity = app.query_one("#task-detail-identity", Static)
            await wait_until(
                lambda: _open_report_writer(writer_descriptors, report_paths[first.id]), timeout=2
            )
            assert first.id in _rendered_text(identity)

            search = app.query_one("#task-browser-search", Input)
            search.value = second.id
            list_view = app.query_one("#task-browser-list", ListView)
            await wait_until(
                lambda: f"task-row-{second.id}" in {item.id for item in list_view.children},
                timeout=2,
            )
            list_view.focus()
            await pilot.press("down")
            await wait_until(
                lambda: _rendered_text(identity) == f"{second.id} · loading selected task",
                timeout=2,
            )
            _finish_report_read(report_paths[first.id], writer_descriptors, report_text)
            await wait_until(
                lambda: _open_report_writer(writer_descriptors, report_paths[second.id]), timeout=2
            )
            _finish_report_read(report_paths[second.id], writer_descriptors, report_text)
            await wait_until(lambda: second.title in _rendered_text(identity), timeout=2)
            assert first.title not in _rendered_text(identity)
    finally:
        for report_path in report_paths.values():
            _finish_report_read(report_path, writer_descriptors, report_text)
        reader.close()


async def test_home_selection_clears_rendered_task_sections_while_next_report_loads(
    database: Database,
    connection: Connection,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = SystemClock()
    first_request = "First task request evidence"
    second_request = "Second task request evidence"
    first = seed_task(connection, clock, title="First loaded task", request=first_request)
    second = seed_task(connection, clock, title="Second loading task", request=second_request)
    first_report = (
        '{"title":"First task report evidence","mode":"implement",'
        '"complexity":"normal","acceptance_criteria":[],"references":[]}'
    )
    second_report = (
        '{"title":"Second task report evidence","mode":"implement",'
        '"complexity":"normal","acceptance_criteria":[],"references":[]}'
    )
    report_paths = {
        task.id: turns_dir(project_dir, task.id) / "01-triage-i0" / REPORT_FILENAME
        for task in (first, second)
    }
    for report_path in report_paths.values():
        report_path.parent.mkdir(parents=True)
        (report_path.parent / REPORT_KIND_FILENAME).write_text(ReportKind.TRIAGE.value)
    report_paths[first.id].write_text(first_report)
    os.mkfifo(report_paths[second.id])
    writer_descriptors: dict[Path, int] = {}
    TaskStore(connection, clock).set_holds(first.id, frozenset({Hold.PAUSED}))
    reader = StateReader(database, clock)
    try:
        app = _app(reader, project_dir, widget_names=["queue"])
        async with app.run_test(size=(120, 40)) as pilot:
            identity = app.query_one("#task-detail-identity", Static)
            await wait_until(lambda: first.title in _rendered_text(identity), timeout=2)
            await wait_until(
                lambda: (
                    "First task report evidence"
                    in _rendered_text(app.query_one(f"#{TASK_DETAIL_REPORT}", Static))
                ),
                timeout=2,
            )
            assert first_request in "\n".join(_rendered_task_detail_sections(app))
            assert Hold.PAUSED.value in "\n".join(_rendered_task_detail_sections(app))

            search = app.query_one("#task-browser-search", Input)
            search.value = second.id
            list_view = app.query_one("#task-browser-list", ListView)
            await wait_until(
                lambda: f"task-row-{second.id}" in {item.id for item in list_view.children},
                timeout=2,
            )
            list_view.focus()
            await pilot.press("down")
            await wait_until(
                lambda: _rendered_text(identity) == f"{second.id} · loading selected task",
                timeout=2,
            )
            await wait_until(
                lambda: _open_report_writer(writer_descriptors, report_paths[second.id]), timeout=2
            )
            task_sections = "\n".join(_rendered_task_detail_sections(app))
            assert first_request not in task_sections
            assert "First task report evidence" not in task_sections
            assert Hold.PAUSED.value not in task_sections

            _finish_report_read(report_paths[second.id], writer_descriptors, second_report)
            await wait_until(lambda: second.title in _rendered_text(identity), timeout=2)
            task_sections = "\n".join(_rendered_task_detail_sections(app))
            assert second_request in task_sections
            assert "Second task report evidence" in task_sections

            app.query_one("#task-detail-panel", TaskDetailPanel).select_task(None)
            await wait_until(
                lambda: _rendered_text(identity) == "Select a task to inspect it.", timeout=2
            )
            assert not any(_rendered_task_detail_sections(app))
    finally:
        _finish_report_read(report_paths[second.id], writer_descriptors, second_report)
        reader.close()


async def test_default_dashboard_widgets_fit_a_narrow_system_pane(
    database: Database, connection: Connection, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = SystemClock()
    task = seed_task(connection, clock, title="Visible narrow task", mode=Mode.IMPLEMENT)
    TaskStore(connection, clock).set_slot(task.id, 0)
    TaskStore(connection, clock).apply_transition(task.id, first_step(task))
    reader = StateReader(database, SystemClock())
    settings = load_settings(project_dir)
    try:
        app = _app(reader, project_dir, widget_names=settings.dashboard.widgets)
        async with app.run_test(size=(30, 24)) as pilot:
            await pilot.pause()
            main = app.query_one("#focus-main")
            assert main.size.height > 0
            queue = app.query_one("#queue")
            usage = app.query_one("#usage")
            resources = app.query_one("#resources")
            assert queue.region.y >= main.region.y
            assert usage.region.width > 0
            assert resources.region.width > 0
            identity = app.query_one("#task-detail-identity", Static)
            await wait_until(lambda: task.id in _rendered_text(identity), timeout=2)
            await _choose_focus_view(app, pilot, "Workflow · Strips")
            strips = app.screen.query_one(StripsPanel)
            assert task.id in _rendered_text(strips)
    finally:
        reader.close()


@pytest.mark.parametrize("size", [(36, 20), (48, 24), (120, 40)])
async def test_dashboard_compact_footer_and_persisted_state_are_readable_without_color(
    database: Database,
    connection: Connection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    size: tuple[int, int],
) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    project_dir = write_minimal_project(tmp_path)
    task_id = _seed_paused_escalated_task(connection)
    reader = StateReader(database, SystemClock())
    try:
        app = _app(reader, project_dir, widget_names=["strips"])
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            footer = app.query_one("#focus-footer")
            assert app.focused is footer
            assert footer.region.height >= 2
            assert footer.region.bottom <= app.size.height
            await _choose_focus_view(app, pilot, "Workflow · Strips")
            strips = app.screen.query_one(StripsPanel)
            strip_text = _rendered_text(strips)
            assert task_id in strip_text
            assert "Escalated" in strip_text
            workflow_screen = app.screen
            await pilot.press("escape")
            await wait_until(lambda: app.screen is not workflow_screen, timeout=2)
            home_screen = app.screen
            await pilot.press("?")
            await pilot.pause()
            assert app.screen is not home_screen
            assert "Focus" in _rendered_text(
                app.screen.query_one("#workspace-help-context", Static)
            )
            await pilot.press("escape")
            await pilot.pause()
            assert app.screen is home_screen
            assert app.is_running
            app.exit()
            await wait_until(
                lambda: not app.is_running,
                timeout=_APP_SHUTDOWN_TIMEOUT_SECONDS,
            )
    finally:
        reader.close()


async def test_dashboard_activity_widget_is_openable_by_keyboard_and_back(
    database: Database,
    connection: Connection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del connection
    monkeypatch.setenv("NO_COLOR", "1")
    project_dir = write_minimal_project(tmp_path)
    reader = StateReader(database, SystemClock())
    try:
        app = _app(reader, project_dir, widget_names=["header", "dialogue", "usage"])
        async with app.run_test(size=(36, 20)) as pilot:
            home_screen = app.screen
            await _choose_focus_view(app, pilot, "Activity · Dialogue")
            assert app.screen is not home_screen
            dialogue_table = cast(
                "DataTable[object]", app.screen.query_one("#dialogue-table", DataTable)
            )
            assert dialogue_table.region.height > 0
            await pilot.press("escape")
            await wait_until(lambda: app.screen is home_screen, timeout=2)

            await pilot.press("?")
            await pilot.pause()
            assert app.screen is not home_screen
            assert app.screen.query_one("#workspace-help-copy", Static).region.height > 0
            assert "Focus" in _rendered_text(
                app.screen.query_one("#workspace-help-context", Static)
            )
            await pilot.press("escape")
            await pilot.pause()
            assert app.screen is home_screen
    finally:
        reader.close()


async def test_configured_usage_and_docker_views_remain_reachable(
    database: Database, connection: Connection, tmp_path: Path
) -> None:
    del connection
    project_dir = write_minimal_project(tmp_path)
    clock = SystemClock()
    reader = StateReader(database, clock)

    class CachedResources(StubServices):
        def __init__(self) -> None:
            super().__init__()
            self.resource_observation = ResourceView(37.0, 50.0, 2.0, 1.0, 0.25)
            self.resource_reads = 0

        async def resources(self) -> ResourceView:
            self.resource_reads += 1
            assert self.resource_observation is not None
            return self.resource_observation

    services = CachedResources()
    try:
        app = _app(
            reader,
            project_dir,
            widget_names=["usage", "resources", "docker"],
            services=services,
            clock=clock,
        )
        async with app.run_test(size=(120, 40)) as pilot:
            home_screen = app.screen
            await wait_until(
                lambda: "CPU 37%" in _rendered_text(app.query_one("#resources", Static)),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            assert services.resource_reads == 1

            assert await pilot.click("#usage")
            await wait_until(lambda: app.screen is not home_screen, timeout=2)
            usage = app.screen.query_one("#usage", Static)
            assert "Token values unknown" in plain_text(usage.content)
            await pilot.press("escape")
            await wait_until(lambda: app.screen is home_screen, timeout=2)

            assert await pilot.click("#resources")
            await wait_until(lambda: app.screen is not home_screen, timeout=2)
            resources = app.screen.query_one("#resources", Static)
            assert "CPU 37%" in _rendered_text(resources)
            assert services.resource_reads == 1
            await pilot.press("escape")
            await wait_until(lambda: app.screen is home_screen, timeout=2)

            await _choose_focus_view(app, pilot, "Diagnostics · Docker")
            docker = app.screen.query_one("#docker", Static)
            assert docker.region.width > 0
            assert _rendered_text(docker)
    finally:
        reader.close()


async def test_dashboard_mouse_navigation_opens_external_widget(
    database: Database,
    connection: Connection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del connection
    entry_points = registry_module.entry_points

    def entry_points_with_extension(*, group: str) -> list[object]:
        registered = entry_points(group=group)
        if group == WIDGET_ENTRY_POINT_GROUP:
            return [*registered, _ExternalEntryPoint()]
        return list(registered)

    monkeypatch.setattr(registry_module, "entry_points", entry_points_with_extension)
    project_dir = write_minimal_project(tmp_path)
    reader = StateReader(database, SystemClock())
    try:
        app = _app(reader, project_dir, widget_names=["usage", _EXTERNAL_WIDGET_NAME])
        async with app.run_test(size=(72, 30)) as pilot:
            workspace_screen = app.screen
            await pilot.press("v")
            await wait_until(lambda: bool(app.screen.query("#focus-views-list")), timeout=2)
            await _choose_focus_view(app, pilot, "Extensions · Outside-Widget", mouse=True)
            assert app.screen is not workspace_screen
            extension_panel = app.screen.query_one("#external-widget-panel", Static)
            assert extension_panel.region.width > 0
            await pilot.press("escape")
            await wait_until(lambda: app.screen is workspace_screen, timeout=2)

            await pilot.press("?")
            await pilot.pause()
            assert app.screen is not workspace_screen
            assert app.screen.query_one("#workspace-help-copy", Static).region.height > 0
            assert await pilot.click("#workspace-help-close", offset=(6, 1))
            await pilot.pause()
            assert app.screen is workspace_screen
    finally:
        reader.close()


async def test_home_views_mouse_opens_selected_task_actions_and_help(
    database: Database,
    connection: Connection,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = SystemClock()
    task = seed_task(connection, clock, title="Home contextual action", mode=Mode.IMPLEMENT)
    tasks = TaskStore(connection, clock)
    tasks.set_slot(task.id, 0)
    tasks.apply_transition(task.id, first_step(task))
    reader = StateReader(database, SystemClock())
    queue = CommandQueue(connection, clock)
    pending_before = queue.pending()
    apps: list[DashboardApp] = []

    async def open_palette_context(request: PaletteRequest) -> None:
        palette = _palette_screen_for_app(reader, connection, project_dir, clock, request)
        await apps[0].push_screen(palette)

    try:
        app = _app(
            reader,
            project_dir,
            widget_names=["queue"],
            open_palette_context=open_palette_context,
        )
        apps.append(app)
        async with app.run_test(size=(120, 40)) as pilot:
            home_screen = app.screen
            identity = app.query_one(f"#{TASK_DETAIL_IDENTITY}", Static)
            await wait_until(
                lambda: task.title in _rendered_text(identity),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )

            await pilot.press("v")
            await wait_until(lambda: bool(app.screen.query("#focus-views-list")), timeout=2)
            await _choose_focus_view(app, pilot, "Operator actions", mouse=True)
            await wait_until(lambda: isinstance(app.screen, PaletteScreen), timeout=2)
            palette = cast("PaletteScreen", app.screen)
            assert palette.query_one("#task-id", Input).value == task.id
            assert cast("Select[str]", palette.query_one("#role", Select)).value == (
                Role.IMPLEMENTER.value
            )
            assert queue.pending() == pending_before

            await pilot.press("escape")
            await wait_until(lambda: app.screen is home_screen, timeout=2)
            await pilot.press("v")
            await wait_until(lambda: bool(app.screen.query("#focus-views-list")), timeout=2)
            await _choose_focus_view(app, pilot, "Help", mouse=True)
            help_context = app.screen.query_one("#workspace-help-context", Static)
            await wait_until(
                lambda: task.id in _rendered_text(help_context),
                timeout=2,
            )
            assert task.title in _rendered_text(identity)
            assert queue.pending() == pending_before
            await pilot.click("#workspace-help-close")
            await wait_until(lambda: app.screen is home_screen, timeout=2)
    finally:
        reader.close()


async def test_ctrl_p_pushes_the_shared_operator_palette(
    connection: Connection, database: Database, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(_TEST_START)
    reader = StateReader(database, SystemClock())
    settings = load_settings(project_dir)
    queue = CommandQueue(connection, clock)
    commands = OperatorCommands(queue, clock, engine_up=lambda: True)
    queries = OperatorQueries(
        reader,
        project=settings.project.name,
        project_root=project_dir,
        engine_up=lambda: False,
        max_parallel=settings.orchestration.max_parallel,
        clock=clock,
    )
    opened: list[str] = []
    app_instance: DashboardApp | None = None

    async def open_palette(action: str) -> None:
        opened.append(action)
        if app_instance is None:
            message = "dashboard app has not been initialized"
            raise RuntimeError(message)
        await app_instance.push_screen(
            PaletteScreen(
                commands,
                queries,
                _unexpected_pane_selection,
                on_change=reader.watch,
            )
        )

    try:
        app_instance = _app(reader, project_dir, widget_names=[], open_palette=open_palette)
        async with app_instance.run_test(size=(72, 32)) as pilot:
            await pilot.press("ctrl+p")
            await pilot.pause()

            assert isinstance(app_instance.screen, PaletteScreen)
            assert opened == [""]
            palette = cast("PaletteScreen", app_instance.screen)
            palette.query_one("#request", TextArea).text = "keep the dashboard available"
            await pilot.click("#palette-submit")
            await wait_until(clock.has_pending_sleep, timeout=2)
            queued = queue.pending()
            assert len(queued) == 1

            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app_instance.screen, PaletteScreen)
            await pilot.press("ctrl+p")
            await pilot.pause()
            assert isinstance(app_instance.screen, PaletteScreen)
            assert opened == ["", ""]
            queue.mark_done(queued[0].id, {"task_id": "T-2"})
            clock.advance(0.25)
    finally:
        reader.close()


async def test_ctrl_p_carries_the_selected_browser_task(
    database: Database,
    connection: Connection,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    task = seed_task(connection, SystemClock(), title="Selected browser task")
    reader = StateReader(database, SystemClock())
    opened: list[PaletteRequest] = []

    async def open_palette_context(request: PaletteRequest) -> None:
        opened.append(request)

    try:
        app = _app(
            reader,
            project_dir,
            widget_names=["queue"],
            open_palette_context=open_palette_context,
        )
        async with app.run_test(size=(72, 32)) as pilot:
            await pilot.pause()
            await pilot.press("ctrl+p")
            await pilot.pause()

            assert len(opened) == 1
            assert opened[0].task_id == task.id
    finally:
        reader.close()


async def test_dashboard_app_rejects_an_ambiguous_widget_name(
    database: Database,
    connection: Connection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del connection
    monkeypatch.setattr(
        registry_module, "entry_points", ambiguous_entry_points("dup", "dist-one", "dist-two")
    )
    project_dir = write_minimal_project(tmp_path)
    reader = StateReader(database, SystemClock())
    try:
        app = _app(reader, project_dir, widget_names=["dup"])
        with pytest.raises(WidgetPluginError) as excinfo:
            async with app.run_test():
                pass
        assert "dist-two" in str(excinfo.value)
    finally:
        reader.close()
