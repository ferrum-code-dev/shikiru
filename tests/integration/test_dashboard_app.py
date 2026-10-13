from __future__ import annotations

import errno
import os
import re
from datetime import UTC, datetime
from html import unescape
from itertools import pairwise
from typing import TYPE_CHECKING, cast

import psutil
import pytest
from rich.cells import cell_len
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.events import MouseScrollDown
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
    BILLING_MODE_UNKNOWN,
    BILLING_SOURCE_OPERATOR_CONFIG,
    BILLING_SOURCE_PROVIDER_REPORTED,
    COST_BASIS_API_EQUIVALENT,
    COST_BASIS_BILLED,
    COST_BASIS_UNKNOWN,
    BillingDeclaration,
    Rung,
    Usage,
)
from shikiru.bootstrap import dashboard_tmux_binding
from shikiru.clock import SystemClock
from shikiru.config.loader import load_settings
from shikiru.dashboard.app import (
    COMMAND_HISTORY_NEXT_SHORTCUT,
    COMMAND_HISTORY_PREVIOUS_SHORTCUT,
    DashboardApp,
    WidgetPluginError,
)
from shikiru.dashboard.contract import (
    WIDGET_ENTRY_POINT_GROUP,
    CommandFeedbackStatus,
    DiagnosticSampleState,
    PaletteDraft,
    PaletteRequest,
    ResourceView,
    TaskPaletteRoute,
    TaskTimelineCategory,
)
from shikiru.dashboard.detail import (
    TASK_DETAIL_BLOCKER,
    TASK_DETAIL_CRITERIA,
    TASK_DETAIL_EVIDENCE,
    TASK_DETAIL_EVIDENCE_INLINE_BUTTON,
    TASK_DETAIL_FACTS,
    TASK_DETAIL_IDENTITY,
    TASK_DETAIL_MORE_RELATIONSHIPS,
    TASK_DETAIL_NEXT_PAGE_KEY,
    TASK_DETAIL_OWNERSHIP,
    TASK_DETAIL_PREVIOUS_PAGE_KEY,
    TASK_DETAIL_PREVIOUS_RELATIONSHIPS,
    TASK_DETAIL_PROGRESS,
    TASK_DETAIL_RELATIONSHIP_PAGE,
    TASK_DETAIL_REPORT,
    TASK_DETAIL_REQUEST,
    TASK_DETAIL_SESSIONS,
    TASK_DETAIL_SITUATION,
    TASK_DETAIL_TIMELINE_NEWER,
    TASK_DETAIL_TIMELINE_OLDER,
    TASK_DETAIL_USAGE_INLINE_BUTTON,
    TASK_DETAIL_VERDICT,
    TASK_DETAIL_VIEWS_OPEN_BUTTON,
    TASK_DETAIL_WORKSPACE,
    TASK_VIEW_EVIDENCE,
    TASK_VIEW_RELATIONSHIPS,
    TASK_VIEW_USAGE,
    TaskDetailBody,
    TaskDetailPanel,
    TaskDetailScreen,
)
from shikiru.dashboard.palette import PaletteScreen
from shikiru.domain.commands import (
    AnswerResolution,
    CancelResolution,
    Prioritize,
    ResolutionKind,
    Resolve,
    RetryResolution,
    Say,
    Stop,
)
from shikiru.domain.escalation import Escalation, EscalationStatus, options_for
from shikiru.domain.events import EventKind
from shikiru.domain.reports import (
    REPORT_FILENAME,
    REPORT_KIND_FILENAME,
    ReportKind,
)
from shikiru.domain.scheduling import WaitReason
from shikiru.domain.steps import Step, Transition, first_step
from shikiru.domain.tasks import EscalationReason, Hold, Mode, Role, Status, Task
from shikiru.engine.escalations import PacketContext, render_packet
from shikiru.operator.commands import OperatorCommands
from shikiru.operator.queries import OperatorQueries
from shikiru.paths import turns_dir
from shikiru.plugins.widgets.escalations import EscalationsPanel
from shikiru.plugins.widgets.strips import StripsPanel
from shikiru.process import (
    identify_pid,
)
from shikiru.store.commands import CommandQueue
from shikiru.store.escalations import EscalationStore
from shikiru.store.events import EventLog
from shikiru.store.reader import StateReader
from shikiru.store.runs import RunStore
from shikiru.store.tasks import TaskStore
from shikiru.telemetry.resources import ResourceProcessRoots, ResourceSampler
from shikiru.tmux.client import Tmux, exact_target, tmux_key_label
from shikiru.tmux.session import PROJECT_DIR_ENV_VAR
from shikiru.ui.compact import format_cpu_pct
from tests.support.clock import FakeClock
from tests.support.config import write_minimal_project
from tests.support.dashboard import StubServices, ignore_palette_action, state_views
from tests.support.entry_points import FakeDistribution
from tests.support.render import plain_text
from tests.support.resource_agent import resource_services, start_resource_agent
from tests.support.store import seed_task
from tests.support.tmux import attach_tmux_client
from tests.support.waiting import wait_until

_TEST_START = datetime(2026, 1, 1, tzinfo=UTC)
_APP_SHUTDOWN_TIMEOUT_SECONDS = 2
_DASHBOARD_WAIT_TIMEOUT_SECONDS = 2
_COMPACT_RESOLUTION_CONTEXT_SCROLL_ROWS = 5
_COMMAND_FEEDBACK_CLOCK_ADVANCE_SECONDS = 120
_COMMAND_HISTORY_INITIAL_SIZE = (100, 34)
COMMAND_HISTORY_TEST_LIMIT = 10
TASK_PAGE_TEST_EVENT_COUNT = 2
TASK_PAGE_TEST_RELATIONSHIP_COUNT = 25
TASK_PAGE_TEST_TIMELINE_PAGE_SIZE = 1
_EXTERNAL_WIDGET_NAME = "outside-widget"
_LATER_EXTERNAL_WIDGET_NAME = "later-widget"
_WIDE_HOME_SIZE = (120, 40)
_PROJECT_SESSION_NAME = "project"
_GLOBAL_TMUX_PREFIX = "C-b"
_SESSION_TMUX_PREFIX = "C-a"
_TMUX_CLIENT_TTY_FORMAT = "#{client_tty}"
_MEASURED_HOME_SIZE = (101, 35)
_HELP_GESTURE_KEY = "key"
_HELP_GESTURE_FOOTER = "footer"
_HELP_GESTURE_VIEWS = "views"
_STANDARD_HOME_SIZE = (100, 35)
_SHORT_WIDE_HOME_SIZE = (80, 24)
_STACKED_INSPECTOR_HOME_SIZE = (64, 24)
_WIDE_SHORT_HOME_SIZE = (80, 14)
_SPLIT_BOUNDARY_HOME_SIZE = (73, 24)
_STACKED_UPPER_BOUNDARY_HOME_SIZE = (72, 24)
_STACKED_LOWER_BOUNDARY_HOME_SIZE = (54, 24)
_LIST_ONLY_BOUNDARY_HOME_SIZE = (53, 24)
_COMPACT_HOME_SIZE = (48, 20)
_NARROW_TASK_SIZE = (36, 20)
_NARROW_SYSTEM_PANE_SIZE = (30, 24)
_FOOTER_EDGE_WIDTHS = (54, 120)
_SHORT_HOME_SIZE = (36, 14)
_COMPACT_SHORT_WIDE_HOME_SIZE = (48, 14)
_TINY_HOME_SIZE = (20, 8)
_PALETTE_NARROW_SAFE_SIZE = (17, 20)
_PALETTE_ZERO_WIDTH_SIZE = (16, 20)
_PALETTE_ZERO_WIDTH_REPRODUCTION_SIZES = (
    _PALETTE_NARROW_SAFE_SIZE,
    _PALETTE_ZERO_WIDTH_SIZE,
    _PALETTE_NARROW_SAFE_SIZE,
)
_HOME_REVIEW_SIZES = (
    _MEASURED_HOME_SIZE,
    _WIDE_HOME_SIZE,
    _SHORT_WIDE_HOME_SIZE,
    _COMPACT_HOME_SIZE,
)
_MIN_WIDE_TASK_LIST_ROWS = 9
_MIN_SHORT_WIDE_TASK_LIST_ROWS = 5
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
_TIMELINE_REFRESH_SECONDS = 1
_FORM_CONTENT_TRAILING_GAP_ROWS = 3


class _ExternalWidget:
    name = _EXTERNAL_WIDGET_NAME

    def create(self, context: WidgetContext) -> Widget:
        result_rows = tuple(
            entry.result
            for entry in context.views.command_history().entries
            if entry.result is not None
        )
        return Static("\n".join(result_rows), id="external-widget-panel", markup=False)


class _ExternalEntryPoint:
    name = _EXTERNAL_WIDGET_NAME
    dist = FakeDistribution("test-extension")

    def load(self) -> type[_ExternalWidget]:
        return _ExternalWidget


class _LaterExternalWidget:
    name = _LATER_EXTERNAL_WIDGET_NAME

    def create(self, context: WidgetContext) -> Widget:
        del context
        return Static(id="later-external-widget-panel")


class _LaterExternalEntryPoint:
    name = _LATER_EXTERNAL_WIDGET_NAME
    dist = FakeDistribution("later-test-extension")

    def load(self) -> type[_LaterExternalWidget]:
        return _LaterExternalWidget


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


def _rendered_text(widget: Static | Button) -> str:
    content = widget.label if isinstance(widget, Button) else widget.content
    return plain_text(content)


def _rendered_widget_lines(widget: Static) -> tuple[str, ...]:
    lines = widget.render_lines(Region(0, 0, widget.region.width, widget.region.height))
    return tuple(line.text.rstrip() for line in lines if line.text.strip())


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


def _widget_text(widget: Static) -> str:
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


def _exported_screen_text(app: DashboardApp) -> str:
    svg = app.export_screenshot()
    text_elements = re.findall(r"<text\b[^>]*>(.*?)</text>", svg, flags=re.DOTALL)
    text = " ".join(unescape(content).replace("\xa0", " ") for content in text_elements)
    return " ".join(text.split())


def _rendered_palette_header_text(app: DashboardApp) -> str:
    elements: list[tuple[str, str]] = []
    for attributes, content in re.findall(
        r"<text\b([^>]*)>(.*?)</text>", app.export_screenshot(), re.DOTALL
    ):
        y = re.search(r'\by="([^"]+)"', attributes)
        label = " ".join(unescape(content).replace("\xa0", " ").split())
        if y is not None and label:
            elements.append((y.group(1), label))
    close_row = next((y for y, label in elements if "Esc \u00d7" in label), None)
    assert close_row is not None
    return " ".join(label for y, label in elements if y == close_row)


