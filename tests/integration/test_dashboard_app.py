from __future__ import annotations

import errno
import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import pytest
from textual.containers import Horizontal, VerticalScroll
from textual.geometry import Region
from textual.widgets import Button, DataTable, Input, Label, ListItem, ListView, Static, TextArea

import shikiru.agents.registry as registry_module
from shikiru.agents.contracts import (
    BILLING_MODE_API,
    BILLING_MODE_SUBSCRIPTION,
    BILLING_SOURCE_OPERATOR_CONFIG,
    COST_BASIS_BILLED,
    COST_BASIS_UNKNOWN,
    BillingDeclaration,
    Rung,
    Usage,
)
from shikiru.clock import SystemClock
from shikiru.config.loader import load_settings
from shikiru.dashboard.app import DashboardApp, WidgetPluginError
from shikiru.dashboard.contract import WIDGET_ENTRY_POINT_GROUP, PaletteRequest
from shikiru.dashboard.detail import (
    TASK_DETAIL_ACTIONS_BUTTON,
    TASK_DETAIL_BLOCKER,
    TASK_DETAIL_BODY,
    TASK_DETAIL_CRITERIA,
    TASK_DETAIL_EVIDENCE,
    TASK_DETAIL_FACTS,
    TASK_DETAIL_OWNERSHIP,
    TASK_DETAIL_PROGRESS,
    TASK_DETAIL_REPORT,
    TASK_DETAIL_REQUEST,
    TASK_DETAIL_SESSIONS,
    TASK_DETAIL_SITUATION,
    TASK_DETAIL_TIMELINE_TURN,
    TASK_DETAIL_VERDICT,
    TASK_DETAIL_WORKSPACE,
    TaskDetailPanel,
    TaskDetailScreen,
)
from shikiru.dashboard.palette import PaletteScreen
from shikiru.domain.commands import Prioritize, Stop
from shikiru.domain.escalation import EscalationStatus, options_for
from shikiru.domain.events import EventKind
from shikiru.domain.reports import REPORT_FILENAME, REPORT_KIND_FILENAME, ReportKind
from shikiru.domain.steps import Transition, first_step
from shikiru.domain.tasks import EscalationReason, Hold, Mode, Role, Status
from shikiru.operator.commands import OperatorCommands
from shikiru.operator.queries import OperatorQueries
from shikiru.paths import turns_dir
from shikiru.plugins.widgets.escalations import EscalationsPanel
from shikiru.plugins.widgets.strips import StripsPanel
from shikiru.store.commands import CommandQueue
from shikiru.store.escalations import EscalationStore
from shikiru.store.events import EventLog
from shikiru.store.reader import StateReader
from shikiru.store.runs import RunStore
from shikiru.store.tasks import TaskStore
from tests.support.clock import FakeClock
from tests.support.config import write_minimal_project
from tests.support.dashboard import StubServices, ignore_palette_action, state_views
from tests.support.entry_points import FakeDistribution, ambiguous_entry_points
from tests.support.render import plain_text
from tests.support.store import seed_task
from tests.support.waiting import wait_until

_TEST_START = datetime(2026, 1, 1, tzinfo=UTC)
_APP_SHUTDOWN_TIMEOUT_SECONDS = 2
_SCROLL_SETTLE_TIMEOUT_SECONDS = 2
_DASHBOARD_WAIT_TIMEOUT_SECONDS = 2
_EXTERNAL_WIDGET_NAME = "outside-widget"
_WIDE_HOME_SIZE = (120, 40)
_MIN_WIDE_TASK_LIST_ROWS = 9
_API_INPUT_TOKENS = 1_000
_API_OUTPUT_TOKENS = 500
_SUBSCRIPTION_INPUT_TOKENS = 400
_SUBSCRIPTION_OUTPUT_TOKENS = 200
_API_BILLED_COST_USD = 0.42


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


