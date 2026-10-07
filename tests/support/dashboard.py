"""Shared dashboard test doubles: services stub, widget host app and widget context."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import App, ComposeResult
from textual.widgets import Label, ListItem, ListView

from shikiru.config.loader import load_settings
from shikiru.dashboard.activity import RoleViewTail
from shikiru.dashboard.contract import DockerView, RepoStatus, ResourceView, WidgetContext
from shikiru.dashboard.motion import DashboardMotion
from shikiru.dashboard.views import StateDashboardViews
from shikiru.operator.queries import OperatorQueries
from shikiru.ui.theme import install_theme
from tests.support.render import plain_text
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from textual.pilot import Pilot
    from textual.widget import Widget

    from shikiru.clock import Clock
    from shikiru.config.settings import Settings
    from shikiru.dashboard.contract import OpenPalette
    from shikiru.process import ProcessIdentity
    from shikiru.store.reader import StateReader

REPO_AHEAD = 1
REPO_DIRTY = 2
FOCUS_VIEW_WAIT_SECONDS = 2
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


async def choose_focus_view(pilot: Pilot[None], label: str) -> None:
    await pilot.press("v")
    await wait_until(
        lambda: bool(pilot.app.screen.query("#focus-views-list")),
        timeout=FOCUS_VIEW_WAIT_SECONDS,
    )
    menu = pilot.app.screen
    view_list = menu.query_one("#focus-views-list", ListView)
    items = [item for item in view_list.children if isinstance(item, ListItem)]
    view_list.index = next(
        index
        for index, item in enumerate(items)
        if plain_text(item.query_one(Label).render()) == label
    )
    view_list.focus()
    await pilot.press("enter")
    await wait_until(
        lambda: pilot.app.screen is not menu,
        timeout=FOCUS_VIEW_WAIT_SECONDS,
    )
    await pilot.pause()


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
        timeline_refresh_seconds=settings.dashboard.timeline_refresh_seconds,
        clock=clock,
        on_change=reader.watch,
        on_motion=lambda _: lambda: None,
        open_palette=open_palette or ignore_palette_action,
    )