async def _assert_rendered_palette_header_identity(
    app: DashboardApp,
    task_id: str,
    escalation_id: str,
) -> None:
    await wait_until(
        lambda: (
            task_id in _rendered_palette_header_text(app)
            and escalation_id in _rendered_palette_header_text(app)
            and "Esc \u00d7" in _rendered_palette_header_text(app)
        ),
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    header = _rendered_palette_header_text(app)
    assert task_id in header
    assert escalation_id in header
    assert "Esc \u00d7" in header


def _rendered_visible_home_origins(app: DashboardApp) -> dict[str, tuple[float, float]]:
    elements: list[tuple[str, float, float]] = []
    for attributes, content in re.findall(
        r"<text\b([^>]*)>(.*?)</text>", app.export_screenshot(), re.DOTALL
    ):
        label = unescape(content).replace("\xa0", " ").strip()
        x = re.search(r'\bx="([^"]+)"', attributes)
        y = re.search(r'\by="([^"]+)"', attributes)
        if label and x is not None and y is not None:
            elements.append((label, float(x.group(1)), float(y.group(1))))

    origins: dict[str, tuple[float, float]] = {}
    for anchor in ("SHIKIRU", "TASKS"):
        origin = next(((x, y) for text, x, y in elements if text == anchor), None)
        if origin is not None:
            origins[anchor] = origin
    return origins


def _assert_last_command_history_page(page: Static) -> None:
    page_text = _rendered_text(page)
    assert "page 3" in page_text
    assert "rows 41-52 of 52" in page_text


async def _assert_popup_close_reachable(
    screen: Widget,
    *,
    close_selector: str,
    frame_selector: str,
    body_selector: str | None,
) -> None:
    close = screen.query_one(close_selector, Button)
    frame = screen.query_one(frame_selector)
    body = screen.query_one(body_selector, VerticalScroll) if body_selector is not None else None
    await wait_until(
        lambda: (
            close.region.width > 0
            and close.region.height > 0
            and frame.region.contains_region(close.region)
            and frame.content_region.contains_region(close.region)
            and screen.region.contains_region(close.region)
            and (body is None or not body.scrollable_content_region.contains_region(close.region))
        ),
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )


def _settings_without_animations(project_dir: Path) -> Settings:
    settings = load_settings(project_dir)
    dashboard = settings.dashboard.model_copy(
        update={
            "animations": False,
            "timeline_refresh_seconds": _TIMELINE_REFRESH_SECONDS,
        }
    )
    return settings.model_copy(update={"dashboard": dashboard})


async def _select_home_task(app: DashboardApp, task_id: str) -> None:
    row = app.query_one(f"#task-row-{task_id}", ListItem)
    task_list = cast("ListView", row.parent)
    index = tuple(task_list.children).index(row)
    task_list.focus()
    task_list.index = index
    await wait_until(
        lambda: (
            task_list.highlighted_child is row
            and task_id in _widget_text(app.query_one(f"#{TASK_DETAIL_IDENTITY}", Static))
        ),
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

    from textual.pilot import Pilot
    from textual.widget import Widget

    from shikiru.clock import Clock
    from shikiru.config.settings import Settings
    from shikiru.dashboard.contract import OpenPalette, OpenPaletteContext, WidgetContext
    from shikiru.dashboard.services import DashboardServices
    from shikiru.store.database import Database
    from tests.support.store import Connection
    from tests.support.tmux import TmuxServer


def _open_agent_error_escalation(
    connection: Connection,
    clock: Clock,
    project_dir: Path,
    task: Task,
    *,
    step: Step | None,
    headline: str,
    packet_name: str,
    question: str,
    attempted: str,
) -> Escalation:
    reason = EscalationReason.AGENT_ERROR
    packet_path = project_dir / ".shikiru" / "escalations" / packet_name
    packet_path.parent.mkdir(parents=True, exist_ok=True)
    packet_path.write_text(
        render_packet(
            PacketContext(
                task_id=task.id,
                step=step,
                reason=reason,
                timeline=(),
                attempted=attempted,
                reports="",
                facts="",
                view_tail="",
                sessions="",
                question=question,
                options=options_for(reason),
            )
        )
    )
    return EscalationStore(connection, clock).open(
        task.id,
        step=step,
        reason=reason,
        headline=headline,
        packet_path=str(packet_path.relative_to(project_dir)),
        options=options_for(reason),
    )


def _assert_non_answer_resolution_form(
    screen: PaletteScreen,
    task_id: str,
    escalation_id: str,
    question: str,
) -> Select[str]:
    assert screen.query_one("#field-escalation").display
    assert screen.query_one("#field-resolution").display
    assert screen.query_one("#field-resolution-text").display is False
    assert cast("Select[str]", screen.query_one("#escalation", Select)).value == escalation_id
    summary = _rendered_text(screen.query_one("#palette-escalation-summary", Static))
    assert task_id in summary
    assert escalation_id in summary
    assert question in summary
    assert ResolutionKind.RETRY.value in summary
    assert ResolutionKind.CANCEL.value in summary
    return cast("Select[str]", screen.query_one("#resolution", Select))


def _assert_missing_packet_resolution_form(
    screen: PaletteScreen,
    task_id: str,
    escalation: Escalation,
) -> tuple[Select[str], Select[str]]:
    assert screen.query_one("#task-id", Input).value == task_id
    target = cast("Select[str]", screen.query_one("#escalation", Select))
    assert target.value == escalation.id
    resolution = cast("Select[str]", screen.query_one("#resolution", Select))
    assert resolution.display
    assert resolution.value == ResolutionKind.RETRY.value
    summary = _rendered_text(screen.query_one("#palette-escalation-summary", Static))
    assert summary.count(task_id) == 1
    assert escalation.id in summary
    assert "Retry the failed step." in summary
    assert "Question: unavailable" in summary
    assert "Agent alternatives" in summary
    assert "unavailable" in summary
    assert ResolutionKind.CANCEL.value in summary
    evidence_error = _rendered_text(screen.query_one("#palette-evidence-error", Static))
    assert "Evidence error:" in evidence_error
    assert "missing-resolution-packet.md" in evidence_error
    return target, resolution


async def _capture_compact_resolution_context(
    app: DashboardApp,
    target: Select[str],
    task_id: str,
    escalation_id: str,
) -> None:
    form = app.screen.query_one("#palette-form", VerticalScroll)
    form.scroll_to(y=0, animate=False, immediate=True)
    await wait_until(lambda: form.scroll_y == 0, timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS)
    target.focus()
    await wait_until(lambda: app.focused is target, timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS)
    form.scroll_to(
        y=_COMPACT_RESOLUTION_CONTEXT_SCROLL_ROWS,
        animate=False,
        immediate=True,
    )
    await wait_until(
        lambda: form.scroll_y == _COMPACT_RESOLUTION_CONTEXT_SCROLL_ROWS,
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    await wait_until(
        lambda: (
            "Question: unavailable" in _exported_screen_text(app)
            and "Agent alternatives" in _exported_screen_text(app)
        ),
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    await _assert_rendered_palette_header_identity(app, task_id, escalation_id)


async def _exercise_native_resolution_select(
    app: DashboardApp,
    pilot: Pilot[None],
    resolution: Select[str],
    queue: CommandQueue,
) -> None:
    resolution.focus()
    await wait_until(lambda: app.focused is resolution, timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS)
    await pilot.press("enter")
    await wait_until(lambda: resolution.expanded, timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS)
    assert queue.pending() == []
    await pilot.press("escape")
    await wait_until(
        lambda: not resolution.expanded and app.focused is resolution,
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    assert queue.pending() == []


async def _assert_compact_resolution_controls(
    app: DashboardApp,
    screen: PaletteScreen,
    size: tuple[int, int],
    queue: CommandQueue,
    *,
    task_id: str,
    escalation_id: str,
) -> Input:
    frame = screen.query_one("#palette-frame")
    for button_id in ("palette-back-to-menu", "palette-submit"):
        button = screen.query_one(f"#{button_id}", Button)
        assert button.display
        assert frame.content_region.contains_region(button.region)
        assert button.region.bottom <= size[1]
    retry_rung = screen.query_one("#resolution-rung", Input)
    retry_rung.focus()
    screen.query_one("#palette-form", VerticalScroll).scroll_to_widget(
        screen.query_one("#field-palette-evidence-error"),
        animate=False,
        immediate=True,
        force=True,
    )
    await wait_until(
        lambda: app.focused is retry_rung and "Evidence error:" in _exported_screen_text(app),
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    await _assert_rendered_palette_header_identity(app, task_id, escalation_id)
    assert queue.pending() == []
    return retry_rung


async def _unexpected_pane_selection(pane_id: str) -> None:
    del pane_id
    raise AssertionError


def _palette_screen_for_app(
    reader: StateReader,
    connection: Connection,
    project_dir: Path,
    clock: Clock,
    request: PaletteRequest | None = None,
    *,
    remember_contextual_draft: Callable[[PaletteDraft], None] | None = None,
    task_routes_available: bool = False,
    project_diagnostics_available: bool = False,
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
        remember_contextual_draft=remember_contextual_draft,
        task_routes_available=task_routes_available,
        project_diagnostics_available=project_diagnostics_available,
    )


def _palette_context_opener(
    reader: StateReader,
    connection: Connection,
    project_dir: Path,
    clock: Clock,
    app_instances: list[DashboardApp],
) -> OpenPaletteContext:
    async def open_palette_context(request: PaletteRequest) -> None:
        app = app_instances[0]
        palette = _palette_screen_for_app(
            reader,
            connection,
            project_dir,
            clock,
            request,
            remember_contextual_draft=app.remember_contextual_palette_draft,
            task_routes_available=True,
        )
        await app.push_screen(
            palette,
            callback=lambda _: app.refresh_task_detail_after_resume(),
        )

    return open_palette_context


def _assert_fixed_context_target_picker_hidden(screen: PaletteScreen) -> None:
    assert not screen.query_one("#field-task-search").display
    assert not screen.query_one("#field-task-choice").display
    assert not screen.query_one("#field-task-id").display
    assert not screen.query_one("#field-resolution").display


def _assert_attention_answer_target(
    screen: PaletteScreen,
    task_id: str,
    escalation_id: str,
    headline: str,
) -> None:
    assert screen.query_one("#task-id", Input).value == task_id
    assert not screen.query_one("#field-escalation").display
    assert screen.query_one("#field-palette-escalation-summary").display
    assert screen.query_one("#field-palette-evidence-error").display
    assert screen.query_one("#field-resolution-text").display
    assert not screen.query_one("#field-role").display
    _assert_fixed_context_target_picker_hidden(screen)
    summary = _rendered_text(screen.query_one("#palette-escalation-summary", Static))
    assert task_id in summary
    assert escalation_id in summary
    assert headline in summary
    assert "Question: unavailable" in summary


async def _enter_answer_text(
    pilot: Pilot[None],
    palette: PaletteScreen,
    guidance: str,
) -> None:
    answer = palette.query_one("#resolution-text", TextArea)
    await wait_until(
        lambda: pilot.app.focused is answer,
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    await pilot.press(*guidance)
    assert answer.text == guidance


async def _open_attention_answer_form(
    app: DashboardApp,
    pilot: Pilot[None],
    row: Static,
) -> PaletteScreen:
    await pilot.click(row)
    await wait_until(
        lambda: isinstance(app.screen, PaletteScreen),
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    return cast("PaletteScreen", app.screen)


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
    tmux_detach_hint: str | None = None,
    detach_tmux_client: Callable[[], Awaitable[bool]] | None = None,
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
        disk_refresh_seconds=settings.dashboard.disk_refresh_seconds,
        quota_warn_pct=settings.dashboard.gauges.quota_warn_pct,
        quota_alert_pct=settings.dashboard.gauges.quota_alert_pct,
        animations=settings.dashboard.animations,
        open_palette=open_palette,
        widget_names=widget_names,
        open_palette_context=open_palette_context,
        timeline_refresh_seconds=settings.dashboard.timeline_refresh_seconds,
        tmux_detach_hint=tmux_detach_hint,
        detach_tmux_client=detach_tmux_client,
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
    await _assert_popup_close_reachable(
        views_screen,
        close_selector="#context-views-close",
        frame_selector="#context-views-frame",
        body_selector="#focus-views-list",
    )
    items = [item for item in view_list.children if isinstance(item, ListItem)]
    assert view_list.region.height <= sum(item.region.height for item in items)
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


@pytest.mark.parametrize("widget_name", ["no-such-widget", _InvalidPlacementWidget.name])
async def test_dashboard_app_rejects_an_invalid_widget_config_naming_the_widget(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    widget_name: str,
) -> None:
    del connection
    entry_points = registry_module.entry_points

    def entry_points_with_extension(*, group: str) -> list[object]:
        registered = entry_points(group=group)
        if group == WIDGET_ENTRY_POINT_GROUP:
            return [*registered, _InvalidPlacementEntryPoint()]
        return list(registered)

    monkeypatch.setattr(registry_module, "entry_points", entry_points_with_extension)
    app = _app(reader, write_minimal_project(tmp_path), widget_names=[widget_name])
    with pytest.raises(WidgetPluginError, match=widget_name):
        async with app.run_test():
            pass


@pytest.mark.parametrize("size", [_WIDE_HOME_SIZE, _NARROW_SYSTEM_PANE_SIZE])
async def test_dashboard_mounts_the_default_widgets_and_fits_the_pane(
    database: Database, connection: Connection, tmp_path: Path, size: tuple[int, int]
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
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            queue_panel = app.query_one("#queue")
            assert queue_panel.region.y >= app.query_one("#focus-main").region.y
            if size == _WIDE_HOME_SIZE:
                assert queue_panel.region.right <= app.query_one("#task-detail-panel").region.x
            identity = app.query_one("#task-detail-identity", Static)
            await wait_until(lambda: task.id in _rendered_text(identity), timeout=2)

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

            await _choose_focus_view(app, pilot, "Diagnostics · faults")
            faults = app.screen.query_one("#faults", Static)
            await wait_until(
                lambda: "host sample failed" in "\n".join(_rendered_widget_lines(faults)),
                timeout=2,
            )
            assert "\N{MULTIPLICATION SIGN}2" in "\n".join(_rendered_widget_lines(faults))
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
                        "No decision needed",
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
            assert "Engine stopped" not in visible_status
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


def _seed_primary_usage(connection: Connection, clock: Clock) -> tuple[str, RunStore]:
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


@pytest.mark.parametrize("size", [_WIDE_SHORT_HOME_SIZE, _TINY_HOME_SIZE])
async def test_home_usage_remains_reachable_at_short_and_tiny_sizes(
    database: Database,
    connection: Connection,
    tmp_path: Path,
    size: tuple[int, int],
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(_TEST_START)
    _seed_primary_usage(connection, clock)
    reader = StateReader(database, clock)
    try:
        app = _app(
            reader,
            project_dir,
            widget_names=["header", "usage"],
            services=StubServices(),
            clock=clock,
        )
        async with app.run_test(size=size):
            root = app.query_one("#focus-root", VerticalScroll)
            header = app.query_one("#header", Static)
            usage = app.query_one("#usage", Static)
            assert header.region.overlaps(root.scrollable_content_region)

            scrollable = (
                root
                if size == _TINY_HOME_SIZE
                else app.query_one("#focus-readouts", VerticalScroll)
            )
            assert scrollable.max_scroll_y > 0
            scrollable.scroll_to_widget(usage, top=True, animate=False, immediate=True)
            await wait_until(
                lambda: usage.region.overlaps(scrollable.scrollable_content_region),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
    finally:
        reader.close()


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


def _seed_project_usage(connection: Connection, clock: SystemClock) -> None:
    task = seed_task(connection, clock, title="Project totals with mixed billing")
    runs = RunStore(connection, clock)
    _record_mixed_billing(runs, task.id)
    runs.start(
        task.id,
        Role.LEADER,
        Rung("codex", "gpt-6", "high"),
        billing_declaration=BillingDeclaration(
            BILLING_MODE_UNKNOWN,
            BILLING_SOURCE_OPERATOR_CONFIG,
        ),
    )
    runs.start(task.id, Role.IMPLEMENTER, Rung("legacy-agent", "model", "low"))
    runs.start(
        task.id,
        Role.HELPER,
        Rung("provider-agent", "model", "low"),
        billing_declaration=BillingDeclaration(
            BILLING_MODE_UNKNOWN,
            BILLING_SOURCE_PROVIDER_REPORTED,
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
    clock: Clock,
    *,
    engine_pid: int | None = None,
    engine_running: bool = True,
) -> StubServices:
    settings = load_settings(project_dir)
    root_identity = identify_pid(engine_pid) if engine_pid is not None else None
    sampler = ResourceSampler(clock, settings.orchestration.resources)
    roots = ResourceProcessRoots((root_identity,) if root_identity is not None else (), ())
    resource_snapshot = sampler.sample_process_roots(roots)
    services = StubServices(engine_running=engine_running)
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
        shikiru_cpu_pct=resource_snapshot.shikiru_cpu_pct,
        shikiru_rss_gb=resource_snapshot.shikiru_rss_gb,
        agents_cpu_pct=resource_snapshot.agents_cpu_pct,
        agents_rss_gb=resource_snapshot.agents_rss_gb,
        shikiru_process_tree_empty=resource_snapshot.shikiru_process_tree_empty,
        agents_process_tree_empty=resource_snapshot.agents_process_tree_empty,
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
            "2.7k tokens" in " ".join(_rendered_widget_lines(usage))
            and attention.display
            and "Operator owns" in _rendered_text(attention_rows.query_one(Static))
            and "paused" in _rendered_text(attention_rows.query_one(Static))
        ),
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    primary = (identity, usage, resources)
    assert all(widget.region.y >= readouts.region.y for widget in primary)
    if app.size == _SHORT_WIDE_HOME_SIZE:
        for widget in primary:
            readouts.scroll_to_widget(widget, animate=False, immediate=True)
            await wait_until(
                lambda widget=widget: widget.region.bottom <= readouts.region.bottom,
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
        readouts.scroll_to(y=0, animate=False, immediate=True)
    else:
        assert readouts.scroll_y == 0
        assert all(widget.is_on_screen for widget in primary)
        assert all(widget.region.bottom <= readouts.region.bottom for widget in primary), (
            f"readouts={readouts.region}, "
            f"widgets={[(widget.id, widget.region) for widget in primary]}, "
            f"header={_rendered_widget_lines(identity)}"
        )
    assert attention.region.bottom <= app.query_one("#focus-main", Horizontal).region.y
    minimum_list_rows = (
        _MIN_SHORT_WIDE_TASK_LIST_ROWS
        if app.size == _SHORT_WIDE_HOME_SIZE
        else _MIN_WIDE_TASK_LIST_ROWS
    )
    assert app.query_one("#task-browser-list", ListView).region.height >= minimum_list_rows

    identity_text = " ".join(_rendered_widget_lines(identity))
    usage_text = " ".join(_rendered_widget_lines(usage))
    resource_text = " ".join(_rendered_widget_lines(resources))
    attention_text = " ".join(_rendered_widget_lines(attention_rows.query_one(Static)))
    assert "Engine running" in identity_text
    assert "2.7k tokens" in usage_text
    assert "$0.42 API" in usage_text
    assert "2 costs unreported" in usage_text
    assert "API-equiv. est. $0.11" not in usage_text
    assert "subscription agent" not in usage_text
    assert all(cell_len(line) <= usage.region.width for line in _rendered_widget_lines(usage))
    assert "Host" in resource_text
    assert "Shikiru process tree is empty" in resource_text
    assert "Agents process tree is empty" in resource_text
    assert "Operator owns" in attention_text


async def _assert_mixed_home_task_rows(
    app: DashboardApp,
    row_ids: tuple[tuple[str, ...], str, str, str],
    size: tuple[int, int],
) -> None:
    active_task_ids, completed_task_id, paused_task_id, owned_task_id = row_ids
    task_list = app.query_one("#task-browser-list", ListView)
    recent_list = app.query_one("#task-browser-recent-list", ListView)
    await wait_until(
        lambda: all(
            app.query_one(f"#task-row-{task_id}", ListItem).region.height > 0
            for task_id in active_task_ids
        ),
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    active_rows = tuple(
        app.query_one(f"#task-row-{task_id}", ListItem) for task_id in active_task_ids
    )
    active_gaps = tuple(
        following.region.y - current.region.bottom for current, following in pairwise(active_rows)
    )
    assert all(gap == 1 for gap in active_gaps), active_gaps
    recent_heading = app.query_one("#task-browser-recent-heading", Horizontal)
    recent_task = app.query_one(f"#task-row-{completed_task_id}", ListItem)
    assert recent_heading.display
    assert recent_heading.region.y < recent_task.region.y
    assert recent_task.parent is recent_list
    item_ids = {item.id for item in task_list.children}
    assert all(f"task-row-{task_id}" in item_ids for task_id in active_task_ids)
    selected_item = app.query_one(f"#task-row-{active_task_ids[1]}", ListItem)
    assert task_list.highlighted_child is selected_item
    if size == _SHORT_WIDE_HOME_SIZE or any(
        not item.region.overlaps(task_list.scrollable_content_region) for item in active_rows
    ):
        assert app.query_one("#focus-main", Horizontal).has_class("wide")
        if size == _SHORT_WIDE_HOME_SIZE:
            assert app.query_one("#task-detail-panel", TaskDetailPanel).display
        task_list.scroll_to_widget(active_rows[-1], animate=False, immediate=True)
        await wait_until(
            lambda: active_rows[-1].region.overlaps(task_list.scrollable_content_region),
            timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
        )
        assert task_list.highlighted_child is selected_item
    else:
        assert all(
            item.region.overlaps(task_list.scrollable_content_region) for item in active_rows
        ), (
            size,
            task_list.region,
            task_list.scrollable_content_region,
            tuple((item.id, item.region) for item in active_rows),
        )
    assert recent_task.region.bottom <= recent_list.region.bottom
    attention = app.query_one("#escalations", EscalationsPanel)
    attention_row = attention.query_one("#escalation-panels", VerticalScroll).query_one(Static)
    attention_text = " ".join(_rendered_widget_lines(attention_row))
    assert "Operator owns" in attention_text
    assert owned_task_id in attention_text
    assert paused_task_id in attention_text


@pytest.mark.parametrize(
    "size",
    [_MEASURED_HOME_SIZE, _STANDARD_HOME_SIZE, _WIDE_HOME_SIZE, _SHORT_WIDE_HOME_SIZE],
)
async def test_wide_home_keeps_usage_resources_and_attention_visible_above_the_task_list(
    database: Database,
    connection: Connection,
    tmp_path_factory: pytest.TempPathFactory,
    size: tuple[int, int],
) -> None:
    project_dir = write_minimal_project(tmp_path_factory.mktemp("p"))
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
            expected_group = "Shikiru CPU" if populated else "Shikiru process tree is empty"
            await wait_until(
                lambda: (
                    expected_group in _rendered_text(resources)
                    and "Host" in _rendered_text(resources)
                ),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            initial_width = resources.region.width
            await pilot.resize_terminal(*_STANDARD_HOME_SIZE)
            await wait_until(
                lambda: (
                    resources.region.width != initial_width
                    and expected_group in _rendered_text(resources)
                    and "Host" in _rendered_text(resources)
                    and "Agents" in _rendered_text(resources)
                ),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            await pilot.resize_terminal(*_COMPACT_HOME_SIZE)
            await wait_until(
                lambda: (
                    resources.region.width <= _COMPACT_HOME_SIZE[0]
                    and expected_group in _rendered_text(resources)
                    and "Host" in _rendered_text(resources)
                    and "Agents" in _rendered_text(resources)
                ),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            compact_lines = _rendered_widget_lines(resources)
            assert compact_lines[0].startswith("Host ")
            compact_shikiru = "Shikiru CPU" if populated else "Shikiru process tree is empty"
            assert compact_shikiru in compact_lines[1]
            assert "Agents process tree is empty" in compact_lines[2]
            assert all(cell_len(line) <= resources.region.width for line in compact_lines)
    finally:
        reader.close()


@pytest.mark.parametrize("size", [_WIDE_HOME_SIZE, _COMPACT_HOME_SIZE])
async def test_headless_agent_resources_are_disjoint_and_diagnostics_reuses_the_sample(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
    tmux_server: TmuxServer,
    size: tuple[int, int],
) -> None:
    project_dir = write_minimal_project(tmp_path)
    settings = load_settings(project_dir)
    clock = FakeClock(_TEST_START)
    task = seed_task(connection, clock, title="Read the recorded agent tree")
    runner, process, turn_processes, task_run_dir, _recorded_identity = await start_resource_agent(
        connection,
        clock,
        project_dir,
        task,
    )
    sampler = ResourceSampler(clock, settings.orchestration.resources)
    services = resource_services(
        reader,
        project_dir,
        clock,
        settings=settings,
        tmux_server=tmux_server,
        sampler=sampler,
        runner=runner,
    )
    try:
        try:
            sample = await services.resources()
        except psutil.AccessDenied, PermissionError:
            pytest.skip("host process enumeration is restricted")

        assert sample.state is DiagnosticSampleState.AVAILABLE
        assert sample.sampled_at == _TEST_START
        assert sample.shikiru_cpu_pct is not None
        assert sample.shikiru_rss_gb is not None
        assert sample.agents_cpu_pct is not None
        assert sample.agents_rss_gb is not None
        assert sample.agents_rss_gb > 0
        assert sample.shikiru_process_tree_empty is False
        assert sample.agents_process_tree_empty is False
        assert sample.instance_cpu_pct == pytest.approx(
            sample.shikiru_cpu_pct + sample.agents_cpu_pct
        )
        assert sample.instance_rss_gb == pytest.approx(sample.shikiru_rss_gb + sample.agents_rss_gb)
        assert sample.host_ram_total_bytes is not None
        assert sample.host_ram_used_bytes is not None
        assert sampler.latest().sampled_at == sample.sampled_at

        expected_groups = (
            f"Host CPU {format_cpu_pct(sample.host_cpu_pct)}",
            f"Shikiru CPU {format_cpu_pct(sample.shikiru_cpu_pct)}",
            f"Agents CPU {format_cpu_pct(sample.agents_cpu_pct)}",
        )
        app = _app(
            reader,
            project_dir,
            widget_names=["queue", "resources"],
            services=cast("StubServices", services),
            clock=clock,
            settings=settings,
        )
        async with app.run_test(size=size) as pilot:
            await _assert_cached_resource_views(
                app,
                pilot,
                expected_groups=expected_groups,
                sample=sample,
                sampler=sampler,
                services=services,
                size=size,
                task_id=task.id,
                reader=reader,
            )
    finally:
        if process.returncode is None:
            await process.stop()
        turn_processes.clear(task_run_dir, Role.IMPLEMENTER)


async def _assert_cached_resource_views(
    app: DashboardApp,
    pilot: Pilot[None],
    *,
    expected_groups: tuple[str, ...],
    sample: ResourceView,
    sampler: ResourceSampler,
    services: DashboardServices,
    size: tuple[int, int],
    task_id: str,
    reader: StateReader,
) -> None:
    home_resources = app.query_one("#resources", Static)
    expected_resource_rows = len(expected_groups) if size == _COMPACT_HOME_SIZE else 1
    await wait_until(
        lambda: (
            home_resources.region.height >= expected_resource_rows
            and all(
                group in "\n".join(_rendered_widget_lines(home_resources))
                for group in expected_groups
            )
        ),
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    await pilot.pause()
    if size == _COMPACT_HOME_SIZE:
        readouts = app.query_one("#focus-readouts", VerticalScroll)
        assert not readouts.allow_focus()
        home_resources.focus()
        await pilot.press("pagedown")
        await wait_until(
            lambda: (
                readouts.scroll_y > 0
                and all(group in _exported_screen_text(app) for group in expected_groups[1:])
            ),
            timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
        )
        await pilot.press("home")
        await wait_until(lambda: readouts.scroll_y == 0, timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS)
        readouts.post_message(
            MouseScrollDown(
                readouts,
                0,
                0,
                0,
                1,
                0,
                False,
                False,
                False,
                readouts.region.x,
                readouts.region.y,
            )
        )
        await wait_until(
            lambda: (
                readouts.scroll_y > 0
                and all(group in _exported_screen_text(app) for group in expected_groups[1:])
            ),
            timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
        )
        readouts.scroll_to(y=0, animate=False, immediate=True)
        await wait_until(lambda: readouts.scroll_y == 0, timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS)
    if size == _WIDE_HOME_SIZE:
        task_list = app.query_one("#task-browser-list", ListView)
        selected_row = app.query_one(f"#task-row-{task_id}", ListItem)
        await wait_until(
            lambda: task_list.highlighted_child is selected_row,
            timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
        )
        home_screen = app.screen
        command_count_before = reader.command_history(limit=COMMAND_HISTORY_TEST_LIMIT).total_count
        assert pilot.app.focused is task_list
        await pilot.press("shift+tab")
        await wait_until(
            lambda: pilot.app.focused is home_resources,
            timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
        )
        await pilot.press("enter")
        await _assert_project_diagnostics_view(
            app, pilot, expected_groups=expected_groups, size=size
        )
        await pilot.press("escape")
        await wait_until(lambda: app.screen is home_screen, timeout=2)
        assert pilot.app.focused is home_resources
        assert task_list.highlighted_child is selected_row
        assert reader.command_history(limit=COMMAND_HISTORY_TEST_LIMIT).total_count == (
            command_count_before
        )
    else:
        assert await pilot.click("#resources")
        await _assert_project_diagnostics_view(
            app, pilot, expected_groups=expected_groups, size=size
        )
    assert sampler.latest().sampled_at == sample.sampled_at
    cached = services.resource_sample()
    assert cached is not None
    assert cached.sampled_at == sample.sampled_at
    assert cached.shikiru_cpu_pct == sample.shikiru_cpu_pct
    assert cached.agents_cpu_pct == sample.agents_cpu_pct


async def _assert_project_diagnostics_view(
    app: DashboardApp,
    pilot: Pilot[None],
    *,
    expected_groups: tuple[str, ...],
    size: tuple[int, int],
) -> None:
    home_resources = app.query_one("#resources", Static)
    await wait_until(lambda: bool(app.screen.query("#context-widget-frame")), timeout=2)
    diagnostic_resources = app.screen.query_one("#project-diagnostics-resources", Static)
    diagnostic_engine = app.screen.query_one("#project-diagnostics-engine", Static)
    assert plain_text(app.screen.query_one("#context-widget-title", Static).render()) == (
        "Project diagnostics"
    )

    def collapsed(widget: Static) -> str:
        return " ".join(" ".join(_rendered_widget_lines(widget)).split())

    await wait_until(
        lambda: all(group in collapsed(diagnostic_resources) for group in expected_groups),
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    await wait_until(
        lambda: "Status Not running Faults None open" in collapsed(diagnostic_engine),
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    if size == _WIDE_HOME_SIZE:
        home_text = plain_text(home_resources.content)
        assert " · Shikiru CPU " in home_text
        assert " · Agents CPU " in home_text
    await pilot.pause()
    await wait_until(
        lambda: all(group in _exported_screen_text(app) for group in expected_groups),
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    assert "Scope" not in _exported_screen_text(app)


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
            recent_list = app.query_one("#task-browser-recent-list", ListView)
            recent_heading = app.query_one("#task-browser-recent-heading", Horizontal)
            active_row = app.query_one(f"#task-row-{active.id}", ListItem)
            await wait_until(
                lambda: any(item.id == f"task-row-{completed.id}" for item in recent_list.children),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            active_rows = tuple(
                _rendered_text(item.query_one(Static))
                for item in task_list.children
                if isinstance(item, ListItem)
            )
            recent_row = _rendered_text(app.query_one(f"#task-row-{completed.id} > Static", Static))
            assert any(active.id in row for row in active_rows)
            assert (
                recent_heading.region.y
                < app.query_one(f"#task-row-{completed.id}", ListItem).region.y
            )
            assert recent_heading.region.y >= active_row.region.bottom
            assert recent_list.region.bottom == app.query_one("#queue").region.bottom
            assert task_list.max_scroll_y == 0
            assert not task_list.show_vertical_scrollbar
            assert recent_row.startswith("✓ ")
            assert "Done" in recent_row
    finally:
        reader.close()


async def test_home_recent_only_keeps_list_focus_and_cross_row_navigation(
    database: Database,
    connection: Connection,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(_TEST_START)
    completed = seed_task(connection, clock, title="Recent completed request")
    cancelled = seed_task(connection, clock, title="Recent cancelled request")
    task_store = TaskStore(connection, clock)
    task_store.apply_transition(
        completed.id,
        Transition.stay(completed, step=None, status=Status.DONE),
    )
    task_store.apply_transition(
        cancelled.id,
        Transition.stay(cancelled, step=None, status=Status.CANCELLED),
    )
    expected_recent_ids = (cancelled.id, completed.id)
    reader = StateReader(database, clock)

    try:
        command_count = reader.command_history(limit=COMMAND_HISTORY_TEST_LIMIT).total_count
        app = _app(reader, project_dir, widget_names=["queue"], clock=clock)
        async with app.run_test(size=_WIDE_HOME_SIZE) as pilot:
            active_list = app.query_one("#task-browser-list", ListView)
            recent_heading = app.query_one("#task-browser-recent-heading", Horizontal)
            recent_list = app.query_one("#task-browser-recent-list", ListView)
            detail_identity = app.query_one(f"#{TASK_DETAIL_IDENTITY}", Static)
            await wait_until(
                lambda: (
                    tuple(
                        item.id.removeprefix("task-row-")
                        for item in recent_list.children
                        if item.id is not None
                    )
                    == expected_recent_ids
                ),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            assert not active_list.display
            assert active_list.region.height == 0
            assert recent_heading.display
            await wait_until(
                lambda: (
                    app.focused is recent_list
                    and expected_recent_ids[0] in _rendered_text(detail_identity)
                ),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            await pilot.press("down")
            await wait_until(
                lambda: expected_recent_ids[1] in _rendered_text(detail_identity),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            await pilot.press("up")
            await wait_until(
                lambda: expected_recent_ids[0] in _rendered_text(detail_identity),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            await pilot.press("escape")
            await wait_until(
                lambda: (
                    recent_list.index is None
                    and expected_recent_ids[0] not in _rendered_text(detail_identity)
                ),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            assert reader.command_history(limit=COMMAND_HISTORY_TEST_LIMIT).total_count == (
                command_count
            )
    finally:
        reader.close()


async def test_home_escape_clears_selection_after_search_and_keeps_compact_detail_access(
    database: Database,
    connection: Connection,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(_TEST_START)
    first = seed_task(connection, clock, title="First selected task")
    second = seed_task(connection, clock, title="Second selectable task")
    reader = StateReader(database, clock)
    commands = CommandQueue(connection, clock)
    for command in commands.pending():
        commands.mark_done(command.id)
    try:
        app = _app(reader, project_dir, widget_names=["queue"], clock=clock)
        async with app.run_test(size=_WIDE_HOME_SIZE) as pilot:
            await _assert_home_search_clear_precedence(app, pilot, first, second, commands)
            await _assert_compact_home_selection_and_clear(app, pilot, first, second, commands)
    finally:
        reader.close()


async def _assert_home_search_clear_precedence(
    app: DashboardApp,
    pilot: Pilot[None],
    first: Task,
    second: Task,
    commands: CommandQueue,
) -> None:
    home = app.screen
    search = app.query_one("#task-browser-search", Input)
    list_view = app.query_one("#task-browser-list", ListView)
    identity = app.query_one(f"#{TASK_DETAIL_IDENTITY}", Static)
    await wait_until(lambda: first.title in _rendered_text(identity), timeout=2)
    await pilot.press("/")
    await wait_until(lambda: search.has_focus and search.display, timeout=2)
    search.value = first.title
    await wait_until(lambda: first.title in _rendered_text(identity), timeout=2)
    await pilot.press("escape")
    await wait_until(lambda: search.value == "", timeout=2)
    assert first.title in _rendered_text(identity)
    await pilot.press("escape")
    await wait_until(lambda: not search.display and list_view.has_focus, timeout=2)
    await pilot.press("escape")
    await wait_until(
        lambda: (
            list_view.index is None
            and first.id not in _rendered_text(identity)
            and second.id not in _rendered_text(identity)
        ),
        timeout=2,
    )
    assert app.screen is home
    assert commands.pending() == []
    assert app.focused is list_view
    await pilot.press("/")
    await wait_until(lambda: search.has_focus and search.display, timeout=2)
    search.value = second.title
    await wait_until(
        lambda: f"task-row-{second.id}" in {item.id for item in list_view.children},
        timeout=2,
    )
    assert list_view.index is None
    assert first.id not in _rendered_text(identity)
    assert second.id not in _rendered_text(identity)
    await pilot.press("escape")
    await wait_until(lambda: search.value == "", timeout=2)
    await pilot.press("escape")
    await wait_until(lambda: not search.display, timeout=2)
    assert list_view.index is None


async def _assert_compact_home_selection_and_clear(
    app: DashboardApp,
    pilot: Pilot[None],
    first: Task,
    second: Task,
    commands: CommandQueue,
) -> None:
    home = app.screen
    list_view = app.query_one("#task-browser-list", ListView)
    identity = app.query_one(f"#{TASK_DETAIL_IDENTITY}", Static)
    panel = app.query_one("#task-detail-panel", TaskDetailPanel)
    await pilot.resize_terminal(*_COMPACT_HOME_SIZE)
    await wait_until(lambda: panel.display and panel.region.width > 0, timeout=2)
    main = app.query_one("#focus-main", Horizontal)
    main.scroll_to_widget(panel, animate=False, immediate=True)
    await pilot.pause()
    assert panel.region.overlaps(main.scrollable_content_region)
    assert list_view.index is None
    assert first.id not in _rendered_text(identity)
    assert second.id not in _rendered_text(identity)
    task_ids = tuple(
        item.id.removeprefix("task-row-")
        for item in list_view.children
        if item.id is not None and item.id.startswith("task-row-")
    )
    assert second.id in task_ids
    list_view.focus()
    await pilot.press("down")
    await wait_until(lambda: task_ids[0] in _rendered_text(identity), timeout=2)
    await pilot.press("enter")
    await pilot.pause()
    assert app.screen is home
    assert task_ids[0] in _rendered_text(identity)
    assert commands.pending() == []
    await pilot.press("escape")
    await wait_until(lambda: list_view.index is None, timeout=2)
    await pilot.press("up")
    await wait_until(lambda: task_ids[-1] in _rendered_text(identity), timeout=2)
    assert app.screen is home
    assert commands.pending() == []
    await pilot.resize_terminal(*_WIDE_HOME_SIZE)
    clear = app.query_one("#focus-footer-clear", Button)
    await wait_until(lambda: clear.is_on_screen, timeout=2)
    await pilot.click(clear)
    await wait_until(
        lambda: list_view.index is None and first.id not in _rendered_text(identity),
        timeout=2,
    )
    assert app.focused is list_view


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
                lambda: "No decisions needed" in _rendered_text(status),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            visible_status = " ".join(_rendered_widget_lines(status))
            assert "No decisions needed" in visible_status
            assert "Automation continues" not in visible_status
            assert "Engine running" not in visible_status
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
            await _choose_focus_view(app, pilot, "Task filter · waiting")
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
            visible_status = " ".join(_rendered_widget_lines(row))
            assert escalation.id in visible_status
            assert "Enter answer" in visible_status
            assert "Engine stopped" in visible_status
            status_lines = _rendered_widget_lines(row)
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


@pytest.mark.parametrize("size", [_STANDARD_HOME_SIZE, _COMPACT_HOME_SIZE, _TINY_HOME_SIZE])
async def test_clicking_primary_attention_opens_the_answer_form_without_dispatch(
    database: Database,
    connection: Connection,
    tmp_path: Path,
    size: tuple[int, int],
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
    app_instances: list[DashboardApp] = []
    open_palette_context = _palette_context_opener(
        reader,
        connection,
        project_dir,
        clock,
        app_instances,
    )

    try:
        app = _app(
            reader,
            project_dir,
            widget_names=["escalations"],
            open_palette_context=open_palette_context,
        )
        app_instances.append(app)
        async with app.run_test(size=size) as pilot:
            attention = app.query_one("#escalations", EscalationsPanel)
            await wait_until(lambda: attention.display, timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS)
            row = attention.query_one("#escalation-panels", VerticalScroll).query_one(Static)
            assert escalation.id in _rendered_text(row)
            if size == _TINY_HOME_SIZE:
                root = app.query_one("#focus-root", VerticalScroll)
                root.scroll_to_widget(row, animate=False, immediate=True)
                await wait_until(
                    lambda: row.region.overlaps(root.scrollable_content_region),
                    timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
                )
            caller = app.screen

            screen = await _open_attention_answer_form(app, pilot, row)
            assert caller.is_mounted
            _assert_attention_answer_target(screen, task.id, escalation.id, escalation.headline)
            title = screen.query_one("#palette-title", Static)
            assert task.id in _rendered_text(title)
            assert escalation.id in _rendered_text(title)
            await _assert_rendered_palette_header_identity(app, task.id, escalation.id)
            assert "missing-packet.md" in _rendered_text(
                screen.query_one("#palette-evidence-error", Static)
            )
            assert queue.pending() == []
    finally:
        reader.close()


def _seed_home_answer_decision(
    connection: Connection,
    clock: Clock,
) -> tuple[Task, Task, Escalation, CommandQueue]:
    task = seed_task(connection, clock, title="Answer from the normal Home focus")
    related = seed_task(connection, clock, title="Task referenced by the decision")
    TaskStore(connection, clock).apply_transition(
        task.id,
        Transition.stay(task, step=task.step, status=Status.ESCALATED),
    )
    headline = f"{task.id} · Keep the behavior used by {related.id}."
    escalation = EscalationStore(connection, clock).open(
        task.id,
        step=task.step,
        reason=EscalationReason.DECISION_REQUIRED,
        headline=headline,
        packet_path=".shikiru/escalations/missing-packet.md",
        options=options_for(EscalationReason.DECISION_REQUIRED),
    )
    queue = CommandQueue(connection, clock)
    for command in queue.pending():
        queue.mark_done(command.id)
    return task, related, escalation, queue


async def _assert_keyboard_answer_enter_and_cancel(
    app: DashboardApp,
    pilot: Pilot[None],
    screen: PaletteScreen,
    *,
    home_screen: object,
    queue: CommandQueue,
) -> None:
    answer = screen.query_one("#resolution-text", TextArea)
    await wait_until(
        lambda: app.focused is answer,
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    await pilot.pause()
    await pilot.press("enter")
    assert answer.text == "\n"
    assert queue.pending() == []
    await pilot.press("escape")
    await wait_until(lambda: app.screen is home_screen, timeout=2)
    assert queue.pending() == []


async def _submit_mouse_opened_answer(
    app: DashboardApp,
    pilot: Pilot[None],
    row: Static,
    *,
    task: Task,
    escalation: Escalation,
    queue: CommandQueue,
) -> None:
    await pilot.click(row)
    await wait_until(
        lambda: isinstance(app.screen, PaletteScreen),
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    screen = cast("PaletteScreen", app.screen)
    assert screen.query_one("#task-id", Input).value == task.id
    assert screen.query_one("#palette-escalation-summary", Static).display
    assert queue.pending() == []
    screen.query_one("#resolution-text", TextArea).text = "Keep the existing behavior."
    submit = screen.query_one("#palette-submit", Button)
    await pilot.pause()
    assert not submit.disabled
    submit.focus()
    await wait_until(
        lambda: app.focused is submit,
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    await pilot.press("enter")
    await wait_until(
        lambda: bool(queue.pending()),
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    (queued,) = queue.pending()
    assert isinstance(queued.command, Resolve)
    assert queued.command.escalation_id == escalation.id
    assert isinstance(queued.command.resolution, AnswerResolution)


@pytest.mark.parametrize("size", [_WIDE_HOME_SIZE, _COMPACT_HOME_SIZE])
async def test_home_list_enter_opens_the_attention_answer_and_waits_for_submit(
    database: Database,
    connection: Connection,
    tmp_path: Path,
    size: tuple[int, int],
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = SystemClock()
    task, related, escalation, queue = _seed_home_answer_decision(connection, clock)
    headline = f"{task.id} · Keep the behavior used by {related.id}."
    reader = StateReader(database, clock)
    app_instances: list[DashboardApp] = []
    open_palette_context = _palette_context_opener(
        reader,
        connection,
        project_dir,
        clock,
        app_instances,
    )

    try:
        app = _app(
            reader,
            project_dir,
            widget_names=["queue", "escalations"],
            open_palette_context=open_palette_context,
        )
        app_instances.append(app)
        async with app.run_test(size=size) as pilot:
            home_screen = app.screen
            task_list = app.query_one("#task-browser-list", ListView)
            attention = app.query_one("#escalations", EscalationsPanel)
            await wait_until(
                lambda: (
                    app.focused is task_list
                    and escalation.id
                    in _rendered_text(
                        attention.query_one("#escalation-panels", VerticalScroll).query_one(Static)
                    )
                ),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            row = attention.query_one("#escalation-panels", VerticalScroll).query_one(Static)
            attention_text = _rendered_text(row)
            assert attention_text.count(task.id) == 1
            assert related.id in attention_text
            assert "Enter answer" in attention_text
            assert queue.pending() == []

            await pilot.press("enter")
            await wait_until(
                lambda: isinstance(app.screen, PaletteScreen),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            screen = cast("PaletteScreen", app.screen)
            assert home_screen.is_mounted
            _assert_fixed_context_target_picker_hidden(screen)
            assert screen.query_one("#task-id", Input).value == task.id
            summary = _rendered_text(screen.query_one("#palette-escalation-summary", Static))
            assert summary.count(task.id) == 1
            assert related.id in summary
            assert escalation.id in summary
            assert headline.removeprefix(f"{task.id} · ") in summary
            stored = EscalationStore(connection, clock).get(escalation.id)
            assert stored is not None
            assert stored.headline == headline
            assert queue.pending() == []
            if size == _WIDE_HOME_SIZE:
                frame = screen.query_one("#palette-frame")
                evidence_error_field = screen.query_one("#field-palette-evidence-error")
                evidence_error = screen.query_one("#palette-evidence-error", Static)
                actions = screen.query_one("#palette-actions")
                assert actions.region.bottom <= frame.content_region.bottom
                assert evidence_error_field.region.height == evidence_error.region.height
                assert (
                    actions.region.y - evidence_error.region.bottom
                    <= _FORM_CONTENT_TRAILING_GAP_ROWS
                )
            await _assert_keyboard_answer_enter_and_cancel(
                app,
                pilot,
                screen,
                home_screen=home_screen,
                queue=queue,
            )
            await _submit_mouse_opened_answer(
                app,
                pilot,
                row,
                task=task,
                escalation=escalation,
                queue=queue,
            )
    finally:
        reader.close()


@pytest.mark.parametrize("size", [_WIDE_HOME_SIZE, _COMPACT_HOME_SIZE])
async def test_home_list_enter_opens_the_selected_resolution_without_dispatching(
    database: Database,
    connection: Connection,
    tmp_path: Path,
    size: tuple[int, int],
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = SystemClock()
    task = seed_task(connection, clock, title="Resolve from the normal Home focus")
    TaskStore(connection, clock).apply_transition(
        task.id,
        Transition.stay(task, step=task.step, status=Status.ESCALATED),
    )
    escalation = EscalationStore(connection, clock).open(
        task.id,
        step=task.step,
        reason=EscalationReason.AGENT_ERROR,
        headline=f"{task.id} · Retry the failed step.",
        packet_path=".shikiru/escalations/missing-resolution-packet.md",
        options=options_for(EscalationReason.AGENT_ERROR),
    )
    queue = CommandQueue(connection, clock)
    for command in queue.pending():
        queue.mark_done(command.id)
    reader = StateReader(database, clock)
    app_instances: list[DashboardApp] = []
    open_palette_context = _palette_context_opener(
        reader,
        connection,
        project_dir,
        clock,
        app_instances,
    )

    try:
        app = _app(
            reader,
            project_dir,
            widget_names=["queue", "escalations"],
            open_palette_context=open_palette_context,
        )
        app_instances.append(app)
        async with app.run_test(size=size) as pilot:
            home_screen = app.screen
            task_list = app.query_one("#task-browser-list", ListView)
            attention = app.query_one("#escalations", EscalationsPanel)
            row = attention.query_one("#escalation-panels", VerticalScroll).query_one(Static)
            await wait_until(
                lambda: (
                    app.focused is task_list
                    and escalation.id in _rendered_text(row)
                    and "Enter resolve" in _rendered_text(row)
                ),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            assert _rendered_text(row).count(task.id) == 1
            await pilot.press("enter")
            await wait_until(
                lambda: isinstance(app.screen, PaletteScreen),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            screen = cast("PaletteScreen", app.screen)
            target, resolution = _assert_missing_packet_resolution_form(
                screen,
                task.id,
                escalation,
            )
            await _assert_rendered_palette_header_identity(app, task.id, escalation.id)
            assert queue.pending() == []
            if size == _COMPACT_HOME_SIZE:
                await _capture_compact_resolution_context(app, target, task.id, escalation.id)
            await _exercise_native_resolution_select(app, pilot, resolution, queue)
            await _assert_rendered_palette_header_identity(app, task.id, escalation.id)
            if size == _COMPACT_HOME_SIZE:
                retry_rung = await _assert_compact_resolution_controls(
                    app,
                    screen,
                    size,
                    queue,
                    task_id=task.id,
                    escalation_id=escalation.id,
                )
                await pilot.resize_terminal(*_TINY_HOME_SIZE)
                await wait_until(
                    lambda: app.size == _TINY_HOME_SIZE and app.focused is retry_rung,
                    timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
                )
                await _assert_rendered_palette_header_identity(app, task.id, escalation.id)
            assert queue.pending() == []
            await pilot.press("escape")
            await wait_until(lambda: app.screen is home_screen, timeout=2)
            assert queue.pending() == []
    finally:
        reader.close()


async def test_home_list_enter_uses_the_selected_decision_when_one_task_has_several(
    database: Database,
    connection: Connection,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = SystemClock()
    task = seed_task(connection, clock, title="Choose between the two decisions")
    TaskStore(connection, clock).apply_transition(
        task.id,
        Transition.stay(task, step=task.step, status=Status.ESCALATED),
    )
    escalations = EscalationStore(connection, clock)
    decisions = tuple(
        escalations.open(
            task.id,
            step=task.step,
            reason=EscalationReason.DECISION_REQUIRED,
            headline=f"{task.id} · {headline}",
            packet_path=f".shikiru/escalations/{index}-missing.md",
            options=options_for(EscalationReason.DECISION_REQUIRED),
        )
        for index, headline in enumerate(("First decision", "Second decision"))
    )
    queue = CommandQueue(connection, clock)
    for command in queue.pending():
        queue.mark_done(command.id)
    reader = StateReader(database, clock)
    app_instances: list[DashboardApp] = []
    open_palette_context = _palette_context_opener(
        reader,
        connection,
        project_dir,
        clock,
        app_instances,
    )

    try:
        app = _app(
            reader,
            project_dir,
            widget_names=["queue", "escalations"],
            open_palette_context=open_palette_context,
        )
        app_instances.append(app)
        async with app.run_test(size=_WIDE_HOME_SIZE) as pilot:
            home_screen = app.screen
            task_list = app.query_one("#task-browser-list", ListView)
            attention = app.query_one("#escalations", EscalationsPanel)
            row = attention.query_one("#escalation-panels", VerticalScroll).query_one(Static)
            await wait_until(
                lambda: app.focused is task_list and decisions[0].id in _rendered_text(row),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            attention.focus()
            await pilot.press("right")
            await wait_until(
                lambda: (
                    decisions[1].id in _rendered_text(row)
                    and decisions[0].id not in _rendered_text(row)
                ),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            task_list.focus()
            await pilot.press("enter")
            await wait_until(
                lambda: isinstance(app.screen, PaletteScreen),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            keyboard_screen = cast("PaletteScreen", app.screen)
            assert keyboard_screen.query_one("#task-id", Input).value == task.id
            assert decisions[1].id in _rendered_text(
                keyboard_screen.query_one("#palette-title", Static)
            )
            assert decisions[0].id not in _rendered_text(
                keyboard_screen.query_one("#palette-title", Static)
            )
            await _assert_rendered_palette_header_identity(app, task.id, decisions[1].id)
            assert queue.pending() == []

            await pilot.press("escape")
            await wait_until(lambda: app.screen is home_screen, timeout=2)
            await pilot.click(row)
            await wait_until(
                lambda: isinstance(app.screen, PaletteScreen),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            mouse_screen = cast("PaletteScreen", app.screen)
            assert mouse_screen.query_one("#task-id", Input).value == task.id
            assert decisions[1].id in _rendered_text(
                mouse_screen.query_one("#palette-title", Static)
            )
            assert queue.pending() == []
    finally:
        reader.close()


@pytest.mark.parametrize("widget_name", ["escalations", "queue"])
@pytest.mark.parametrize(
    ("resolution_kind", "resolution_type"),
    [
        (ResolutionKind.RETRY, RetryResolution),
        (ResolutionKind.CANCEL, CancelResolution),
    ],
)
async def test_attention_routes_non_answer_decisions_to_explicit_resolution(
    database: Database,
    connection: Connection,
    tmp_path: Path,
    *,
    widget_name: str,
    resolution_kind: ResolutionKind,
    resolution_type: type[object],
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = SystemClock()
    task = seed_task(connection, clock, title="Resolve the failed step", mode=Mode.IMPLEMENT)
    step = Step.IMPLEMENT
    TaskStore(connection, clock).apply_transition(
        task.id,
        Transition.stay(task, step=step, status=Status.ESCALATED),
    )
    question = "Should the failed implementation step run again?"
    escalation = _open_agent_error_escalation(
        connection,
        clock,
        project_dir,
        task,
        step=step,
        headline="The implementation step failed",
        packet_name="agent-error.md",
        question=question,
        attempted="The implementation step failed on its final retry.",
    )
    queue = CommandQueue(connection, clock)
    for command in queue.pending():
        queue.mark_done(command.id)
    assert queue.pending() == []
    reader = StateReader(database, clock)
    app_instances: list[DashboardApp] = []
    palette_opener = _palette_context_opener(
        reader,
        connection,
        project_dir,
        clock,
        app_instances,
    )
    palette_requests: list[PaletteRequest] = []

    async def open_palette_context(request: PaletteRequest) -> None:
        palette_requests.append(request)
        await palette_opener(request)

    try:
        app = _app(
            reader,
            project_dir,
            widget_names=[widget_name],
            open_palette_context=open_palette_context,
        )
        app_instances.append(app)
        async with app.run_test(size=_STANDARD_HOME_SIZE) as pilot:
            attention = app.query_one("#escalations", Static)
            await wait_until(lambda: attention.display, timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS)
            row = (
                app.query_one("#escalation-panels", VerticalScroll).query_one(Static)
                if widget_name == "escalations"
                else attention
            )
            await wait_until(
                lambda: escalation.id in _rendered_text(row),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            await pilot.click(row)
            await wait_until(
                lambda: isinstance(app.screen, PaletteScreen),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )

            assert len(palette_requests) == 1
            assert palette_requests[0].direct_answer is False
            screen = cast("PaletteScreen", app.screen)
            resolution = _assert_non_answer_resolution_form(
                screen,
                task.id,
                escalation.id,
                question,
            )
            assert queue.pending() == []

            resolution.value = resolution_kind.value
            await pilot.pause()
            assert queue.pending() == []
            await pilot.click("#palette-submit")
            await wait_until(lambda: bool(queue.pending()), timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS)

            (queued,) = queue.pending()
            assert isinstance(queued.command, Resolve)
            assert queued.command.escalation_id == escalation.id
            assert isinstance(queued.command.resolution, resolution_type)
    finally:
        reader.close()


async def test_attention_answer_draft_stays_with_its_decision_on_the_same_task(
    database: Database,
    connection: Connection,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = SystemClock()
    task = seed_task(connection, clock, title="Sequential decision drafts")
    TaskStore(connection, clock).apply_transition(
        task.id,
        Transition.stay(task, step=task.step, status=Status.ESCALATED),
    )
    escalations = EscalationStore(connection, clock)
    first = escalations.open(
        task.id,
        step=task.step,
        reason=EscalationReason.DECISION_REQUIRED,
        headline="First decision summary",
        packet_path=".shikiru/escalations/first-missing.md",
        options=options_for(EscalationReason.DECISION_REQUIRED),
    )
    queue = CommandQueue(connection, clock)
    for command in queue.pending():
        queue.mark_done(command.id)
    assert queue.pending() == []
    reader = StateReader(database, clock)
    app_instances: list[DashboardApp] = []
    open_palette_context = _palette_context_opener(
        reader,
        connection,
        project_dir,
        clock,
        app_instances,
    )

    try:
        app = _app(
            reader,
            project_dir,
            widget_names=["escalations"],
            open_palette_context=open_palette_context,
        )
        app_instances.append(app)
        async with app.run_test(size=_STANDARD_HOME_SIZE) as pilot:
            attention = app.query_one("#escalations", EscalationsPanel)
            await wait_until(lambda: attention.display, timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS)
            row = attention.query_one("#escalation-panels", VerticalScroll).query_one(Static)
            await wait_until(
                lambda: first.id in _rendered_text(row),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            caller = app.screen
            first_palette = await _open_attention_answer_form(app, pilot, row)
            _assert_attention_answer_target(
                first_palette,
                task.id,
                first.id,
                "First decision summary",
            )
            assert queue.pending() == []
            first_guidance = "Keep this answer with the first decision only."
            await _enter_answer_text(pilot, first_palette, first_guidance)
            await _close_palette_to_caller(app, pilot, caller, queue)

            escalations.resolve(first.id, "answer closed first decision")
            second = escalations.open(
                task.id,
                step=task.step,
                reason=EscalationReason.DECISION_REQUIRED,
                headline="Second decision summary",
                packet_path=".shikiru/escalations/second-missing.md",
                options=options_for(EscalationReason.DECISION_REQUIRED),
            )
            await wait_until(
                lambda: second.id in _rendered_text(row) and first.id not in _rendered_text(row),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            second_palette = await _open_attention_answer_form(app, pilot, row)
            second_summary = _rendered_text(
                second_palette.query_one("#palette-escalation-summary", Static)
            )
            _assert_attention_answer_target(
                second_palette,
                task.id,
                second.id,
                "Second decision summary",
            )
            assert "First decision summary" not in second_summary
            second_answer = second_palette.query_one("#resolution-text", TextArea)
            assert second_answer.text == ""
            assert not second_palette.query_one("#field-escalation").display
            assert queue.pending() == []

            second_guidance = "Keep this answer with the second decision only."
            await _enter_answer_text(pilot, second_palette, second_guidance)
            await _close_palette_to_caller(app, pilot, caller, queue)
            reopened = await _open_attention_answer_form(app, pilot, row)
            _assert_attention_answer_target(
                reopened,
                task.id,
                second.id,
                "Second decision summary",
            )
            assert reopened.query_one("#resolution-text", TextArea).text == second_guidance
            assert queue.pending() == []
            await _close_palette_to_caller(app, pilot, caller, queue)
    finally:
        reader.close()


async def test_attention_non_answer_route_keeps_an_earlier_answer_draft_on_its_target(
    database: Database,
    connection: Connection,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = SystemClock()
    task = seed_task(connection, clock, title="Answer draft and retry decision")
    TaskStore(connection, clock).apply_transition(
        task.id,
        Transition.stay(task, step=task.step, status=Status.ESCALATED),
    )
    escalations = EscalationStore(connection, clock)
    first = escalations.open(
        task.id,
        step=task.step,
        reason=EscalationReason.DECISION_REQUIRED,
        headline="Earlier answer decision",
        packet_path=".shikiru/escalations/first-missing.md",
        options=options_for(EscalationReason.DECISION_REQUIRED),
    )
    queue = CommandQueue(connection, clock)
    for command in queue.pending():
        queue.mark_done(command.id)
    reader = StateReader(database, clock)
    app_instances: list[DashboardApp] = []
    open_palette_context = _palette_context_opener(
        reader,
        connection,
        project_dir,
        clock,
        app_instances,
    )

    try:
        app = _app(
            reader,
            project_dir,
            widget_names=["escalations"],
            open_palette_context=open_palette_context,
        )
        app_instances.append(app)
        async with app.run_test(size=_MEASURED_HOME_SIZE) as pilot:
            attention = app.query_one("#escalations", EscalationsPanel)
            await wait_until(lambda: attention.display, timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS)
            row = attention.query_one("#escalation-panels", VerticalScroll).query_one(Static)
            await wait_until(
                lambda: first.id in _rendered_text(row),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            caller = app.screen
            first_palette = await _open_attention_answer_form(app, pilot, row)
            _assert_attention_answer_target(
                first_palette,
                task.id,
                first.id,
                "Earlier answer decision",
            )
            first_guidance = "Guidance belongs to the resolved decision."
            await _enter_answer_text(pilot, first_palette, first_guidance)
            await _close_palette_to_caller(app, pilot, caller, queue)

            escalations.resolve(first.id, "answer closed the earlier decision")
            question = "Should the failed step run again?"
            second = _open_agent_error_escalation(
                connection,
                clock,
                project_dir,
                task,
                step=task.step,
                headline="Retry decision",
                packet_name="retry.md",
                question=question,
                attempted="The step failed after its final retry.",
            )
            await wait_until(
                lambda: second.id in _rendered_text(row) and first.id not in _rendered_text(row),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )

            second_palette = await _open_attention_answer_form(app, pilot, row)
            resolution = _assert_non_answer_resolution_form(
                second_palette,
                task.id,
                second.id,
                question,
            )
            assert second_palette.query_one("#resolution-text", TextArea).text == ""
            summary = _rendered_text(
                second_palette.query_one("#palette-escalation-summary", Static)
            )
            assert "Earlier answer decision" not in summary
            assert resolution.value in {
                ResolutionKind.RETRY.value,
                ResolutionKind.CANCEL.value,
            }
            assert queue.pending() == []

            resolution.value = ResolutionKind.CANCEL.value
            await pilot.pause()
            assert queue.pending() == []
            await pilot.click("#palette-submit")
            await wait_until(lambda: bool(queue.pending()), timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS)
            (queued,) = queue.pending()
            assert isinstance(queued.command, Resolve)
            assert queued.command.escalation_id == second.id
            assert isinstance(queued.command.resolution, CancelResolution)
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
        async with first_app.run_test(size=(120, 40)) as pilot:
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
            queued_at = reader.command_history(limit=1).entries[0].ts
            queued_time = datetime.fromisoformat(queued_at)
            summary_time = f"queued {queued_time.month}/{queued_time.day} {queued_time:%H:%M}Z"
            await pilot.press("escape")
            await wait_until(lambda: first_app.screen is dashboard_screen, timeout=2)
            status = first_app.query_one("#workspace-command-status", Button)
            await wait_until(
                lambda: (
                    status.display
                    and "Pending: Stop " in _rendered_text(status)
                    and summary_time in _rendered_text(status)
                ),
                timeout=2,
            )
            await pilot.click(status)
            await wait_until(
                lambda: bool(first_app.screen.query("#command-history-page")), timeout=2
            )
            history_screen = first_app.screen
            history_copy = history_screen.query_one("#command-history-copy", Static)
            await pilot.pause()
            assert "Loading" not in _rendered_text(history_copy), type(history_screen).__name__
            assert f"#{pending_id} · Pending" in _rendered_text(history_copy)
            assert f"queued at {queued_at}" in _rendered_text(history_copy)
            assert "engine currently stopped" in _rendered_text(history_copy)
            assert "original acknowledgement wait context unknown" in _rendered_text(history_copy)
            await pilot.press("escape")
            await wait_until(lambda: first_app.screen is dashboard_screen, timeout=2)
            queue.mark_done(pending_id, {"stopping": True})
            await wait_until(
                lambda: (
                    "Applied: Stop " in _rendered_text(status)
                    and "shutdown requested" in _rendered_text(status)
                ),
                timeout=2,
            )
    finally:
        reader.close()


async def test_pending_feedback_keeps_recorded_queue_time_when_clock_advances_without_writes(
    database: Database,
    connection: Connection,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(_TEST_START)
    queue = CommandQueue(connection, clock)
    queue.append(Stop())
    reader = StateReader(database, clock)
    queued_at = reader.command_history(limit=1).entries[0].ts
    summary_time = f"queued {_TEST_START.month}/{_TEST_START.day} {_TEST_START:%H:%M}Z"
    data_version = reader.data_version()

    try:
        app = _app(
            reader,
            project_dir,
            widget_names=[],
            services=StubServices(engine_running=False),
            clock=clock,
        )
        async with app.run_test(size=(120, 40)) as pilot:
            status = app.query_one("#workspace-command-status", Button)
            await wait_until(
                lambda: status.display and summary_time in _rendered_text(status),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            await pilot.pause()
            displayed = _rendered_text(status)
            clock.advance(_COMMAND_FEEDBACK_CLOCK_ADVANCE_SECONDS)
            await pilot.pause()
            assert reader.data_version() == data_version
            assert _rendered_text(status) == displayed
            assert "ago" not in displayed
            assert summary_time in displayed

            await pilot.click(status)
            await wait_until(lambda: bool(app.screen.query("#command-history-page")), timeout=2)
            history = app.screen.query_one("#command-history-copy", Static)
            await wait_until(lambda: f"queued at {queued_at}" in _rendered_text(history), timeout=2)
            assert "ago" not in _rendered_text(history)
            assert "original acknowledgement wait context unknown" in _rendered_text(history)
            assert reader.data_version() == data_version
    finally:
        reader.close()


@pytest.mark.parametrize("removed_selector", ["#focus-footer", "#focus-main"])
async def test_home_command_feedback_callbacks_survive_partial_home_teardown(
    database: Database,
    connection: Connection,
    tmp_path: Path,
    removed_selector: str,
) -> None:
    del connection
    project_dir = write_minimal_project(tmp_path)
    reader = StateReader(database, SystemClock())

    async def refuse_detach() -> bool:
        return False

    try:
        app = _app(
            reader,
            project_dir,
            widget_names=[],
            tmux_detach_hint=_GLOBAL_TMUX_PREFIX,
            detach_tmux_client=refuse_detach,
        )
        async with app.run_test(size=(120, 40)) as pilot:
            status = app.query_one("#workspace-command-status", Button)
            await pilot.click("#focus-footer-detach")
            await wait_until(
                lambda: status.display and "No project tmux client" in _rendered_text(status),
                timeout=2,
            )
            removed = app.query_one(removed_selector)
            removal = removed.remove()
            await removal
            await wait_until(lambda: not app.query(removed_selector), timeout=2)
            await pilot.pause()
            assert app.is_running
            if removed_selector == "#focus-footer":
                assert not app.query("#workspace-command-status")
            else:
                assert status.display
                assert "No project tmux client" in _rendered_text(status)
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

        task = seed_task(connection, clock, title="Message delivery feedback")
        for submitted in queue.pending():
            queue.mark_done(submitted.id, {"task_id": task.id})
        pending = queue.append(Stop())
        say = queue.append(
            Say(task_id=task.id, role=Role.IMPLEMENTER, text="Review this on your next turn.")
        )
        queue.mark_done(say.id, {"task_id": task.id, "role": Role.IMPLEMENTER.value})
        other_client = database.connect()
        try:
            other_queue = CommandQueue(other_client, clock)
            rejected = other_queue.append(Prioritize(task_id="T-404", priority=4))
            rejection = {"error": "fake engine rejected the target"}
            other_queue.mark_rejected(rejected.id, rejection)
        finally:
            other_client.close()

        restarted_app = _app(
            reader,
            project_dir,
            widget_names=[],
            services=StubServices(engine_running=True),
        )
        async with restarted_app.run_test(size=(120, 40)) as pilot:
            status = restarted_app.query_one("#workspace-command-status", Button)
            await pilot.pause()
            await wait_until(
                lambda: status.display and "Rejected: Prioritize T-404" in _rendered_text(status),
                timeout=2,
            )
            await _choose_focus_view(
                restarted_app, pilot, "Command history · pending, applied and rejected"
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
            assert f"#{say.id} · Applied" in history_text
            assert rejection["error"] in history_text
    finally:
        reader.close()


def _seed_command_feedback_task(
    connection: Connection,
    clock: SystemClock,
    status: CommandFeedbackStatus,
) -> tuple[Task, int]:
    task = seed_task(connection, clock, title="Footer feedback task")
    queue = CommandQueue(connection, clock)
    for submitted in queue.pending():
        queue.mark_done(submitted.id, {"task_id": task.id})
    prioritize = queue.append(Prioritize(task_id=task.id, priority=4))
    if status is CommandFeedbackStatus.APPLIED:
        TaskStore(connection, clock).set_priority(task.id, 4)
        queue.mark_done(prioritize.id, {"task_id": task.id, "priority": 4})
    elif status is CommandFeedbackStatus.REJECTED:
        queue.mark_rejected(prioritize.id, {"error": "task T-404 does not exist"})
    return task, prioritize.id


def _assert_rendered_command_summary(
    app: DashboardApp,
    status: Button,
    *,
    mono: bool,
) -> None:
    if mono:
        rendered = _exported_screen_text(app)
        assert "Rejected" in rendered, (
            status.region,
            status.content_size,
            _rendered_text(status),
            rendered,
        )
        assert "Prioritize T-404" in rendered, (
            status.region,
            status.content_size,
            _rendered_text(status),
            rendered,
        )
        assert "…" in rendered, (
            f"status label {_rendered_text(status)!r}, content {status.content_size}, "
            f"region {status.region}, exported {rendered!r}"
        )
    else:
        assert "priority 4" in _rendered_text(status)


async def _assert_command_feedback_footer(
    app: DashboardApp,
    pilot: Pilot[None],
    task: Task,
    command_id: int,
    render_case: tuple[tuple[int, int], bool],
) -> None:
    size, mono = render_case
    identity = app.query_one(f"#{TASK_DETAIL_IDENTITY}", Static)
    await wait_until(
        lambda: task.title in _rendered_text(identity),
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    status = app.query_one("#workspace-command-status", Button)
    actions = app.query_one("#focus-footer-actions", Button)
    help_button = app.query_one("#focus-footer-help", Button)
    footer = app.query_one("#focus-footer", Grid)
    await wait_until(lambda: status.display and status.size.width > 0, timeout=2)
    if not mono:
        await wait_until(lambda: "priority 4" in _rendered_text(status), timeout=2)
    else:
        await wait_until(lambda: "…" in _rendered_text(status), timeout=2)
    status_text = _rendered_text(status)
    assert status.region.bottom <= size[1]
    assert footer.region.right <= size[0]
    assert footer.region.contains_region(status.region)
    assert app.query_one("#focus-main", Horizontal).size.height > 0
    assert task.title in _rendered_text(identity)
    if mono:
        assert "Rejected: Prioritize T-404" in status_text
        assert "does not exist" not in status_text
        assert status.region.y > help_button.region.y or status.region.x >= help_button.region.right
    else:
        assert "Applied: Prioritize T-1" in status_text
        assert task.id in status_text
        assert "priority 4" in status_text
        assert status.region.y == actions.region.y
        assert status.region.x > help_button.region.x
        assert status.region.x - help_button.region.right >= 1
        assert status.region.right <= footer.region.right
    resized_size = _WIDE_HOME_SIZE if mono else _COMPACT_HOME_SIZE
    await pilot.resize_terminal(*resized_size)
    await wait_until(
        lambda: (
            app.size.width == resized_size[0]
            and status.size.width > 0
            and app.query_one("#focus-footer", Grid).region.contains_region(status.region)
        ),
        timeout=2,
    )
    assert task.title in _rendered_text(identity)
    await pilot.resize_terminal(*size)
    await wait_until(
        lambda: app.size.width == size[0] and status.region.bottom <= size[1], timeout=2
    )
    await pilot.pause()
    _assert_rendered_command_summary(app, status, mono=mono)
    home_screen = app.screen
    if mono:
        await pilot.click(status)
    else:
        status.focus()
        await pilot.press("enter")
    await wait_until(lambda: app.screen is not home_screen, timeout=2)
    history_copy = app.screen.query_one("#command-history-copy", Static)
    expected_detail = "task T-404 does not exist" if mono else "priority 4"
    expected_status = "Rejected" if mono else "Applied"
    await wait_until(
        lambda: (
            f"#{command_id} · {expected_status}" in _rendered_text(history_copy)
            and expected_detail in _rendered_text(history_copy)
        ),
        timeout=2,
    )


@pytest.mark.parametrize(
    "render_case",
    [(_WIDE_HOME_SIZE, False), (_COMPACT_HOME_SIZE, True)],
)
async def test_durable_command_feedback_footer_matches_home_facts_and_resizes(
    database: Database,
    connection: Connection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    render_case: tuple[tuple[int, int], bool],
) -> None:
    size, mono = render_case
    project_dir = write_minimal_project(tmp_path)
    if mono:
        monkeypatch.setenv("NO_COLOR", "1")
    else:
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.delenv("FORCE_COLOR", raising=False)
        monkeypatch.setenv("TERM", "xterm-256color")
    clock = SystemClock()
    task, command_id = _seed_command_feedback_task(connection, clock, CommandFeedbackStatus.APPLIED)
    if mono:
        queue = CommandQueue(connection, clock)
        rejected = queue.append(Prioritize(task_id="T-404", priority=4))
        queue.mark_rejected(rejected.id, {"error": "task T-404 does not exist"})
        command_id = rejected.id
    reader = StateReader(database, clock)

    try:
        app = _app(
            reader,
            project_dir,
            widget_names=["header", "queue", "usage", "resources", "escalations"],
            clock=clock,
        )
        async with app.run_test(size=size) as pilot:
            await _select_home_task(app, task.id)
            await _assert_command_feedback_footer(app, pilot, task, command_id, render_case)
    finally:
        reader.close()


def _last_glyph_column(widget: Widget) -> int:
    line = widget.render_lines(Region(0, 0, widget.region.width, widget.region.height))[0]
    return widget.region.x + len(line.text.rstrip()) - 1


async def _assert_status_ends_on_footer_edge(
    app: DashboardApp,
    pilot: Pilot[None],
    status_line: Button,
    footer: Grid,
    width: int,
) -> None:
    await pilot.resize_terminal(width, _WIDE_HOME_SIZE[1])
    await wait_until(
        lambda: (
            app.size.width == width
            and status_line.size.width > 0
            and footer.region.contains_region(status_line.region)
        ),
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    await pilot.pause()
    assert _last_glyph_column(status_line) == footer.region.right - 1, (
        width,
        _rendered_text(status_line),
    )


@pytest.mark.parametrize("status", list(CommandFeedbackStatus))
async def test_latest_command_status_ends_on_footer_right_edge_across_widths(
    database: Database,
    connection: Connection,
    tmp_path: Path,
    status: CommandFeedbackStatus,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = SystemClock()
    _seed_command_feedback_task(connection, clock, status)
    reader = StateReader(database, clock)

    try:
        app = _app(
            reader,
            project_dir,
            widget_names=["header", "queue", "usage", "resources", "escalations"],
            clock=clock,
            tmux_detach_hint=_GLOBAL_TMUX_PREFIX,
        )
        async with app.run_test(size=_WIDE_HOME_SIZE) as pilot:
            status_line = app.query_one("#workspace-command-status", Button)
            footer = app.query_one("#focus-footer", Grid)
            for width in _FOOTER_EDGE_WIDTHS:
                await _assert_status_ends_on_footer_edge(app, pilot, status_line, footer, width)
            assert status.value in _rendered_text(status_line)
    finally:
        reader.close()


async def _assert_command_history_initial_shortcuts(
    app: DashboardApp,
    pilot: Pilot[None],
    page: Static,
    history_copy: Static,
    pending_id: int,
    *,
    history_controls: Widget,
    controls_y: int,
    history_frame: Widget,
    preferred_region: Region,
) -> None:
    previous_button = app.screen.query_one("#command-history-previous", Button)
    assert previous_button.disabled
    await pilot.press("p")
    assert "page 1" in _rendered_text(page)
    next_button = app.screen.query_one("#command-history-next", Button)
    await pilot.click(next_button)
    await wait_until(lambda: "page 2" in _rendered_text(page), timeout=2)
    assert history_frame.region == preferred_region
    assert history_controls.region.y == controls_y
    next_button.focus()
    await pilot.press("p")
    await wait_until(lambda: "page 1" in _rendered_text(page), timeout=2)
    assert history_frame.region == preferred_region
    assert history_controls.region.y == controls_y
    await pilot.press("n")
    await wait_until(lambda: "page 2" in _rendered_text(page), timeout=2)
    assert history_frame.region == preferred_region
    assert history_controls.region.y == controls_y
    assert f"#{pending_id} · Pending" not in _rendered_text(history_copy)


async def _assert_command_history_filter_shortcuts(
    app: DashboardApp,
    pilot: Pilot[None],
    page: Static,
    history_copy: Static,
    applied_id: int,
    *,
    history_controls: Widget,
    controls_y: int,
    history_frame: Widget,
    preferred_region: Region,
) -> None:
    history_filter = cast("Select[str]", app.screen.query_one("#command-history-filter", Select))
    history_filter.value = "all"
    await wait_until(lambda: history_filter.value == "all", timeout=2)
    previous_button = app.screen.query_one("#command-history-previous", Button)
    previous_button.focus()
    await pilot.press(COMMAND_HISTORY_PREVIOUS_SHORTCUT)
    await wait_until(lambda: "page 1" in _rendered_text(page), timeout=2)
    await wait_until(lambda: f"#{applied_id} · Applied" in _rendered_text(history_copy), timeout=2)
    await wait_until(
        lambda: (
            "all statuses" in _rendered_text(page)
            and not app.screen.query_one("#command-history-next", Button).disabled
        ),
        timeout=2,
    )
    history_filter.focus()
    await pilot.press("enter")
    await wait_until(lambda: history_filter.expanded, timeout=2)
    assert history_frame.region == preferred_region
    assert history_controls.region.y == controls_y
    for shortcut in (COMMAND_HISTORY_PREVIOUS_SHORTCUT, COMMAND_HISTORY_NEXT_SHORTCUT):
        await pilot.press(shortcut)
        assert history_filter.expanded
        assert "page 1" in _rendered_text(page)
    await pilot.press("enter")
    await wait_until(
        lambda: history_filter.value == CommandFeedbackStatus.PENDING.value,
        timeout=2,
    )
    assert "page 1" in _rendered_text(page)
    history_filter.value = CommandFeedbackStatus.REJECTED.value
    await wait_until(
        lambda: history_filter.value == CommandFeedbackStatus.REJECTED.value,
        timeout=2,
    )
    await wait_until(lambda: "No commands recorded." in _rendered_text(history_copy), timeout=2)
    assert history_frame.region == preferred_region
    assert history_controls.region.y == controls_y
    history_filter.value = "all"
    await wait_until(lambda: history_filter.value == "all", timeout=2)
    await wait_until(lambda: f"#{applied_id} · Applied" in _rendered_text(history_copy), timeout=2)
    await wait_until(lambda: not history_filter.expanded, timeout=2)
    assert "page 1" in _rendered_text(page)
    assert history_frame.region == preferred_region
    assert history_controls.region.y == controls_y
    await wait_until(
        lambda: (
            "all statuses" in _rendered_text(page)
            and not app.screen.query_one("#command-history-next", Button).disabled
        ),
        timeout=2,
    )
    app.screen.query_one("#command-history-next", Button).focus()


async def _assert_command_history_last_page_and_refresh(
    app: DashboardApp,
    pilot: Pilot[None],
    *,
    database: Database,
    clock: SystemClock,
    pending_id: int,
    home_screen: Widget,
    history_frame: Widget,
    history_controls: Widget,
    controls_y: int,
    history_scroll: VerticalScroll,
    preferred_region: Region,
) -> None:
    page = app.screen.query_one("#command-history-page", Static)
    history_copy = app.screen.query_one("#command-history-copy", Static)
    app.screen.query_one("#command-history-next", Button).focus()
    await pilot.press("n")
    await wait_until(lambda: "page 2" in _rendered_text(page), timeout=2)
    assert history_frame.region == preferred_region
    assert history_controls.region.y == controls_y
    next_button = app.screen.query_one("#command-history-next", Button)
    assert not next_button.disabled, _rendered_text(page)
    next_button.focus()
    await pilot.press("p")
    await wait_until(lambda: "page 1" in _rendered_text(page), timeout=2)
    await pilot.press("n")
    await wait_until(lambda: "page 2" in _rendered_text(page), timeout=2)
    await pilot.press("n")
    await wait_until(lambda: "page 3" in _rendered_text(page), timeout=2)
    assert history_frame.region == preferred_region
    assert history_controls.region.y == controls_y
    assert app.screen.query_one("#command-history-next", Button).disabled
    await pilot.press("n")
    assert "page 3" in _rendered_text(page)
    assert f"#{pending_id} · Pending" in _rendered_text(history_copy), (
        _rendered_text(page),
        _rendered_text(history_copy),
    )
    _assert_last_command_history_page(page)
    history_scroll.scroll_to(y=history_scroll.max_scroll_y, animate=False)
    await pilot.pause()
    assert history_scroll.scroll_y == history_scroll.max_scroll_y > 0
    focus_before_refresh = app.focused
    engine = database.connect()
    try:
        CommandQueue(engine, clock).mark_done(pending_id, {"effect": "applied once"})
    finally:
        engine.close()
    await wait_until(
        lambda: (
            f"#{pending_id} · Applied" in _rendered_text(history_copy)
            and "applied once" in _rendered_text(history_copy)
            and '"effect": "applied once"' in _rendered_text(history_copy)
        ),
        timeout=2,
    )
    await pilot.pause()
    assert app.focused is focus_before_refresh
    assert history_frame.region == preferred_region
    assert history_controls.region.y == controls_y
    assert history_scroll.scroll_y == history_scroll.max_scroll_y
    await _resize_command_history_to_compact_and_close(
        app,
        pilot,
        home_screen=home_screen,
        frame=history_frame,
        controls=history_controls,
        scroll=history_scroll,
        controls_y=controls_y,
        preferred_region=preferred_region,
        reading_position=history_scroll.scroll_y,
        focused_widget=focus_before_refresh,
    )


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
    applied = queue.append(Stop())
    queue.mark_done(applied.id, {"effect": 0})
    for index in range(1, 51):
        applied = queue.append(Stop())
        queue.mark_done(applied.id, {"effect": index})

    try:
        app = _app(reader, project_dir, widget_names=[])
        async with app.run_test(size=_COMMAND_HISTORY_INITIAL_SIZE) as pilot:
            home_screen = app.screen
            await _choose_focus_view(app, pilot, "Command history · pending, applied and rejected")
            await wait_until(lambda: bool(app.screen.query("#command-history-page")), timeout=2)
            history_frame = app.screen.query_one("#command-history-frame")
            history_controls = app.screen.query_one("#command-history-controls")
            history_copy = app.screen.query_one("#command-history-copy", Static)
            history_scroll = app.screen.query_one("#command-history-scroll", VerticalScroll)
            _assert_command_history_controls_reachable(
                app.screen,
                history_frame,
                history_controls,
            )
            await _assert_popup_close_reachable(
                app.screen,
                close_selector="#command-history-close",
                frame_selector="#command-history-frame",
                body_selector="#command-history-scroll",
            )
            page = app.screen.query_one("#command-history-page", Static)
            await wait_until(lambda: "page 1" in _rendered_text(page), timeout=2)
            preferred_region = history_frame.region
            controls_y = history_controls.region.y
            assert f"#{pending.id} · Pending" not in _rendered_text(history_copy)
            await _assert_command_history_initial_shortcuts(
                app,
                pilot,
                page,
                history_copy,
                pending.id,
                history_controls=history_controls,
                controls_y=controls_y,
                history_frame=history_frame,
                preferred_region=preferred_region,
            )
            await _assert_command_history_filter_shortcuts(
                app,
                pilot,
                page,
                history_copy,
                applied.id,
                history_controls=history_controls,
                controls_y=controls_y,
                history_frame=history_frame,
                preferred_region=preferred_region,
            )
            assert history_frame.region == preferred_region
            await _assert_command_history_last_page_and_refresh(
                app,
                pilot,
                database=database,
                clock=clock,
                pending_id=pending.id,
                home_screen=home_screen,
                history_frame=history_frame,
                history_controls=history_controls,
                controls_y=controls_y,
                history_scroll=history_scroll,
                preferred_region=preferred_region,
            )
        for initial_size in (_TINY_HOME_SIZE, _SHORT_HOME_SIZE):
            await _assert_initial_command_history_size_restoration(
                reader,
                project_dir,
                connection,
                clock,
                initial_size,
            )
    finally:
        reader.close()


@pytest.mark.usefixtures("connection")
async def _assert_initial_command_history_size_restoration(
    reader: StateReader,
    project_dir: Path,
    connection: Connection,
    clock: SystemClock,
    initial_size: tuple[int, int],
) -> None:
    app = _app(reader, project_dir, widget_names=[])
    queue = CommandQueue(connection, clock)
    async with app.run_test(size=initial_size) as pilot:
        home_screen = app.screen
        await _choose_focus_view(app, pilot, "Command history · pending, applied and rejected")
        await wait_until(lambda: bool(app.screen.query("#command-history-page")), timeout=2)
        initial_geometry = await _assert_initial_history_pages(app, pilot)
        await _assert_initial_history_empty_size_restoration(
            app,
            pilot,
            initial_size,
            initial_geometry,
        )
        await _assert_initial_history_live_restoration(
            app,
            pilot,
            queue,
            initial_size,
            initial_geometry,
        )
        assert await pilot.click("#command-history-close")
        await wait_until(lambda: app.screen is home_screen, timeout=2)


async def _assert_initial_history_pages(
    app: DashboardApp,
    pilot: Pilot[None],
) -> tuple[Region, int, int]:
    screen = app.screen
    frame = screen.query_one("#command-history-frame")
    controls = screen.query_one("#command-history-controls")
    body = screen.query_one("#command-history-scroll", VerticalScroll)
    copy = screen.query_one("#command-history-copy", Static)
    next_page = screen.query_one("#command-history-next", Button)
    await wait_until(lambda: body.virtual_size.height > 1, timeout=2)
    _assert_command_history_controls_reachable(screen, frame, controls)
    _assert_command_history_title_visible(screen, frame)
    await _assert_popup_close_reachable(
        screen,
        close_selector="#command-history-close",
        frame_selector="#command-history-frame",
        body_selector="#command-history-scroll",
    )
    initial_region = frame.region
    initial_controls_y = controls.region.y
    initial_content_height = body.virtual_size.height
    while not next_page.disabled:
        previous_content = _rendered_text(copy)
        next_page.focus()
        await pilot.press("n")
        await wait_until(
            lambda previous_content=previous_content: _rendered_text(copy) != previous_content,
            timeout=2,
        )
        assert frame.region == initial_region
        assert controls.region.y == initial_controls_y
        _assert_command_history_controls_reachable(screen, frame, controls)
        _assert_command_history_title_visible(screen, frame)
    assert body.virtual_size.height < initial_content_height
    return initial_region, initial_controls_y, initial_content_height


async def _assert_initial_history_empty_size_restoration(
    app: DashboardApp,
    pilot: Pilot[None],
    initial_size: tuple[int, int],
    initial_geometry: tuple[Region, int, int],
) -> None:
    screen = app.screen
    frame = screen.query_one("#command-history-frame")
    controls = screen.query_one("#command-history-controls")
    body = screen.query_one("#command-history-scroll", VerticalScroll)
    next_page = screen.query_one("#command-history-next", Button)
    initial_region, initial_controls_y, initial_content_height = initial_geometry
    await pilot.resize_terminal(*_COMMAND_HISTORY_INITIAL_SIZE)
    await wait_until(
        lambda: (
            app.size == _COMMAND_HISTORY_INITIAL_SIZE
            and frame.region.width > initial_region.width
            and frame.region.height > initial_region.height
            and frame.content_region.contains_region(controls.region)
        ),
        timeout=2,
    )
    preferred_region = frame.region
    history_filter = cast("Select[str]", screen.query_one("#command-history-filter", Select))
    history_filter.value = CommandFeedbackStatus.REJECTED.value
    await wait_until(
        lambda: (
            history_filter.value == CommandFeedbackStatus.REJECTED.value
            and screen.query_one("#command-history-previous", Button).disabled
            and next_page.disabled
            and body.virtual_size.height < initial_content_height
        ),
        timeout=2,
    )
    assert frame.region == preferred_region
    _assert_command_history_controls_reachable(screen, frame, controls)
    _assert_command_history_title_visible(screen, frame)
    await pilot.resize_terminal(*initial_size)
    await wait_until(
        lambda: (
            app.size == initial_size
            and frame.region.width < preferred_region.width
            and frame.region.height < preferred_region.height
            and frame.content_region.contains_region(controls.region)
        ),
        timeout=2,
    )
    assert frame.region == initial_region
    assert controls.region.y == initial_controls_y
    _assert_command_history_title_visible(screen, frame)
    await pilot.resize_terminal(*_COMMAND_HISTORY_INITIAL_SIZE)
    await wait_until(lambda: frame.region == preferred_region, timeout=2)


async def _assert_initial_history_live_restoration(
    app: DashboardApp,
    pilot: Pilot[None],
    queue: CommandQueue,
    initial_size: tuple[int, int],
    initial_geometry: tuple[Region, int, int],
) -> None:
    screen = app.screen
    frame = screen.query_one("#command-history-frame")
    controls = screen.query_one("#command-history-controls")
    body = screen.query_one("#command-history-scroll", VerticalScroll)
    copy = screen.query_one("#command-history-copy", Static)
    initial_region, initial_controls_y, _ = initial_geometry
    preferred_region = frame.region
    history_filter = cast("Select[str]", screen.query_one("#command-history-filter", Select))
    next_page = screen.query_one("#command-history-next", Button)
    history_filter.value = "all"
    await wait_until(
        lambda: (
            history_filter.value == "all"
            and not next_page.disabled
            and body.virtual_size.height > 1
        ),
        timeout=2,
    )
    await pilot.resize_terminal(*initial_size)
    await wait_until(
        lambda: (
            app.size == initial_size
            and frame.region == initial_region
            and frame.content_region.contains_region(controls.region)
        ),
        timeout=2,
    )
    live_entry = queue.append(Stop())
    await wait_until(
        lambda: f"#{live_entry.id} · Pending" in _rendered_text(copy),
        timeout=2,
    )
    assert frame.region == initial_region
    assert controls.region.y == initial_controls_y
    _assert_command_history_title_visible(screen, frame)
    await pilot.resize_terminal(*_COMMAND_HISTORY_INITIAL_SIZE)
    await wait_until(
        lambda: (
            frame.region == preferred_region
            and f"#{live_entry.id} · Pending" in _rendered_text(copy)
        ),
        timeout=2,
    )
    _assert_command_history_controls_reachable(screen, frame, controls)
    _assert_command_history_title_visible(screen, frame)


async def _assert_task_timeline_page_shortcuts(app: DashboardApp, pilot: Pilot[None]) -> None:
    timeline_category = cast(
        "Select[str]", app.screen.query_one("#task-detail-timeline-category", Select)
    )
    timeline_category.value = TaskTimelineCategory.ALL.value
    timeline_status = app.screen.query_one("#task-detail-timeline-status", Static)
    timeline_body = app.screen.query_one("#task-detail-body", TaskDetailBody)
    previous = app.screen.query_one(f"#{TASK_DETAIL_TIMELINE_NEWER}", Button)
    next_page = app.screen.query_one(f"#{TASK_DETAIL_TIMELINE_OLDER}", Button)
    await wait_until(lambda: not next_page.disabled, timeout=2)
    assert previous.disabled
    assert "Recorded page route evidence 1" in _rendered_text(
        app.screen.query_one("#task-detail-timeline-selected", Static)
    )
    timeline_body.focus()
    await pilot.press("p")
    assert "Following latest" in _rendered_text(timeline_status)
    await pilot.press("n")
    await wait_until(
        lambda: "Follow paused while viewing older evidence" in _rendered_text(timeline_status),
        timeout=2,
    )
    assert next_page.disabled
    assert not previous.disabled
    await pilot.press("n")
    assert "Follow paused while viewing older evidence" in _rendered_text(timeline_status)
    await pilot.press("p")
    await wait_until(lambda: "Following latest" in _rendered_text(timeline_status), timeout=2)
    assert previous.disabled
    await pilot.click(next_page)
    await wait_until(
        lambda: "Follow paused while viewing older evidence" in _rendered_text(timeline_status),
        timeout=2,
    )
    await pilot.click(previous)
    await wait_until(lambda: "Following latest" in _rendered_text(timeline_status), timeout=2)
    timeline_turn = app.screen.query_one("#task-detail-timeline-turn", Input)
    status_before_editing = _rendered_text(timeline_status)
    previous_disabled_before_editing = previous.disabled
    next_disabled_before_editing = next_page.disabled
    timeline_turn.value = "3"
    timeline_turn.focus()
    await pilot.press("right", TASK_DETAIL_PREVIOUS_PAGE_KEY, TASK_DETAIL_NEXT_PAGE_KEY)
    await wait_until(lambda: timeline_turn.value == "3pn", timeout=2)
    await pilot.wait_for_scheduled_animations()
    assert _rendered_text(timeline_status) == status_before_editing
    assert previous.disabled is previous_disabled_before_editing
    assert next_page.disabled is next_disabled_before_editing
    timeline_body.focus()


async def _assert_task_relationship_page_shortcuts(app: DashboardApp, pilot: Pilot[None]) -> None:
    page = app.screen.query_one(f"#{TASK_DETAIL_RELATIONSHIP_PAGE}", Static)
    body = app.screen.query_one("#task-detail-body", TaskDetailBody)
    previous = app.screen.query_one(f"#{TASK_DETAIL_PREVIOUS_RELATIONSHIPS}", Button)
    next_page = app.screen.query_one(f"#{TASK_DETAIL_MORE_RELATIONSHIPS}", Button)
    await wait_until(
        lambda: "Page 1" in _rendered_text(page) and not next_page.disabled,
        timeout=2,
    )
    assert previous.disabled
    body.focus()
    await pilot.press("p")
    assert "Page 1" in _rendered_text(page)
    await pilot.press("n")
    await wait_until(lambda: "Page 2" in _rendered_text(page), timeout=2)
    assert next_page.disabled
    assert not previous.disabled
    await pilot.press("n")
    assert "Page 2" in _rendered_text(page)
    await pilot.press("p")
    await wait_until(lambda: "Page 1" in _rendered_text(page), timeout=2)
    body.scroll_to_widget(next_page, animate=False)
    await wait_until(lambda: body.can_view_entire(next_page), timeout=2)
    await pilot.pause()
    await pilot.click(next_page)
    await wait_until(lambda: "Page 2" in _rendered_text(page), timeout=2)
    body.scroll_to_widget(previous, animate=False)
    await wait_until(lambda: body.can_view_entire(previous), timeout=2)
    await pilot.pause()
    await pilot.click(previous)
    await wait_until(lambda: "Page 1" in _rendered_text(page), timeout=2)


async def test_task_detail_page_shortcuts_match_page_buttons_and_stop_at_boundaries(
    database: Database,
    connection: Connection,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = SystemClock()
    task = seed_task(connection, clock, title="Page route target")
    tasks = TaskStore(connection, clock)
    for index in range(TASK_PAGE_TEST_RELATIONSHIP_COUNT):
        dependent = seed_task(connection, clock, title=f"Page route dependent {index}")
        tasks.set_depends_on(dependent.id, (task.id,))
    events = EventLog(connection, clock)
    for index in range(TASK_PAGE_TEST_EVENT_COUNT):
        events.append(
            EventKind.NUDGE,
            task_id=task.id,
            message=f"Recorded page route evidence {index}",
        )
    settings = load_settings(project_dir)
    dashboard = settings.dashboard.model_copy(
        update={"animations": False, "timeline_page_size": TASK_PAGE_TEST_TIMELINE_PAGE_SIZE}
    )
    settings = settings.model_copy(update={"dashboard": dashboard})
    reader = StateReader(database, clock)

    try:
        app = _app(
            reader,
            project_dir,
            widget_names=[],
            focus_task=task.id,
            settings=settings,
        )
        async with app.run_test(size=(100, 34)) as pilot:
            await pilot.press("e")
            await wait_until(lambda: isinstance(app.screen, TaskDetailScreen), timeout=2)
            panel = app.screen.query_one("#task-detail-panel", TaskDetailPanel)
            await wait_until(lambda: panel.active_view == TASK_VIEW_EVIDENCE, timeout=2)
            await _assert_task_timeline_page_shortcuts(app, pilot)
            await pilot.press("v")
            await wait_until(lambda: bool(app.screen.query("#task-detail-view-choice")), timeout=2)
            view_choice = cast(
                "Select[str]", app.screen.query_one("#task-detail-view-choice", Select)
            )
            view_choice.value = TASK_VIEW_RELATIONSHIPS
            await pilot.press("tab", "enter")
            await wait_until(lambda: panel.active_view == TASK_VIEW_RELATIONSHIPS, timeout=2)
            await _assert_task_relationship_page_shortcuts(app, pilot)
    finally:
        reader.close()


async def _resize_command_history_to_compact_and_close(
    app: DashboardApp,
    pilot: Pilot[None],
    *,
    home_screen: Widget,
    frame: Widget,
    controls: Widget,
    scroll: VerticalScroll,
    controls_y: int,
    preferred_region: Region,
    reading_position: float,
    focused_widget: Widget | None,
) -> None:
    await pilot.resize_terminal(*_COMPACT_HOME_SIZE)
    await wait_until(
        lambda: (
            app.size == _COMPACT_HOME_SIZE
            and frame.content_region.contains_region(controls.region)
            and app.screen.region.contains_region(controls.region)
        ),
        timeout=2,
    )
    await _assert_popup_close_reachable(
        app.screen,
        close_selector="#command-history-close",
        frame_selector="#command-history-frame",
        body_selector="#command-history-scroll",
    )
    assert frame.region.height < preferred_region.height
    assert scroll.max_scroll_y > 0
    await pilot.resize_terminal(*_TINY_HOME_SIZE)
    await wait_until(
        lambda: (
            app.size == _TINY_HOME_SIZE
            and frame.content_region.contains_region(controls.region)
            and app.screen.region.contains_region(controls.region)
        ),
        timeout=2,
    )
    await _assert_popup_close_reachable(
        app.screen,
        close_selector="#command-history-close",
        frame_selector="#command-history-frame",
        body_selector="#command-history-scroll",
    )
    previous = app.screen.query_one("#command-history-previous", Button)
    next_page = app.screen.query_one("#command-history-next", Button)
    close = app.screen.query_one("#command-history-close", Button)
    _assert_command_history_title_visible(app.screen, frame)
    assert frame.content_region.contains_region(close.region)
    assert previous.is_on_screen
    assert next_page.is_on_screen
    assert frame.region.height < preferred_region.height
    assert await pilot.click(previous)
    await wait_until(
        lambda: (
            "#32 · Applied" in _rendered_text(app.screen.query_one("#command-history-copy", Static))
        ),
        timeout=2,
    )
    assert await pilot.click(next_page)
    await wait_until(
        lambda: (
            "#12 · Applied" in _rendered_text(app.screen.query_one("#command-history-copy", Static))
        ),
        timeout=2,
    )
    await pilot.resize_terminal(*_COMMAND_HISTORY_INITIAL_SIZE)
    await wait_until(
        lambda: app.size == _COMMAND_HISTORY_INITIAL_SIZE and frame.region == preferred_region,
        timeout=2,
    )
    await wait_until(lambda: scroll.scroll_y == reading_position, timeout=2)
    await wait_until(lambda: app.focused is focused_widget, timeout=2)
    assert frame.region == preferred_region
    assert controls.region.y == controls_y
    assert await pilot.click("#command-history-close")
    await wait_until(lambda: app.screen is home_screen, timeout=2)


def _assert_command_history_controls_reachable(
    screen: Widget,
    frame: Widget,
    controls: Widget,
) -> None:
    assert frame.content_region.contains_region(controls.region)
    assert screen.region.contains_region(controls.region)


def _assert_command_history_title_visible(screen: Widget, frame: Widget) -> None:
    title = screen.query_one("#command-history-title", Label)
    header = screen.query_one("#command-history-header", Horizontal)
    assert title.is_on_screen
    assert _rendered_widget_lines(title)
    assert header.region.contains_region(title.region)
    assert frame.content_region.contains_region(title.region)


@pytest.mark.parametrize("size", [_COMPACT_HOME_SIZE, _TINY_HOME_SIZE])
async def test_narrow_home_keeps_keyboard_routes_reachable(
    database: Database,
    connection: Connection,
    tmp_path: Path,
    size: tuple[int, int],
) -> None:
    width, height = size
    project_dir = write_minimal_project(tmp_path)
    clock = SystemClock()
    reader = StateReader(database, clock)
    task = seed_task(connection, clock, title="Task120 native Home task")
    queue = CommandQueue(connection, clock)
    for submitted in queue.pending():
        queue.mark_done(submitted.id, {"task_id": task.id})
    for _ in range(3):
        queue.append(Stop())

    try:
        app = _app(reader, project_dir, widget_names=["header", "queue"])
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            status = app.query_one("#workspace-command-status", Button)
            footer = app.query_one("#focus-footer", Grid)
            assert footer.display is (height >= 9)
            assert not status.is_on_screen if height < 9 else status.region.bottom <= height
            assert footer.region.right <= width
            home_screen = app.screen
            if height >= 9:
                assert status.display
                await pilot.click(status)
                await wait_until(lambda: app.screen is not home_screen, timeout=2)
                history_screen = app.screen
                history_copy = history_screen.query_one("#command-history-copy", Static)
                await wait_until(lambda: "#4 · Pending" in _rendered_text(history_copy), timeout=2)
                await pilot.press("escape")
                await wait_until(lambda: app.screen is home_screen, timeout=2)
            else:
                assert not status.is_on_screen
            await _choose_focus_view(app, pilot, "Command history · pending, applied and rejected")
            await wait_until(lambda: bool(app.screen.query("#command-history-page")), timeout=2)
            page = cast("Static", app.screen.query_one("#command-history-page"))
            frame = app.screen.query_one("#command-history-frame")
            if page.display:
                assert page.is_on_screen
                assert frame.content_region.contains_region(page.region)
            history_copy = app.screen.query_one("#command-history-copy", Static)
            await wait_until(lambda: "· Pending" in _rendered_text(history_copy), timeout=2)
            if size == _TINY_HOME_SIZE:
                controls = app.screen.query_one("#command-history-controls")
                _assert_command_history_controls_reachable(app.screen, frame, controls)
                await _assert_popup_close_reachable(
                    app.screen,
                    close_selector="#command-history-close",
                    frame_selector="#command-history-frame",
                    body_selector="#command-history-scroll",
                )
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
        _MEASURED_HOME_SIZE,
        _WIDE_SHORT_HOME_SIZE,
        _SPLIT_BOUNDARY_HOME_SIZE,
        _STACKED_UPPER_BOUNDARY_HOME_SIZE,
        _STACKED_INSPECTOR_HOME_SIZE,
        _STACKED_LOWER_BOUNDARY_HOME_SIZE,
        _LIST_ONLY_BOUNDARY_HOME_SIZE,
        _COMPACT_HOME_SIZE,
        _SHORT_HOME_SIZE,
        _NARROW_TASK_SIZE,
        _COMPACT_SHORT_WIDE_HOME_SIZE,
        _TINY_HOME_SIZE,
    ):
        await pilot.resize_terminal(width, height)
        await pilot.pause()
        assert app.focused is search
        assert search.value == task.id
        assert task.title in _rendered_text(identity)
        assert home_layout.region.width <= width
        should_show_inspector = True
        assert detail_panel.display is should_show_inspector
        if width >= 73:
            assert queue_panel.region.right <= detail_panel.region.x
        elif should_show_inspector:
            assert detail_panel.region.x == queue_panel.region.x
            assert detail_panel.region.y >= queue_panel.region.bottom, (
                width,
                home_layout.classes,
                home_layout.styles.layout,
                queue_panel.region,
                detail_panel.region,
            )
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
    root.scroll_to_widget(selected_item, animate=False, immediate=True)
    await wait_until(
        lambda: (
            selected_item.region.y >= root.scrollable_content_region.y
            and selected_item.region.bottom <= root.scrollable_content_region.bottom
        ),
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    assert root.scroll_y <= root.max_scroll_y
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
    await pilot.pause()
    assert app.screen is home_screen
    detail_identity = app.query_one("#task-detail-identity", Static)
    assert task.id in _rendered_text(detail_identity)
    assert task.title in _rendered_text(detail_identity)
    root.scroll_to_widget(detail_identity, animate=False, immediate=True)
    await wait_until(
        lambda: detail_identity.region.overlaps(root.scrollable_content_region),
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    assert task.id in _rendered_text(detail_identity)
    assert task.title in _rendered_text(detail_identity)
    await pilot.press("escape")
    await wait_until(
        lambda: app.screen is home_screen and list_view.index is None,
        timeout=2,
    )
    assert app.focused is list_view
    assert app.query_one("#task-browser-search", Input).value == task.id
    assert task.id not in _rendered_text(detail_identity)


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


async def test_home_row_click_and_enter_keep_the_shared_inspector_on_home(
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
            await pilot.press("enter")
            await pilot.pause()
            assert app.screen is home
            assert first_id in _rendered_text(app.query_one(f"#{TASK_DETAIL_IDENTITY}", Static))
            assert (
                reader.command_history(limit=COMMAND_HISTORY_TEST_LIMIT).total_count
                == before_commands
            )

            await pilot.press("escape")
            await wait_until(
                lambda: app.screen is home and list_view.index is None,
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            assert app.focused is list_view
            assert search.value == "Focused route"
            assert first_id not in _rendered_text(app.query_one(f"#{TASK_DETAIL_IDENTITY}", Static))
            assert (
                reader.command_history(limit=COMMAND_HISTORY_TEST_LIMIT).total_count
                == before_commands
            )
            await _select_home_task(app, first_id)
            await _assert_home_inline_secondary_routes(
                app,
                pilot,
                first_id,
                reader,
                before_commands,
            )
    finally:
        reader.close()


async def _exercise_contextual_draft_and_evidence_route(
    app: DashboardApp,
    pilot: Pilot[None],
    task: Task,
    queue: CommandQueue,
) -> None:
    home = app.screen
    search = app.query_one("#task-browser-search", Input)
    search.value = task.title
    list_view = app.query_one("#task-browser-list", ListView)
    await wait_until(
        lambda: bool(app.query(f"#task-row-{task.id}")),
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    await _select_home_task(app, task.id)
    list_view.focus()
    await pilot.click("#focus-footer-actions")
    await wait_until(
        lambda: isinstance(app.screen, PaletteScreen),
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    palette = cast("PaletteScreen", app.screen)
    context = _rendered_text(palette.query_one("#palette-menu-context", Static))
    assert task.id in context
    assert "Leader" in context
    assert palette.query_one("#palette-menu-view-evidence").display
    assert palette.query_one("#palette-menu-view-usage").display
    assert not palette.query_one("#palette-menu-task-views").display
    assert palette.query_one("#palette-menu-diagnostics").display
    await _click_palette_menu_button(pilot, palette, "palette-menu-more-actions")
    await wait_until(
        lambda: palette.query_one("#palette-menu-task-views").display,
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    assert palette.query_one("#palette-menu-view-secondary").display
    await _click_palette_menu_button(pilot, palette, "palette-menu-action-message")
    guidance = "Retain this task scoped guidance after closing."
    await pilot.press(*guidance)
    await _resize_contextual_message_form(app, pilot, palette, guidance)
    await pilot.click("#palette-back-to-menu")
    await _click_palette_menu_button(pilot, palette, "palette-menu-view-evidence")
    await wait_until(
        lambda: isinstance(app.screen, TaskDetailScreen),
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    evidence_screen = app.screen
    evidence_panel = evidence_screen.query_one("#task-detail-panel", TaskDetailPanel)
    await wait_until(
        lambda: evidence_panel.active_view == TASK_VIEW_EVIDENCE,
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    assert task.id in _rendered_text(evidence_screen.query_one(f"#{TASK_DETAIL_IDENTITY}", Static))
    assert queue.pending() == []
    await pilot.press("escape")
    await wait_until(
        lambda: app.screen is home,
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    list_view.focus()
    await pilot.press("ctrl+p")
    await wait_until(
        lambda: isinstance(app.screen, PaletteScreen),
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    palette = cast("PaletteScreen", app.screen)
    assert task.id in _rendered_text(palette.query_one("#palette-menu-context", Static))
    await _click_palette_menu_button(pilot, palette, "palette-menu-action-message")
    await wait_until(
        lambda: palette.query_one("#message", TextArea).text == guidance,
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    await pilot.click("#palette-close")
    await wait_until(lambda: app.screen is home, timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS)
    assert search.value == task.title
    assert queue.pending() == []


async def _resize_contextual_message_form(
    app: DashboardApp,
    pilot: Pilot[None],
    palette: PaletteScreen,
    guidance: str,
) -> None:
    message = palette.query_one("#message", TextArea)
    frame = palette.query_one("#palette-frame")
    cursor = message.cursor_location
    for width, height in (
        *_PALETTE_ZERO_WIDTH_REPRODUCTION_SIZES,
        (48, 20),
        (36, 14),
        _TINY_HOME_SIZE,
    ):
        await pilot.resize_terminal(width, height)
        if (width, height) == _PALETTE_ZERO_WIDTH_SIZE:
            await pilot.pause()
            assert message.text == guidance
            continue
        await wait_until(
            lambda: app.focused is message and message.cursor_location == cursor,
            timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
        )
        for button_id in ("palette-back-to-menu", "palette-submit"):
            button = palette.query_one(f"#{button_id}", Button)
            assert button.display
            assert frame.content_region.contains_region(button.region)
            assert button.region.right <= width
            assert button.region.bottom < height
    await pilot.resize_terminal(*_STANDARD_HOME_SIZE)
    await wait_until(
        lambda: app.focused is message and message.cursor_location == cursor,
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    assert message.text == guidance


async def _resize_blank_palette_text_area(
    app: DashboardApp,
    pilot: Pilot[None],
    palette: PaletteScreen,
    text_area: TextArea,
    queue: CommandQueue,
) -> None:
    for size in (*_PALETTE_ZERO_WIDTH_REPRODUCTION_SIZES, _TINY_HOME_SIZE, _WIDE_HOME_SIZE):
        await pilot.resize_terminal(*size)
        await pilot.pause()
        assert text_area.text == ""
        assert queue.pending() == []
        if size != _PALETTE_ZERO_WIDTH_SIZE:
            assert app.focused is text_area
        if size == _TINY_HOME_SIZE:
            await _assert_popup_close_reachable(
                palette,
                close_selector="#palette-close",
                frame_selector="#palette-frame",
                body_selector="#palette-form",
            )
            frame = palette.query_one("#palette-frame")
            for button_id in ("palette-back-to-menu", "palette-submit"):
                button = palette.query_one(f"#{button_id}", Button)
                assert button.display
                assert frame.content_region.contains_region(button.region)
                assert button.region.right <= _TINY_HOME_SIZE[0]
                assert button.region.bottom < _TINY_HOME_SIZE[1]
            if text_area.id == "message":
                role = cast("Select[str]", palette.query_one("#role", Select))
                role.focus()
                await pilot.press("enter")
                await wait_until(
                    lambda target=role: target.expanded,
                    timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
                )
                await pilot.press("escape")
                await wait_until(
                    lambda target=role: not target.expanded,
                    timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
                )
                assert app.screen is palette
                text_area.focus()


async def _exercise_home_help_route(app: DashboardApp, pilot: Pilot[None], task: Task) -> None:
    home = app.screen
    search = app.query_one("#task-browser-search", Input)
    await pilot.click("#focus-footer-help")
    await wait_until(
        lambda: bool(app.screen.query("#workspace-help-copy")),
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    await pilot.press("escape")
    await wait_until(lambda: app.screen is home, timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS)
    assert app.focused is not None
    assert app.focused.id == "focus-footer-help"
    assert search.value == task.title


async def _exercise_inspector_mouse_and_native_escape(
    app: DashboardApp,
    pilot: Pilot[None],
    task: Task,
    queue: CommandQueue,
) -> None:
    home = app.screen
    search = app.query_one("#task-browser-search", Input)
    await _select_home_task(app, task.id)
    assert task.id in _rendered_text(app.query_one(f"#{TASK_DETAIL_IDENTITY}", Static))
    app.query_one(f"#{TASK_DETAIL_EVIDENCE_INLINE_BUTTON}", Button).focus()
    await pilot.press("ctrl+p")
    await wait_until(
        lambda: isinstance(app.screen, PaletteScreen),
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    palette = cast("PaletteScreen", app.screen)
    assert task.id in _rendered_text(palette.query_one("#palette-menu-context", Static))
    await _click_palette_menu_button(pilot, palette, "palette-menu-action-message")
    role = cast("Select[str]", palette.query_one("#role", Select))
    role.focus()
    await pilot.press("enter")
    await wait_until(lambda: role.expanded, timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS)
    await pilot.press("ctrl+p")
    assert app.screen is palette
    assert role.expanded
    await pilot.press("escape")
    await wait_until(lambda: not role.expanded, timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS)
    assert app.screen is palette
    await pilot.press("escape")
    await wait_until(
        lambda: app.screen is home,
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    assert task.id in _rendered_text(app.query_one(f"#{TASK_DETAIL_IDENTITY}", Static))
    await pilot.press("escape")
    await wait_until(
        lambda: (
            app.screen is home
            and task.id not in _rendered_text(app.query_one(f"#{TASK_DETAIL_IDENTITY}", Static))
        ),
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    assert search.value == task.title
    assert app.query_one("#task-browser-list", ListView).index is None
    assert queue.pending() == []


async def _open_task_context_menu(
    app: DashboardApp,
    pilot: Pilot[None],
    list_view: ListView,
) -> PaletteScreen:
    list_view.focus()
    await pilot.press("ctrl+p")
    await wait_until(
        lambda: isinstance(app.screen, PaletteScreen),
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    palette = cast("PaletteScreen", app.screen)
    await _assert_popup_close_reachable(
        palette,
        close_selector="#palette-close",
        frame_selector="#palette-frame",
        body_selector="#palette-menu",
    )
    return palette


async def _exercise_task_secondary_menu_routes(
    app: DashboardApp,
    pilot: Pilot[None],
    task: Task,
    queue: CommandQueue,
) -> None:
    home_screen = app.screen
    search = app.query_one("#task-browser-search", Input)
    list_view = app.query_one("#task-browser-list", ListView)
    await _select_home_task(app, task.id)
    for route_button, expected_view in [
        ("#palette-menu-view-evidence", TASK_VIEW_EVIDENCE),
        ("#palette-menu-view-usage", TASK_VIEW_USAGE),
    ]:
        await _open_task_context_menu(app, pilot, list_view)
        await pilot.click(route_button)
        await wait_until(
            lambda: isinstance(app.screen, TaskDetailScreen),
            timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
        )
        detail_screen = app.screen
        detail_panel = detail_screen.query_one("#task-detail-panel", TaskDetailPanel)
        await wait_until(
            lambda expected_view=expected_view, detail_panel=detail_panel: (
                detail_panel.active_view == expected_view
            ),
            timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
        )
        assert search.value == task.title
        assert queue.pending() == []
        await pilot.press("escape")
        await wait_until(
            lambda: app.screen is home_screen,
            timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
        )

    await _open_task_context_menu(app, pilot, list_view)
    assert not app.screen.query("#palette-menu-inspect")
    await pilot.click("#palette-menu-commands")
    await wait_until(
        lambda: bool(app.screen.query("#command-history-page")),
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    assert queue.pending() == []
    await pilot.press("escape")
    await wait_until(lambda: app.screen is home_screen, timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS)

    await _open_task_context_menu(app, pilot, list_view)
    await pilot.click("#palette-menu-diagnostics")
    await wait_until(
        lambda: bool(app.screen.query("#context-widget-frame")),
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    widget_screen = app.screen
    await _assert_popup_close_reachable(
        widget_screen,
        close_selector="#context-widget-close",
        frame_selector="#context-widget-frame",
        body_selector="#context-widget-content",
    )
    assert _rendered_text(widget_screen.query_one("#context-widget-title", Static)) == (
        "Project diagnostics"
    )
    assert queue.pending() == []
    await pilot.click("#context-widget-close")
    await wait_until(lambda: app.screen is home_screen, timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS)

    await _open_task_context_menu(app, pilot, list_view)
    menu = cast("PaletteScreen", app.screen).query_one("#palette-menu", VerticalScroll)
    menu.scroll_to_widget(
        menu.query_one("#palette-menu-view-secondary", Button),
        animate=False,
        immediate=True,
    )
    await pilot.pause()
    await _click_palette_menu_button(
        pilot,
        cast("PaletteScreen", app.screen),
        "palette-menu-view-secondary",
    )
    await wait_until(
        lambda: bool(app.screen.query("#task-detail-views-frame")),
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    views = app.screen
    views.query_one("#task-detail-view-choice", Select).value = "widget-faults"
    await pilot.click(f"#{TASK_DETAIL_VIEWS_OPEN_BUTTON}")
    await wait_until(
        lambda: bool(app.screen.query("#context-widget-frame")),
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    assert "faults" in _rendered_text(app.screen.query_one("#context-widget-title", Static))
    assert queue.pending() == []


async def test_task_context_menu_closes_and_opens_secondary_views_without_commands(
    database: Database,
    connection: Connection,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = SystemClock()
    task = seed_task(connection, clock, title="Context menu view target")
    queue = CommandQueue(connection, clock)
    for command in queue.pending():
        queue.mark_done(command.id)
    reader = StateReader(database, clock)
    app_instances: list[DashboardApp] = []

    async def open_palette_context(request: PaletteRequest) -> None:
        app = app_instances[0]
        palette = _palette_screen_for_app(
            reader,
            connection,
            project_dir,
            clock,
            request,
            remember_contextual_draft=app.remember_contextual_palette_draft,
            task_routes_available=True,
            project_diagnostics_available=True,
        )

        def after_close(route: TaskPaletteRoute | None) -> None:
            if route is None or request.task_id is None:
                app.refresh_task_detail_after_resume()
                return
            app.route_task_palette(request.task_id, route)

        await app.push_screen(palette, callback=after_close)

    try:
        app = _app(
            reader,
            project_dir,
            widget_names=["queue", "faults", "resources"],
            open_palette_context=open_palette_context,
            clock=clock,
        )
        app_instances.append(app)
        async with app.run_test(size=_MEASURED_HOME_SIZE) as pilot:
            await _exercise_contextual_draft_and_evidence_route(app, pilot, task, queue)
            await _exercise_home_help_route(app, pilot, task)
            await _exercise_inspector_mouse_and_native_escape(app, pilot, task, queue)
            await _exercise_task_secondary_menu_routes(app, pilot, task, queue)
    finally:
        reader.close()


async def _open_and_close_task_message_draft(
    app: DashboardApp,
    pilot: Pilot[None],
    task: Task,
) -> tuple[object, ListView, str]:
    home = app.screen
    search = app.query_one("#task-browser-search", Input)
    search.value = task.title
    list_view = app.query_one("#task-browser-list", ListView)
    await wait_until(
        lambda: bool(app.query(f"#task-row-{task.id}")),
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    await _select_home_task(app, task.id)
    list_view.focus()
    await pilot.press("ctrl+p")
    await wait_until(
        lambda: isinstance(app.screen, PaletteScreen),
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    palette = cast("PaletteScreen", app.screen)
    assert task.id in _rendered_text(palette.query_one("#palette-menu-context", Static))
    await _click_palette_menu_button(pilot, palette, "palette-menu-action-message")
    guidance = "Keep this task's message after a different decision route."
    message = palette.query_one("#message", TextArea)
    await wait_until(
        lambda: app.focused is message,
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    await pilot.press(*guidance)
    await pilot.press("escape")
    await wait_until(lambda: app.screen is home, timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS)
    return home, list_view, guidance


async def _open_home_task_action_menu(
    app: DashboardApp,
    pilot: Pilot[None],
    task: Task,
) -> PaletteScreen:
    await _select_home_task(app, task.id)
    app.query_one("#task-browser-list", ListView).focus()
    await pilot.press("ctrl+p")
    await wait_until(
        lambda: isinstance(app.screen, PaletteScreen),
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    return cast("PaletteScreen", app.screen)


async def _back_to_context_actions(
    pilot: Pilot[None],
    palette: PaletteScreen,
    queue: CommandQueue,
) -> None:
    await pilot.click("#palette-back-to-menu")
    await wait_until(
        lambda: palette.query_one("#palette-menu", VerticalScroll).display,
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    assert queue.pending() == []


async def _click_palette_menu_button(
    pilot: Pilot[None],
    palette: PaletteScreen,
    button_id: str,
) -> None:
    button = palette.query_one(f"#{button_id}", Button)
    menu = palette.query_one("#palette-menu", VerticalScroll)
    parent = button.parent
    while parent is not None and parent is not menu:
        if not parent.display:
            more_actions = palette.query_one("#palette-menu-more-actions", Button)
            menu.scroll_to_widget(more_actions, animate=False, immediate=True)
            await pilot.pause()
            await pilot.click(more_actions)
            await wait_until(
                lambda parent=parent: parent.display,
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            break
        parent = parent.parent
    menu.scroll_to_widget(button, animate=False, immediate=True)
    await pilot.pause()
    await pilot.click(button)


async def _close_palette_to_caller(
    app: DashboardApp,
    pilot: Pilot[None],
    caller: object,
    queue: CommandQueue,
) -> None:
    await pilot.press("escape")
    await wait_until(lambda: app.screen is caller, timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS)
    assert queue.pending() == []


async def test_contextual_message_draft_survives_back_action_change_and_another_tasks_answer(
    database: Database,
    connection: Connection,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = SystemClock()
    task = seed_task(connection, clock, title="Message draft caller task")
    role_task = seed_task(connection, clock, title="Back menu message draft", mode=Mode.IMPLEMENT)
    answer_task, _, escalation, queue = _seed_home_answer_decision(connection, clock)
    reader = StateReader(database, clock)
    app_instances: list[DashboardApp] = []

    async def open_palette_context(request: PaletteRequest) -> None:
        app = app_instances[0]
        palette = _palette_screen_for_app(
            reader,
            connection,
            project_dir,
            clock,
            request,
            remember_contextual_draft=app.remember_contextual_palette_draft,
            task_routes_available=True,
        )
        await app.push_screen(palette, callback=app.palette_return_callback(request.task_id))

    try:
        app = _app(
            reader,
            project_dir,
            widget_names=["queue", "escalations"],
            open_palette_context=open_palette_context,
            clock=clock,
        )
        app_instances.append(app)
        async with app.run_test(size=_WIDE_HOME_SIZE) as pilot:
            home, list_view, guidance = await _open_and_close_task_message_draft(
                app,
                pilot,
                task,
            )

            attention = app.query_one("#escalations", EscalationsPanel)
            answer_row = attention.query_one("#escalation-panels", VerticalScroll).query_one(Static)
            app.query_one("#task-browser-search", Input).value = answer_task.title
            await wait_until(
                lambda: bool(app.query(f"#task-row-{answer_task.id}")),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            await _select_home_task(app, answer_task.id)
            await wait_until(
                lambda: escalation.id in _rendered_text(answer_row),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            answer_palette = await _open_attention_answer_form(app, pilot, answer_row)
            answer = answer_palette.query_one("#resolution-text", TextArea)
            await wait_until(
                lambda: app.focused is answer,
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            await _resize_blank_palette_text_area(app, pilot, answer_palette, answer, queue)
            await pilot.press(*"Preserve the typed answer for the second task.")
            assert queue.pending() == []
            await pilot.press("escape")
            await wait_until(lambda: app.screen is home, timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS)

            app.query_one("#task-browser-search", Input).value = task.title
            await wait_until(
                lambda: bool(app.query(f"#task-row-{task.id}")),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            await _select_home_task(app, task.id)
            list_view.focus()
            await pilot.press("ctrl+p")
            await wait_until(
                lambda: isinstance(app.screen, PaletteScreen),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            message_palette = cast("PaletteScreen", app.screen)
            await _click_palette_menu_button(
                pilot,
                message_palette,
                "palette-menu-action-message",
            )
            await wait_until(
                lambda: message_palette.query_one("#message", TextArea).text == guidance,
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            assert cast("Select[str]", message_palette.query_one("#role", Select)).value == (
                Role.LEADER.value
            )
            assert queue.pending() == []
            await pilot.press("escape")
            await wait_until(lambda: app.screen is home, timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS)

            app.query_one("#task-browser-search", Input).value = role_task.title
            await wait_until(
                lambda: bool(app.query(f"#task-row-{role_task.id}")),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            await _exercise_back_and_action_change_draft(app, pilot, home, role_task, queue)
    finally:
        reader.close()


async def _exercise_back_and_action_change_draft(
    app: DashboardApp,
    pilot: Pilot[None],
    home: object,
    task: Task,
    queue: CommandQueue,
) -> None:
    palette = await _open_home_task_action_menu(app, pilot, task)
    await _click_palette_menu_button(pilot, palette, "palette-menu-action-message")
    role = cast("Select[str]", palette.query_one("#role", Select))
    role.focus()
    role.value = Role.IMPLEMENTER.value
    await wait_until(
        lambda: role.value == Role.IMPLEMENTER.value,
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    message = palette.query_one("#message", TextArea)
    message.focus()
    await _resize_blank_palette_text_area(app, pilot, palette, message, queue)
    assert role.value == Role.IMPLEMENTER.value
    guidance = "Keep the explicitly selected implementer message."
    await pilot.press(*guidance)
    assert queue.pending() == []

    await _exercise_submit_request_form_resize(app, pilot, palette, home, queue)

    palette = await _open_home_task_action_menu(app, pilot, task)
    await _click_palette_menu_button(pilot, palette, "palette-menu-action-message")
    message = palette.query_one("#message", TextArea)
    role = cast("Select[str]", palette.query_one("#role", Select))
    await wait_until(
        lambda: message.text == guidance and role.value == Role.IMPLEMENTER.value,
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    await _back_to_context_actions(pilot, palette, queue)
    await _close_palette_to_caller(app, pilot, home, queue)

    palette = await _open_home_task_action_menu(app, pilot, task)
    await _click_palette_menu_button(pilot, palette, "palette-menu-action-message")
    message = palette.query_one("#message", TextArea)
    role = cast("Select[str]", palette.query_one("#role", Select))
    await wait_until(
        lambda: message.text == guidance and role.value == Role.IMPLEMENTER.value,
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    await _close_palette_to_caller(app, pilot, home, queue)


async def _exercise_submit_request_form_resize(
    app: DashboardApp,
    pilot: Pilot[None],
    palette: PaletteScreen,
    home: object,
    queue: CommandQueue,
) -> None:
    await _back_to_context_actions(pilot, palette, queue)
    await _click_palette_menu_button(pilot, palette, "palette-menu-action-submit")
    await wait_until(
        lambda: palette.query_one("#palette-form", VerticalScroll).display,
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    request = palette.query_one("#request", TextArea)
    await wait_until(
        lambda: app.focused is request,
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    await _resize_blank_palette_text_area(app, pilot, palette, request, queue)
    await _close_palette_to_caller(app, pilot, home, queue)


@pytest.mark.parametrize("size", [_WIDE_HOME_SIZE, _COMPACT_HOME_SIZE, _NARROW_TASK_SIZE])
async def test_empty_home_footer_actions_are_mouse_reachable_and_read_only(
    database: Database,
    connection: Connection,
    tmp_path: Path,
    size: tuple[int, int],
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = SystemClock()
    reader = StateReader(database, clock)
    queue = CommandQueue(connection, clock)
    app_instances: list[DashboardApp] = []

    async def open_palette_context(request: PaletteRequest) -> None:
        app = app_instances[0]
        palette = _palette_screen_for_app(reader, connection, project_dir, clock, request)
        await app.push_screen(palette)

    try:
        app = _app(
            reader,
            project_dir,
            widget_names=["queue"],
            open_palette_context=open_palette_context,
        )
        app_instances.append(app)
        async with app.run_test(size=size) as pilot:
            home = app.screen
            search = app.query_one("#task-browser-search", Input)
            search.value = "no matching task"
            search.focus()
            await pilot.pause()
            detach = app.query_one("#focus-footer-detach", Button)
            assert detach.disabled
            assert not app.query("#focus-footer-inspect")
            for button_id in ("focus-footer-actions", "focus-footer-help"):
                button = app.query_one(f"#{button_id}", Button)
                assert button.is_on_screen
                assert button.region.right <= size[0]
            await pilot.click("#focus-footer-actions")
            await wait_until(
                lambda: isinstance(app.screen, PaletteScreen),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            palette = cast("PaletteScreen", app.screen)
            assert _rendered_text(palette.query_one("#palette-menu-context", Static)) == "Project"
            assert palette.query_one("#palette-menu-action-submit").display
            assert palette.query_one("#palette-menu-action-stop").display
            assert not palette.query_one("#palette-menu-task-actions").display
            assert not palette.query_one("#palette-menu-task-views").display
            assert queue.pending() == []
            await pilot.press("escape")
            await wait_until(lambda: app.screen is home, timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS)
            assert search.value == "no matching task"
            assert app.focused is not None
            assert app.focused.id == "focus-footer-actions"

            assert queue.pending() == []
    finally:
        reader.close()


async def test_home_footer_detach_uses_the_configured_prefix_and_action(
    database: Database,
    connection: Connection,
    tmp_path: Path,
    tmux_server: TmuxServer,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = SystemClock()
    CommandQueue(connection, clock).append(Stop())
    tmux = Tmux(tmux_server.runner, socket=tmux_server.socket_name)
    project_session = exact_target(_PROJECT_SESSION_NAME)
    unrelated_session = exact_target(tmux_server.session)
    await tmux_server.run("new-session", "-d", "-s", _PROJECT_SESSION_NAME, "-x", "80", "-y", "24")
    await tmux_server.run(
        "set-environment",
        "-t",
        project_session,
        PROJECT_DIR_ENV_VAR,
        str(project_dir),
    )
    project_pane = (
        await tmux_server.run("list-panes", "-s", "-t", project_session, "-F", "#{pane_id}")
    ).stdout.strip()
    await tmux_server.run("set-option", "-g", "prefix", _GLOBAL_TMUX_PREFIX)
    assert await tmux.current_prefix(project_session) == _GLOBAL_TMUX_PREFIX
    await tmux.set_session_option(project_session, "prefix", _SESSION_TMUX_PREFIX)
    reader = StateReader(database, clock)

    try:
        prefix_hint, detach_project_client = await dashboard_tmux_binding(
            tmux, project_pane, project_dir, _PROJECT_SESSION_NAME
        )
        assert prefix_hint == tmux_key_label(_SESSION_TMUX_PREFIX)
        assert prefix_hint is not None
        assert detach_project_client is not None
        async with (
            attach_tmux_client(tmux_server, project_session),
            attach_tmux_client(tmux_server, unrelated_session),
        ):
            app = _app(
                reader,
                project_dir,
                widget_names=["queue"],
                tmux_detach_hint=prefix_hint,
                detach_tmux_client=detach_project_client,
            )
            async with app.run_test(size=_WIDE_HOME_SIZE) as pilot:
                footer = app.query_one("#focus-footer", Grid)
                detach = app.query_one("#focus-footer-detach", Button)
                status = app.query_one("#workspace-command-status", Button)
                assert len({child.region.y for child in footer.children}) == 1
                actions_index = next(
                    index
                    for index, child in enumerate(footer.children)
                    if child.id == "focus-footer-actions"
                )
                detach_index = next(
                    index
                    for index, child in enumerate(footer.children)
                    if child.id == "focus-footer-detach"
                )
                help_index = next(
                    index
                    for index, child in enumerate(footer.children)
                    if child.id == "focus-footer-help"
                )
                status_index = next(
                    index
                    for index, child in enumerate(footer.children)
                    if child.id == "workspace-command-status"
                )
                assert actions_index < detach_index < help_index < status_index
                assert status.display
                assert "Pending: Stop " in _rendered_text(status)
                assert prefix_hint in plain_text(detach.label)
                assert not detach.disabled
                assert await tmux.client_for_pane(project_session, project_pane) is not None

                await pilot.click(detach)

                await wait_until(
                    lambda: _no_tmux_clients(tmux, project_session),
                    timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
                )
                unrelated_clients = await tmux.list_clients(
                    unrelated_session, _TMUX_CLIENT_TTY_FORMAT
                )
                assert len(unrelated_clients) == 1
    finally:
        reader.close()


async def _no_tmux_clients(tmux: Tmux, target: str) -> bool:
    return not await tmux.list_clients(target, _TMUX_CLIENT_TTY_FORMAT)


@pytest.mark.parametrize("size", [_COMPACT_HOME_SIZE, _TINY_HOME_SIZE])
async def test_compact_contextual_views_open_the_shared_task_detail_and_return(
    database: Database,
    connection: Connection,
    tmp_path: Path,
    size: tuple[int, int],
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = SystemClock()
    settings = load_settings(project_dir)
    task = seed_task(connection, clock, title="Compact contextual view")
    queue = CommandQueue(connection, clock)
    for command in queue.pending():
        queue.mark_done(command.id)
    reader = StateReader(database, clock)
    app_instances: list[DashboardApp] = []

    async def open_palette_context(request: PaletteRequest) -> None:
        app = app_instances[0]
        palette = _palette_screen_for_app(
            reader,
            connection,
            project_dir,
            clock,
            request,
            remember_contextual_draft=app.remember_contextual_palette_draft,
            task_routes_available=True,
        )

        def after_close(route: TaskPaletteRoute | None) -> None:
            if route is not None and request.task_id is not None:
                app.route_task_palette(request.task_id, route)

        await app.push_screen(palette, callback=after_close)

    try:
        app = _app(
            reader,
            project_dir,
            widget_names=list(settings.dashboard.widgets),
            open_palette_context=open_palette_context,
            clock=clock,
            settings=settings,
        )
        app_instances.append(app)
        async with app.run_test(size=size) as pilot:
            home, list_view, evidence_screen = await _open_compact_task_evidence_route(
                app,
                pilot,
                task.id,
            )
            evidence_panel = evidence_screen.query_one("#task-detail-panel", TaskDetailPanel)
            assert evidence_panel.active_view == TASK_VIEW_EVIDENCE
            assert task.id in _rendered_text(
                evidence_screen.query_one(f"#{TASK_DETAIL_IDENTITY}", Static)
            )
            assert home.is_mounted
            assert queue.pending() == []
            await pilot.press("escape")
            await wait_until(
                lambda: app.screen is home,
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            await pilot.press("escape")
            await wait_until(
                lambda: (
                    app.screen is home
                    and task.id
                    not in _rendered_text(app.query_one(f"#{TASK_DETAIL_IDENTITY}", Static))
                ),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            assert app.query_one("#task-browser-search", Input).value == ""
            assert list_view.index is None
            assert queue.pending() == []
    finally:
        reader.close()


async def _open_compact_task_evidence_route(
    app: DashboardApp,
    pilot: Pilot[None],
    task_id: str,
) -> tuple[Widget, ListView, TaskDetailScreen]:
    home = cast("Widget", app.screen)
    list_view = app.query_one("#task-browser-list", ListView)
    await wait_until(
        lambda: bool(app.query(f"#task-row-{task_id}")),
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    list_view.focus()
    await pilot.press("ctrl+p")
    await wait_until(
        lambda: isinstance(app.screen, PaletteScreen),
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    menu = cast("PaletteScreen", app.screen).query_one("#palette-menu", VerticalScroll)
    menu.scroll_to_widget(
        menu.query_one("#palette-menu-view-secondary", Button),
        animate=False,
        immediate=True,
    )
    await pilot.pause()
    await _click_palette_menu_button(
        pilot,
        cast("PaletteScreen", app.screen),
        "palette-menu-view-secondary",
    )
    await wait_until(
        lambda: bool(app.screen.query("#task-detail-views-frame")),
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    views = app.screen
    views_frame = views.query_one("#task-detail-views-frame")
    choice = cast("Select[str]", views.query_one("#task-detail-view-choice", Select))
    choice.value = TASK_VIEW_EVIDENCE
    views_content = views.query_one("#task-detail-views-content", Vertical)
    open_button = views.query_one(f"#{TASK_DETAIL_VIEWS_OPEN_BUTTON}", Button)
    close_button = views.query_one("#task-detail-views-close", Button)
    await pilot.press("tab")
    await pilot.pause()
    await wait_until(
        lambda: views_frame.content_region.contains_region(open_button.region),
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    assert pilot.app.focused is open_button
    assert views_frame.content_region.contains_region(open_button.region)
    assert views_content.region.contains_region(choice.region)
    assert views_content.region.contains_region(open_button.region)
    assert views.region.contains_region(close_button.region)
    await pilot.press("enter")
    await wait_until(
        lambda: isinstance(app.screen, TaskDetailScreen),
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    return home, list_view, cast("TaskDetailScreen", app.screen)


@pytest.mark.parametrize("widget_names", [["header"], ["escalations"]])
async def test_project_fault_attention_without_faults_extension_opens_existing_history(
    database: Database,
    connection: Connection,
    tmp_path: Path,
    widget_names: list[str],
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = SystemClock()
    EventLog(connection, clock).append(
        EventKind.ENGINE_FAULT,
        task_id=None,
        message="project engine stopped after a recorded fault",
        data={
            "scope": "engine",
            "error": "engine stopped with a recoverable fault",
            "recoverable": True,
            "repeat": 1,
        },
    )
    reader = StateReader(database, clock)
    queue = CommandQueue(connection, clock)
    try:
        app = _app(reader, project_dir, widget_names=widget_names)
        async with app.run_test(size=_STANDARD_HOME_SIZE) as pilot:
            attention = app.query_one("#escalations")
            attention_text = (
                attention.query_one("#escalation-panels", VerticalScroll).query_one(Static)
                if isinstance(attention, EscalationsPanel)
                else attention
            )
            await wait_until(
                lambda: "engine fault" in _rendered_text(cast("Static", attention_text)),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            await pilot.click("#escalations")
            await wait_until(
                lambda: bool(app.screen.query("#command-history-page")),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            history_screen = app.screen
            history_scroll = history_screen.query_one("#command-history-scroll", VerticalScroll)
            history_copy = history_screen.query_one("#command-history-copy", Static)
            assert history_scroll.region.height <= history_copy.region.height
            assert queue.pending() == []
            assert reader.command_history(limit=COMMAND_HISTORY_TEST_LIMIT).total_count == 0
            await pilot.press("escape")
            await wait_until(
                lambda: app.screen.query_one("#workspace-command-status", Button).display,
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
    finally:
        reader.close()


async def test_stopped_project_attention_without_rows_opens_command_history(
    database: Database,
    connection: Connection,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    reader = StateReader(database, SystemClock())
    queue = CommandQueue(connection, SystemClock())
    try:
        app = _app(
            reader,
            project_dir,
            widget_names=["header"],
            services=StubServices(engine_running=False),
        )
        async with app.run_test(size=_STANDARD_HOME_SIZE) as pilot:
            attention = app.query_one("#escalations", Static)
            await wait_until(
                lambda: "No decisions needed" in _rendered_text(attention),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            assert "Engine stopped" not in _rendered_text(attention)
            await pilot.click(attention)
            await wait_until(
                lambda: bool(app.screen.query("#command-history-page")),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            history_screen = app.screen
            history_scroll = history_screen.query_one("#command-history-scroll", VerticalScroll)
            history_copy = history_screen.query_one("#command-history-copy", Static)
            assert history_scroll.region.height <= history_copy.region.height
            assert queue.pending() == []
    finally:
        reader.close()


@pytest.mark.parametrize(
    ("button_id", "view"),
    [
        (TASK_DETAIL_USAGE_INLINE_BUTTON, TASK_VIEW_USAGE),
        (TASK_DETAIL_EVIDENCE_INLINE_BUTTON, TASK_VIEW_EVIDENCE),
    ],
)
async def test_compact_home_inline_detail_buttons_open_their_view_and_escape_returns(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
    button_id: str,
    view: str,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    task = seed_task(connection, SystemClock(), title="Inline detail route")
    app = _app(reader, project_dir, widget_names=["queue"])
    async with app.run_test(size=_COMPACT_HOME_SIZE) as pilot:
        home = app.screen
        await wait_until(
            lambda: bool(app.query(f"#task-row-{task.id}")),
            timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
        )
        await _select_home_task(app, task.id)
        button = app.query_one(f"#{button_id}", Button)
        button.focus()
        await wait_until(
            lambda: button.region.bottom <= app.size.height,
            timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
        )
        await pilot.press("enter")
        await wait_until(
            lambda: isinstance(app.screen, TaskDetailScreen),
            timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
        )
        panel = app.screen.query_one("#task-detail-panel", TaskDetailPanel)
        await wait_until(lambda: panel.active_view == view, timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS)
        await pilot.press("escape")
        await wait_until(lambda: app.screen is home, timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS)


async def _assert_home_inline_secondary_routes(
    app: DashboardApp,
    pilot: Pilot[None],
    task_id: str,
    reader: StateReader,
    before_commands: int,
) -> None:
    home_screen = app.screen
    await pilot.press("e")
    await wait_until(
        lambda: isinstance(app.screen, TaskDetailScreen),
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    evidence_screen = app.screen
    assert evidence_screen.query_one("#task-detail-panel", TaskDetailPanel).active_view == (
        TASK_VIEW_EVIDENCE
    )
    assert task_id in _rendered_text(evidence_screen.query_one(f"#{TASK_DETAIL_IDENTITY}", Static))
    assert home_screen.is_mounted
    await pilot.press("escape")
    await wait_until(lambda: app.screen is home_screen, timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS)
    await pilot.press("u")
    await wait_until(
        lambda: isinstance(app.screen, TaskDetailScreen),
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )
    usage_screen = app.screen
    assert usage_screen.query_one("#task-detail-panel", TaskDetailPanel).active_view == (
        TASK_VIEW_USAGE
    )
    assert task_id in _rendered_text(usage_screen.query_one(f"#{TASK_DETAIL_IDENTITY}", Static))
    assert reader.command_history(limit=COMMAND_HISTORY_TEST_LIMIT).total_count == before_commands
    await pilot.press("escape")
    await wait_until(lambda: app.screen is home_screen, timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS)


def _assert_home_navigation_fits(app: DashboardApp, width: int, height: int) -> None:
    footer = app.query_one("#focus-footer", Grid)
    root = app.query_one("#focus-root", VerticalScroll)
    assert footer.region.x >= 0
    assert footer.region.right <= width
    assert footer.region.y >= 0
    assert not footer.display or footer.region.bottom <= height or root.max_scroll_y > 0


async def _open_home_help(app: DashboardApp, pilot: Pilot[None], gesture: str) -> None:
    if gesture == _HELP_GESTURE_KEY:
        await pilot.press("?")
    elif gesture == _HELP_GESTURE_FOOTER:
        await pilot.click("#focus-footer-help")
    else:
        await _choose_focus_view(app, pilot, "Help", mouse=True)
    await wait_until(
        lambda: bool(app.screen.query("#workspace-help-close")),
        timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
    )


@pytest.mark.parametrize(
    "case",
    [
        (_WIDE_HOME_SIZE, _HELP_GESTURE_KEY),
        (_WIDE_HOME_SIZE, _HELP_GESTURE_VIEWS),
        (_COMPACT_HOME_SIZE, _HELP_GESTURE_FOOTER),
        (_TINY_HOME_SIZE, _HELP_GESTURE_KEY),
    ],
)
async def test_home_help_opens_by_key_or_mouse_and_closing_leaves_home_unchanged(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: tuple[tuple[int, int], str],
) -> None:
    size, gesture = case
    monkeypatch.setenv("NO_COLOR", "1")
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(_TEST_START)
    task = seed_task(connection, clock, title="Popup underlay task")
    app = _app(
        reader,
        project_dir,
        widget_names=["header", "queue", "usage", "resources"],
        services=StubServices(engine_running=False),
        clock=clock,
        settings=_settings_without_animations(project_dir),
    )
    popup = {
        "close_selector": "#workspace-help-close",
        "frame_selector": "#workspace-help-frame",
        "body_selector": "#workspace-help-scroll",
    }
    async with app.run_test(size=size) as pilot:
        home = app.screen
        await wait_until(
            lambda: bool(app.query(f"#task-row-{task.id}")),
            timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
        )
        await _select_home_task(app, task.id)
        root = app.query_one("#focus-root", VerticalScroll)
        task_list = app.query_one("#task-browser-list", ListView)
        if gesture == _HELP_GESTURE_FOOTER:
            app.query_one("#focus-footer-help", Button).focus()
            await pilot.pause()
        before = (root.scroll_y, task_list.index, _rendered_visible_home_origins(app))

        await _open_home_help(app, pilot, gesture)
        help_screen = app.screen
        focus_on_home = home.focused
        await _assert_popup_close_reachable(help_screen, **popup)
        if gesture == _HELP_GESTURE_VIEWS:
            context = help_screen.query_one("#workspace-help-context", Static)
            assert task.id in _rendered_text(context)
        if size == _TINY_HOME_SIZE:
            frame = help_screen.query_one("#workspace-help-frame")
            await pilot.resize_terminal(*_WIDE_HOME_SIZE)
            await wait_until(
                lambda: app.size == _WIDE_HOME_SIZE and frame.region.height > _TINY_HOME_SIZE[1],
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            await pilot.resize_terminal(*_TINY_HOME_SIZE)
            await wait_until(
                lambda: app.size == _TINY_HOME_SIZE, timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS
            )
            await _assert_popup_close_reachable(help_screen, **popup)

        if gesture == _HELP_GESTURE_KEY:
            await pilot.press("escape")
        else:
            assert await pilot.click("#workspace-help-close")
        await wait_until(lambda: app.screen is home, timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS)
        await wait_until(
            lambda: _rendered_visible_home_origins(app) == before[2],
            timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
        )
        assert (root.scroll_y, task_list.index) == before[:2]
        assert home.focused is focus_on_home

        await pilot.press("?")
        await wait_until(
            lambda: bool(app.screen.query("#workspace-help-close")),
            timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
        )
        await pilot.press("escape")
        await wait_until(lambda: app.screen is home, timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS)


async def _assert_evidence_editor_shortcut_precedence(
    app: DashboardApp,
    pilot: Pilot[None],
    task_id: str,
    palette_requests: list[PaletteRequest],
) -> None:
    await pilot.press("e")
    await wait_until(lambda: isinstance(app.screen, TaskDetailScreen), timeout=2)
    evidence_screen = app.screen
    role_select = cast(
        "Select[str]", evidence_screen.query_one("#task-detail-timeline-role", Select)
    )
    role_select.focus()
    await pilot.press("enter")
    await wait_until(lambda: role_select.expanded, timeout=2)
    await pilot.press("p")
    assert role_select.expanded
    assert role_select.value == Select.BLANK
    await pilot.press("n")
    assert role_select.expanded
    await pilot.press("escape")
    await wait_until(lambda: not role_select.expanded, timeout=2)
    native_select = cast(
        "Select[str]", evidence_screen.query_one("#task-detail-timeline-category", Select)
    )
    await wait_until(lambda: native_select.display, timeout=2)
    native_select.focus()
    await pilot.press("ctrl+p")
    assert palette_requests == []
    assert app.screen is evidence_screen
    await pilot.press("enter")
    await pilot.pause()
    await pilot.press("ctrl+p")
    assert palette_requests == []
    assert native_select.expanded
    assert app.screen is evidence_screen
    await pilot.press("escape")
    await wait_until(lambda: not native_select.expanded, timeout=2)
    assert app.screen is evidence_screen
    await pilot.press("escape")
    await wait_until(lambda: not isinstance(app.screen, TaskDetailScreen), timeout=2)
    app.query_one("#task-detail-body").focus()
    await pilot.press("ctrl+p")
    assert palette_requests[-1].task_id == task_id


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
            home = app.screen
            search = app.query_one("#task-browser-search", Input)
            search.value = task.id
            search.focus()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("enter")
            assert not isinstance(app.screen, TaskDetailScreen)
            identity = home.query_one("#task-detail-identity", Static)
            await wait_until(
                lambda: task.title in str(identity.content),
                timeout=2,
            )
            task_list = home.query_one("#task-browser-list", ListView)
            home_focus = home.focused
            assert home_focus is not None
            await pilot.press("u")
            await wait_until(lambda: isinstance(app.screen, TaskDetailScreen), timeout=2)
            usage_screen = app.screen
            usage_panel = usage_screen.query_one("#task-detail-panel", TaskDetailPanel)
            sessions = usage_screen.query_one(f"#{TASK_DETAIL_SESSIONS}", Static)
            await wait_until(
                lambda: (
                    usage_panel.active_view == TASK_VIEW_USAGE
                    and "recorded usage" in plain_text(sessions.content)
                ),
                timeout=2,
            )
            usage_close = usage_screen.query_one("#task-detail-popup-close", Button)
            await wait_until(
                lambda: (
                    usage_close.region.width > 0
                    and usage_close.region.height > 0
                    and usage_close.region.x >= 0
                    and usage_close.region.y >= 0
                    and usage_close.region.right <= app.size.width
                    and usage_close.region.bottom <= app.size.height
                ),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            await pilot.pause()
            await pilot.click("#task-detail-popup-close")
            await wait_until(lambda: app.screen is home, timeout=2)
            assert task_list.highlighted_child is not None
            assert task_list.highlighted_child.id == f"task-row-{task.id}"
            assert home.focused is home_focus

            await _assert_evidence_editor_shortcut_precedence(
                app,
                pilot,
                task.id,
                palette_requests,
            )
    finally:
        reader.close()


@pytest.mark.parametrize("size", [_WIDE_HOME_SIZE, _COMPACT_HOME_SIZE])
async def test_dashboard_terminal_palette_event_keeps_home_mounted_under_actions(
    database: Database,
    connection: Connection,
    tmp_path: Path,
    size: tuple[int, int],
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = SystemClock()
    task = seed_task(connection, clock, title="Mounted palette route")
    queued_before = CommandQueue(connection, clock).pending()
    reader = StateReader(database, clock)
    app_instances: list[DashboardApp] = []

    async def open_palette_context(request: PaletteRequest) -> None:
        app = app_instances[0]
        await app.push_screen(
            _palette_screen_for_app(
                reader,
                connection,
                project_dir,
                clock,
                request,
                remember_contextual_draft=app.remember_contextual_palette_draft,
                task_routes_available=True,
            )
        )

    try:
        app = _app(
            reader,
            project_dir,
            widget_names=["queue"],
            open_palette_context=open_palette_context,
        )
        app_instances.append(app)
        async with app.run_test(size=size) as pilot:
            home = app.screen
            await wait_until(
                lambda: bool(home.query(f"#task-row-{task.id}")),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            await _select_home_task(app, task.id)
            search = home.query_one("#task-browser-search", Input)
            search.focus()

            await pilot.press("f12")
            await wait_until(
                lambda: isinstance(app.screen, PaletteScreen),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            palette = cast("PaletteScreen", app.screen)
            home_row = home.query_one(f"#task-row-{task.id}")
            assert home.is_mounted
            assert home_row.visible
            assert home_row.region.width > 0
            assert task.id in _rendered_text(palette.query_one("#palette-menu-context", Static))
            screenshot_text = _exported_screen_text(app)
            if size == _WIDE_HOME_SIZE:
                assert task.title in screenshot_text
            else:
                assert "✓ No decisions needed" in screenshot_text
                detach = home.query_one("#focus-footer-detach", Button)
                assert detach.disabled
                assert detach.region.width > 0
                assert detach.region.right <= size[0]
                assert detach.region.bottom <= size[1]
            assert "Actions" in screenshot_text
            await pilot.press("escape")
            await wait_until(
                lambda: app.screen is home and home.is_mounted,
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            assert app.focused is search
            assert task.id in _rendered_text(home.query_one("#task-detail-identity", Static))
            assert CommandQueue(connection, clock).pending() == queued_before
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
            await wait_until(
                lambda: bool(home_screen.query(f"#task-row-{task.id}")),
                timeout=2,
            )
            app.route_task_palette(task.id, TaskPaletteRoute.INSPECT)
            await wait_until(lambda: isinstance(app.screen, TaskDetailScreen), timeout=2)
            task_screen = app.screen
            await wait_until(
                lambda: (
                    task.title
                    in _rendered_text(task_screen.query_one(f"#{TASK_DETAIL_IDENTITY}", Static))
                ),
                timeout=2,
            )
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
            task_row = home_screen.query_one(f"#task-row-{task.id} > Static", Static)
            await wait_until(
                lambda: Hold.PAUSED.value in _rendered_text(task_row),
                timeout=2,
            )
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
                lambda: second.id not in _rendered_text(identity),
                timeout=2,
            )
            assert not any(_rendered_task_detail_sections(app))
    finally:
        _finish_report_read(report_paths[second.id], writer_descriptors, second_report)
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
            assert app.focused is not None
            assert app.focused.parent is footer
            assert footer.region.height >= 2
            assert footer.region.bottom <= app.size.height
            await _choose_focus_view(app, pilot, "Workflow · strips")
            strips = app.screen.query_one(StripsPanel)
            strip_text = _rendered_text(strips)
            assert task_id in strip_text
            assert "Escalated" in strip_text
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
            await _choose_focus_view(app, pilot, "Activity · dialogue")
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


@pytest.mark.parametrize("size", [_WIDE_HOME_SIZE, _COMPACT_HOME_SIZE])
async def test_configured_usage_and_docker_views_remain_reachable(
    database: Database,
    connection: Connection,
    tmp_path: Path,
    size: tuple[int, int],
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = SystemClock()
    _seed_project_usage(connection, clock)
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
        async with app.run_test(size=size) as pilot:
            home_screen = app.screen
            await wait_until(
                lambda: "CPU 37%" in _rendered_text(app.query_one("#resources", Static)),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            assert services.resource_reads == 1

            assert await pilot.click("#usage")
            await wait_until(lambda: app.screen is not home_screen, timeout=2)
            assert home_screen.is_mounted
            usage = app.screen.query_one("#usage", Static)
            usage_text = plain_text(usage.content)
            assert usage_text.startswith("Totals")
            assert all(
                fact in usage_text
                for fact in ("API billed", "subscription", "API unreported", "API-equivalent")
            )
            assert "3 unknown" in usage_text
            assert not any(
                raw in usage_text
                for raw in ("operator_config", "provider_reported", "billing_mode")
            )
            await pilot.click("#context-widget-close")
            await wait_until(lambda: app.screen is home_screen, timeout=2)

            app.query_one("#resources", Static).scroll_visible(animate=False, force=True)
            await pilot.pause()
            assert await pilot.click("#resources")
            await wait_until(lambda: app.screen is not home_screen, timeout=2)
            assert home_screen.is_mounted
            resources = app.screen.query_one("#project-diagnostics-resources", Static)
            assert "CPU 37%" in " ".join(_rendered_widget_lines(resources))
            assert services.resource_reads == 1
            await pilot.click("#context-widget-close")
            await wait_until(lambda: app.screen is home_screen, timeout=2)

            await _choose_focus_view(app, pilot, "Diagnostics · docker")
            assert home_screen.is_mounted
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
    clock = SystemClock()
    queue = CommandQueue(connection, clock)
    command = queue.append(Stop())
    queue.mark_done(command.id, {"stopping": True})
    entry_points = registry_module.entry_points

    def entry_points_with_extension(*, group: str) -> list[object]:
        registered = entry_points(group=group)
        if group == WIDGET_ENTRY_POINT_GROUP:
            return [*registered, _ExternalEntryPoint(), _LaterExternalEntryPoint()]
        return list(registered)

    monkeypatch.setattr(registry_module, "entry_points", entry_points_with_extension)
    project_dir = write_minimal_project(tmp_path)
    reader = StateReader(database, clock)
    try:
        app = _app(
            reader,
            project_dir,
            widget_names=["usage", _LATER_EXTERNAL_WIDGET_NAME, _EXTERNAL_WIDGET_NAME],
        )
        async with app.run_test(size=(72, 30)) as pilot:
            workspace_screen = app.screen
            await pilot.press("v")
            await wait_until(lambda: bool(app.screen.query("#focus-views-list")), timeout=2)
            extension_choices = app.screen.query_one("#focus-views-list", ListView)
            extension_labels = tuple(
                _rendered_text(item.query_one(Static))
                for item in extension_choices.children
                if isinstance(item, ListItem)
                and _rendered_text(item.query_one(Static)).startswith("Extensions ·")
            )
            assert extension_labels.index("Extensions · later-widget") < extension_labels.index(
                "Extensions · outside-widget"
            )
            await _choose_focus_view(app, pilot, "Extensions · later-widget", mouse=True)
            later_panel = app.screen.query_one("#later-external-widget-panel", Static)
            assert later_panel.region.width > 0
            await pilot.press("escape")
            await wait_until(lambda: app.screen is workspace_screen, timeout=2)

            await pilot.press("v")
            await wait_until(lambda: bool(app.screen.query("#focus-views-list")), timeout=2)
            await _choose_focus_view(app, pilot, "Extensions · outside-widget", mouse=True)
            assert app.screen is not workspace_screen
            extension_panel = app.screen.query_one("#external-widget-panel", Static)
            assert extension_panel.region.width > 0
            assert _rendered_text(extension_panel) == '{"stopping": true}'
            await pilot.press("escape")
            await wait_until(lambda: app.screen is workspace_screen, timeout=2)

            await pilot.press("?")
            await pilot.pause()
            assert app.screen is not workspace_screen
            assert app.screen.query_one("#workspace-help-copy", Static).region.height > 0
            assert await pilot.click("#workspace-help-close")
            await pilot.pause()
            assert app.screen is workspace_screen
    finally:
        reader.close()


async def test_home_views_mouse_opens_selected_task_actions(
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
