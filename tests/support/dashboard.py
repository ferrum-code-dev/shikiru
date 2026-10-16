"""Shared dashboard test doubles: services stub, widget host app and widget context."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from textual.app import App, ComposeResult
from textual.widgets import Static

from shikiru.config.loader import load_settings
from shikiru.dashboard.activity import RoleViewTail
from shikiru.dashboard.app import DashboardApp
from shikiru.dashboard.contract import (
    DiagnosticSampleState,
    DiskView,
    DockerView,
    RepoStatus,
    ResourceView,
    WidgetContext,
)
from shikiru.dashboard.detail import TASK_DETAIL_BRIEFING, TaskDetailPanel
from shikiru.dashboard.detail_fold import DetailFold, FoldHeader
from shikiru.dashboard.motion import DashboardMotion
from shikiru.dashboard.views import StateDashboardViews
from shikiru.operator.queries import OperatorQueries
from shikiru.ui.gated_static import GatedStatic
from shikiru.ui.theme import install_theme
from tests.support.render import plain_text
from tests.support.waiting import UI_WAIT_SECONDS, wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from textual.dom import DOMNode
    from textual.pilot import Pilot
    from textual.widget import Widget

    from shikiru.clock import Clock
    from shikiru.config.settings import Settings
    from shikiru.dashboard.contract import (
        NavigateHome,
        OpenPalette,
        OpenPaletteContext,
        OpenResolution,
        ResolutionScreenFactory,
    )
    from shikiru.process import ProcessIdentity
    from shikiru.store.reader import StateReader

REPO_AHEAD = 1
REPO_DIRTY = 2
RESOURCES = ResourceView(10.0, 40.0, 4.0, 2.0, 0.2)


class StubServices:
    """Dashboard data sources with fixed answers and no processes behind them."""

    def __init__(
        self,
        *,
        engine_running: bool = False,
        engine_process_identity: ProcessIdentity | None = None,
        repo_status_value: RepoStatus | None = None,
    ) -> None:
        self.engine_running = engine_running
        self._engine_process_identity = engine_process_identity
        self._repo_status_value = repo_status_value
        self.resource_observation: ResourceView | None = None

    async def repo_status(self) -> RepoStatus:
        return self._repo_status_value or RepoStatus(
            branch="main", ahead=REPO_AHEAD, behind=0, dirty=REPO_DIRTY
        )

    def engine_up(self) -> bool:
        return self.engine_running

    def engine_identity(self) -> ProcessIdentity | None:
        return self._engine_process_identity

    async def resources(self) -> ResourceView:
        return self.resource_observation or RESOURCES

    def resource_sample(self) -> ResourceView | None:
        return self.resource_observation

    async def containers(self) -> DockerView:
        return DockerView(available=False)

    async def disk(self) -> DiskView:
        return DiskView(state=DiagnosticSampleState.UNAVAILABLE)


class WidgetHostApp(App[None]):
    """Hosts one widget so its rendering and refresh can be observed."""

    def __init__(self, widget: Widget) -> None:
        super().__init__()
        install_theme(self)
        self._widget = widget

    def compose(self) -> ComposeResult:
        yield self._widget


async def ignore_palette_action(action: str) -> None:
    del action


async def _activate_control(pilot: Pilot[None], selector: str) -> None:
    origin = pilot.app.screen
    pilot.app.screen.query_one(selector).focus()
    await pilot.press("enter")
    await wait_until(lambda: pilot.app.screen is not origin, timeout=UI_WAIT_SECONDS)


async def open_command_history(pilot: Pilot[None]) -> None:
    await _activate_control(pilot, "#workspace-command-status")


async def open_project_diagnostics(pilot: Pilot[None]) -> None:
    await _activate_control(pilot, "#resources")


async def open_usage_details(pilot: Pilot[None]) -> None:
    await _activate_control(pilot, "#usage")


async def choose_details_entry(pilot: Pilot[None], entry_id: str) -> None:
    await pilot.press("v")
    await pick_details_entry(pilot, entry_id)


async def focus_inspector_details(pilot: Pilot[None]) -> None:
    panel = pilot.app.screen.query_one("#task-detail-panel", TaskDetailPanel)
    await wait_until(
        lambda: bool(panel.query_one(f"#{TASK_DETAIL_BRIEFING}", GatedStatic).content),
        timeout=UI_WAIT_SECONDS,
    )
    panel.focus_summary()
    await wait_until(
        lambda: pilot.app.focused is not None and panel in pilot.app.focused.ancestors,
        timeout=UI_WAIT_SECONDS,
    )


async def choose_inspector_details_entry(pilot: Pilot[None], entry_id: str) -> None:
    await focus_inspector_details(pilot)
    await choose_details_entry(pilot, entry_id)


def _focused_fold(pilot: Pilot[None]) -> DetailFold | None:
    focused = pilot.app.focused
    parent = focused.parent if isinstance(focused, FoldHeader) else None
    return parent if isinstance(parent, DetailFold) else None


async def pick_details_entry(pilot: Pilot[None], entry_id: str) -> None:
    await wait_until(lambda: _focused_fold(pilot) is not None, timeout=UI_WAIT_SECONDS)
    fold = cast("DetailFold", _focused_fold(pilot))
    ids = [item.view_id for item in fold.folds.all_folds]
    step = 1 if ids.index(entry_id) > ids.index(fold.view_id) else -1
    current, target = ids.index(fold.view_id), ids.index(entry_id)
    for expected in (ids[index] for index in range(current + step, target + step, step)):
        await pilot.press("down" if step > 0 else "up")
        await wait_until(
            lambda expected=expected: getattr(_focused_fold(pilot), "view_id", None) == expected,
            timeout=UI_WAIT_SECONDS,
        )
    target = cast("DetailFold", _focused_fold(pilot))
    if target.is_open:
        await pilot.press("enter")
        await wait_until(lambda: not target.is_open, timeout=UI_WAIT_SECONDS)
    await pilot.press("enter")


def fold_headline(root: DOMNode, view_id: str) -> str:
    """Return the headline shown on the header of the fold `view_id` under `root`."""
    fold = next(item for item in root.query(DetailFold) if item.view_id == view_id)
    return fold.headline or ""


def list_rows(root: DOMNode, list_id: str) -> list[str]:
    """Return the plain text of every row shown in the row list `list_id` under `root`."""
    return [
        plain_text(row.content) for row in root.query(f"#{list_id} .row-list-row").results(Static)
    ]


def state_views(
    reader: StateReader,
    project_dir: Path,
    clock: Clock,
    services: StubServices,
    *,
    focus_task: str | None = None,
    settings: Settings | None = None,
) -> StateDashboardViews:
    settings = settings or load_settings(project_dir)
    return StateDashboardViews(
        reader,
        focus_task=focus_task,
        settings=settings,
        sources=services,
        queries=OperatorQueries(
            reader,
            project=settings.project.name,
            project_root=project_dir,
            engine_up=services.engine_up,
            max_parallel=settings.orchestration.max_parallel,
            clock=clock,
        ),
        clock=clock,
        motion=DashboardMotion(
            reader, project_dir, settings, RoleViewTail(settings.dashboard.last_words_chars)
        ),
    )


def widget_context(
    reader: StateReader,
    project_dir: Path,
    clock: Clock,
    *,
    services: StubServices | None = None,
    open_palette: OpenPalette | None = None,
    open_resolution: OpenResolution | None = None,
    focus_task: str | None = None,
) -> WidgetContext:
    settings = load_settings(project_dir)
    return WidgetContext(
        views=state_views(
            reader,
            project_dir,
            clock,
            services or StubServices(),
            focus_task=focus_task,
        ),
        focus_task=focus_task,
        resources_refresh_seconds=settings.orchestration.resources.sample_seconds,
        docker_refresh_seconds=settings.dashboard.docker_refresh_seconds,
        disk_refresh_seconds=settings.dashboard.disk_refresh_seconds,
        timeline_refresh_seconds=settings.dashboard.timeline_refresh_seconds,
        now_max_lines=settings.dashboard.now_max_lines,
        now_compact_lines=settings.dashboard.now_compact_lines,
        quota_warn_pct=settings.dashboard.gauges.quota_warn_pct,
        quota_alert_pct=settings.dashboard.gauges.quota_alert_pct,
        clock=clock,
        animations=settings.dashboard.animations,
        on_change=reader.watch,
        on_motion=lambda _: lambda: None,
        open_palette=open_palette or ignore_palette_action,
        open_resolution=open_resolution,
    )


def dashboard_app(
    reader: StateReader,
    project_dir: Path,
    clock: Clock,
    *,
    widget_names: list[str],
    settings: Settings | None = None,
    services: StubServices | None = None,
    resources_refresh_seconds: float | None = None,
    open_palette_context: OpenPaletteContext | None = None,
    resolution_screen: ResolutionScreenFactory | None = None,
    focus_task: str | None = None,
    navigate_home: NavigateHome | None = None,
) -> DashboardApp:
    """Build the Home dashboard, or the fixed view of `focus_task`, over `reader`."""
    settings = settings or load_settings(project_dir)
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
        motion_interval_seconds=None,
        resources_refresh_seconds=(
            resources_refresh_seconds or settings.orchestration.resources.sample_seconds
        ),
        docker_refresh_seconds=settings.dashboard.docker_refresh_seconds,
        disk_refresh_seconds=settings.dashboard.disk_refresh_seconds,
        quota_warn_pct=settings.dashboard.gauges.quota_warn_pct,
        quota_alert_pct=settings.dashboard.gauges.quota_alert_pct,
        animations=False,
        open_palette=ignore_palette_action,
        widget_names=widget_names,
        open_palette_context=open_palette_context,
        resolution_screen=resolution_screen,
        navigate_home=navigate_home,
        timeline_refresh_seconds=settings.dashboard.timeline_refresh_seconds,
        now_max_lines=settings.dashboard.now_max_lines,
        now_compact_lines=settings.dashboard.now_compact_lines,
    )