if TYPE_CHECKING:
    from pathlib import Path

    from rich.text import Text
    from textual.pilot import Pilot
    from textual.widget import Widget

    from shikiru.clock import Clock
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
    open_palette: OpenPalette = ignore_palette_action,
    open_palette_context: OpenPaletteContext | None = None,
) -> DashboardApp:
    settings = load_settings(project_dir)
    return DashboardApp(
        focus_task=None,
        views=state_views(reader, project_dir, SystemClock(), StubServices()),
        on_change=reader.watch,
        clock=SystemClock(),
        motion_interval_seconds=_motion_interval(project_dir),
        resources_refresh_seconds=settings.orchestration.resources.sample_seconds,
        docker_refresh_seconds=settings.dashboard.docker_refresh_seconds,
        open_palette=open_palette,
        widget_names=widget_names,
        open_palette_context=open_palette_context,
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
                    and "1 faults" in _rendered_text(attention_row)
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


async def test_header_without_home_attention_keeps_stopped_engine_fault_context(
    database: Database,
    connection: Connection,
    tmp_path: Path,
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
        async with app.run_test(size=_WIDE_HOME_SIZE):
            header = app.query_one("#header", Static)
            await wait_until(
                lambda: "Engine stopped" in " ".join(_rendered_widget_lines(header)),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            text = " ".join(_rendered_widget_lines(header))
            assert "Engine stopped" in text
            assert "host sample failed x2" in text
    finally:
        reader.close()


async def test_wide_focus_renders_mixed_billing_and_attention_inside_visible_readout_area(
    database: Database,
    connection: Connection,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = SystemClock()
    task = seed_task(connection, clock, title="Visible attention and usage")
    runs = RunStore(connection, clock)
    api_run = runs.start(
        task.id,
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
        task.id,
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
    EventLog(connection, clock).append(
        EventKind.ENGINE_FAULT,
        task_id=task.id,
        message="recorded resource failure",
        data={
            "scope": "resource sample",
            "error": "host sample failed",
            "recoverable": True,
            "repeat": 2,
        },
    )
    reader = StateReader(database, clock)
    try:
        app = _app(
            reader,
            project_dir,
            widget_names=["header", "queue", "escalations", "usage", "resources"],
        )
        async with app.run_test(size=_WIDE_HOME_SIZE):
            readouts = app.query_one("#focus-readouts", VerticalScroll)
            identity = app.query_one("#header", Static)
            usage = app.query_one("#usage", Static)
            resources = app.query_one("#resources", Static)
            attention = app.query_one("#escalations", EscalationsPanel)
            attention_rows = attention.query_one("#escalation-panels", VerticalScroll)
            await wait_until(
                lambda: (
                    "2.1k input+output" in " ".join(_rendered_widget_lines(usage))
                    and attention.display
                    and bool(attention_rows.query(Static))
                ),
                timeout=_DASHBOARD_WAIT_TIMEOUT_SECONDS,
            )
            primary = (identity, usage, resources, attention)
            assert readouts.scroll_y == 0
            assert all(widget.is_on_screen for widget in primary)
            assert all(widget.region.y >= readouts.region.y for widget in primary)
            assert all(widget.region.bottom <= readouts.region.bottom for widget in primary)
            assert app.query_one("#task-browser-list", ListView).region.height >= (
                _MIN_WIDE_TASK_LIST_ROWS
            )

            identity_text = " ".join(_rendered_widget_lines(identity))
            usage_text = " ".join(_rendered_widget_lines(usage))
            resource_text = " ".join(_rendered_widget_lines(resources))
            attention_row = attention_rows.query_one(Static)
            attention_text = " ".join(_rendered_widget_lines(attention_row))
            assert "Engine stopped" in identity_text
            assert "2.1k input+output" in usage_text
            assert "API billed $0.42" in usage_text
            assert "1 subscription agent" in usage_text
            assert "Host" in resource_text
            assert "Shikiru including agents" in resource_text
            assert "1 faults" in attention_text
            assert task.id in attention_text
            assert "host sample failed" not in identity_text
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
            await pilot.pause()
            assert visible_anchor() == anchor_before
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
            assert app.query_one("#escalations").display is False
            await _choose_focus_view(app, pilot, "Task filter · Waiting")
            count = app.query_one("#task-browser-count", Static)
            await wait_until(
                lambda: plain_text(count.content).startswith("Waiting · 1 of 1"),
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


async def test_focus_attention_opens_durable_decision_evidence(
    database: Database, connection: Connection, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = SystemClock()
    task = seed_task(connection, clock, title="Choose retry behavior")
    TaskStore(connection, clock).apply_transition(
        task.id,
        Transition.stay(task, step=task.step, status=Status.ESCALATED),
    )
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
        async with app.run_test(size=(120, 40)) as pilot:
            attention = app.query_one("#escalations", EscalationsPanel)
            await wait_until(lambda: attention.display, timeout=2)
            row = attention.query_one("#escalation-panels", VerticalScroll).query_one(Static)
            assert escalation.id in _rendered_text(row)
            assert "Enter inspect · r resolve · o evidence" in _rendered_text(row)

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
async def test_narrow_dashboard_keeps_command_controls_reachable(
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
            views_button = app.query_one("#focus-views", Button)
            help_button = app.query_one("#focus-help", Button)
            assert views_button.region.bottom <= 20
            assert views_button.region.right <= width
            assert help_button.region.bottom <= 20
            assert help_button.region.right <= width
            home_screen = app.screen
            await pilot.click(views_button)
            await wait_until(lambda: app.screen is not home_screen, timeout=2)
            await pilot.pause()
            await _choose_focus_view(app, pilot, "Command history · Pending, Applied and Rejected")
            await wait_until(lambda: bool(app.screen.query("#command-history-page")), timeout=2)
            page = cast("Static", app.screen.query_one("#command-history-page"))
            assert "all statuses" in _rendered_text(page)
    finally:
        reader.close()


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
    reader = StateReader(database, SystemClock())

    try:
        app = _app(reader, project_dir, widget_names=["queue"])
        async with app.run_test(size=(120, 40)) as pilot:
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

            for width, height in ((48, 20), (36, 20), (48, 14), (20, 8)):
                await pilot.resize_terminal(width, height)
                await pilot.pause()
                assert app.focused is search
                assert search.value == task.id
                assert task.title in _rendered_text(identity)
                assert home_layout.region.width <= width
                assert detail_panel.display is False
                assert app.query_one("#focus-footer").region.bottom <= height
                assert app.query_one("#focus-views", Button).region.right <= width
                assert app.query_one("#focus-help", Button).region.right <= width

            home_screen = app.screen
            await pilot.press("enter")
            await pilot.press("enter")
            await wait_until(lambda: isinstance(app.screen, TaskDetailScreen), timeout=2)
            await pilot.press("escape")
            await wait_until(lambda: app.screen is home_screen, timeout=2)
    finally:
        reader.close()


async def test_home_help_works_with_mouse_and_keyboard_without_color(
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
            await pilot.click("#focus-help")
            await pilot.pause()
            assert "Mouse: select or open a task" in _rendered_text(
                app.screen.query_one("#workspace-help-copy", Static)
            )
            await pilot.press("escape")
            await pilot.pause()
            assert await pilot.click("#focus-help")
            await pilot.pause()
            assert app.screen.query_one("#workspace-help-copy", Static).display
            await pilot.press("escape")
    finally:
        reader.close()


async def test_task_detail_toolbar_and_timeline_form_remain_reachable_after_resize(
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
            action_button = app.screen.query_one(f"#{TASK_DETAIL_ACTIONS_BUTTON}", Button)
            await _assert_task_detail_toolbar_controls_are_reachable(app, pilot)
            timeline_turn = app.screen.query_one(f"#{TASK_DETAIL_TIMELINE_TURN}", Input)
            detail_body = app.screen.query_one(f"#{TASK_DETAIL_BODY}", VerticalScroll)

            def timeline_visible() -> bool:
                return (
                    detail_body.scroll_y > 0
                    and timeline_turn.region.y >= detail_body.region.y
                    and timeline_turn.region.bottom <= detail_body.region.bottom
                    and timeline_turn.region.y >= 0
                    and timeline_turn.region.bottom <= app.size.height
                )

            timeline_turn.value = "3"
            timeline_turn.focus()
            await pilot.wait_for_scheduled_animations()
            await wait_until(timeline_visible, timeout=2)
            assert app.focused is timeline_turn
            await pilot.resize_terminal(48, 14)
            await pilot.wait_for_scheduled_animations()
            await wait_until(timeline_visible, timeout=2)
            assert app.focused is timeline_turn
            assert timeline_turn.value == "3"
            action_button.focus()
            await pilot.wait_for_scheduled_animations()
            await wait_until(
                lambda: detail_body.scroll_y == detail_body.scroll_target_y,
                timeout=_SCROLL_SETTLE_TIMEOUT_SECONDS,
            )
            reading_scroll = detail_body.scroll_y
            await pilot.resize_terminal(36, 20)
            await pilot.wait_for_scheduled_animations()
            await wait_until(
                lambda: detail_body.scroll_y == detail_body.scroll_target_y,
                timeout=_SCROLL_SETTLE_TIMEOUT_SECONDS,
            )
            assert app.focused is action_button
            assert detail_body.scroll_y == reading_scroll
            assert action_button.region.x >= 0
            assert action_button.region.right <= app.size.width
            await pilot.press("enter")
            assert palette_requests[-1].task_id == task.id
    finally:
        reader.close()


async def _assert_task_detail_toolbar_controls_are_reachable(
    app: DashboardApp,
    pilot: Pilot[None],
) -> None:
    toolbar = app.screen.query_one("#task-detail-toolbar")
    for control in toolbar.query(Button):
        if control.disabled:
            continue
        control.focus()
        await pilot.pause()
        assert control.region.width > 0
        assert control.region.x >= 0
        assert control.region.right <= app.size.width
        assert control.region.overlaps(toolbar.region)


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
            actions = task_screen.query_one(f"#{TASK_DETAIL_ACTIONS_BUTTON}", Button)
            actions.focus()
            await pilot.press("enter")
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
            views_button = app.query_one("#focus-views", Button)
            help_button = app.query_one("#focus-help", Button)
            assert app.focused is app.query_one("#focus-views", Button)
            assert footer.region.height >= 2
            assert footer.region.bottom <= app.size.height
            assert views_button.region.right <= app.size.width
            assert help_button.region.right <= app.size.width
            assert help_button.region.bottom <= app.size.height
            await _choose_focus_view(app, pilot, "Workflow · Strips")
            strips = app.screen.query_one(StripsPanel)
            strip_text = _rendered_text(strips)
            assert task_id in strip_text
            assert "Escalated" in strip_text
            workflow_screen = app.screen
            await pilot.press("escape")
            await wait_until(lambda: app.screen is not workflow_screen, timeout=2)
            home_screen = app.screen
            assert await pilot.click("#focus-help")
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
    reader = StateReader(database, SystemClock())
    try:
        app = _app(reader, project_dir, widget_names=["usage", "docker"])
        async with app.run_test(size=(120, 40)) as pilot:
            home_screen = app.screen
            await _choose_focus_view(
                app, pilot, "Usage details · subscriptions, costs, coverage and quota"
            )
            usage = app.screen.query_one("#usage", Static)
            assert "Token values unknown" in plain_text(usage.content)
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
            await pilot.click("#focus-views")
            await wait_until(lambda: bool(app.screen.query("#focus-views-list")), timeout=2)
            await _choose_focus_view(app, pilot, "Extensions · Outside-Widget", mouse=True)
            assert app.screen is not workspace_screen
            extension_panel = app.screen.query_one("#external-widget-panel", Static)
            assert extension_panel.region.width > 0
            await pilot.press("escape")
            await wait_until(lambda: app.screen is workspace_screen, timeout=2)

            assert await pilot.click("#focus-help")
            await pilot.pause()
            assert app.screen is not workspace_screen
            assert app.screen.query_one("#workspace-help-copy", Static).region.height > 0
            assert await pilot.click("#workspace-help-close", offset=(6, 1))
            await pilot.pause()
            assert app.screen is workspace_screen
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
