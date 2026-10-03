"""Shared dashboard test doubles: services stub, widget host app and widget context."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import App, ComposeResult

from shikiru.config.loader import load_settings
from shikiru.dashboard.activity import RoleViewTail
from shikiru.dashboard.contract import DockerView, RepoStatus, ResourceView, WidgetContext
from shikiru.dashboard.motion import DashboardMotion
from shikiru.dashboard.views import StateDashboardViews
from shikiru.operator.queries import OperatorQueries
from shikiru.ui.theme import install_theme

if TYPE_CHECKING:
    from pathlib import Path

    from textual.widget import Widget

    from shikiru.clock import Clock
    from shikiru.config.settings import Settings
    from shikiru.dashboard.contract import OpenPalette
    from shikiru.store.reader import StateReader

REPO_AHEAD = 1
REPO_DIRTY = 2
RESOURCES = ResourceView(10.0, 40.0, 4.0, 2.0, 0.2)


class StubServices:
    """Dashboard data sources with fixed answers and no processes behind them."""

    def __init__(self, *, engine_running: bool = False) -> None:
        self._engine_running = engine_running
        self.resource_observation: ResourceView | None = None

    async def repo_status(self) -> RepoStatus:
        return RepoStatus(branch="main", ahead=REPO_AHEAD, behind=0, dirty=REPO_DIRTY)

    def engine_up(self) -> bool:
        return self._engine_running

    async def resources(self) -> ResourceView:
        return RESOURCES

    def resource_sample(self) -> ResourceView | None:
        return self.resource_observation

    async def containers(self) -> DockerView:
        return DockerView(available=False, containers=())


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
        motion=DashboardMotion(reader, project_dir, settings, RoleViewTail()),
    )


def widget_context(
    reader: StateReader,
    project_dir: Path,
    clock: Clock,
    *,
    services: StubServices | None = None,
    open_palette: OpenPalette | None = None,
) -> WidgetContext:
    settings = load_settings(project_dir)
    return WidgetContext(
        views=state_views(reader, project_dir, clock, services or StubServices()),
        focus_task=None,
        resources_refresh_seconds=settings.orchestration.resources.sample_seconds,
        docker_refresh_seconds=settings.dashboard.docker_refresh_seconds,
        timeline_refresh_seconds=settings.dashboard.timeline_refresh_seconds,
        clock=clock,
        on_change=reader.watch,
        on_motion=lambda _: lambda: None,
        open_palette=open_palette or ignore_palette_action,
    )
