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
from shikiru.dashboard.detail import (
    TASK_DETAIL_BRIEFING,
    TASK_DETAIL_FOLDS,
    TASK_DETAIL_VERBS,
    TASK_DETAIL_VIEWS_KEY,
    TaskDetailPanel,
)
from shikiru.dashboard.detail_fold import DetailFold, DetailFolds, FoldHeader
from shikiru.dashboard.live_settings import LiveSettings
from shikiru.dashboard.motion import DashboardMotion
from shikiru.dashboard.services import TaskWorkService
from shikiru.dashboard.views import StateDashboardViews
from shikiru.operator.queries import OperatorQueries
from shikiru.process import SubprocessRunner
from shikiru.ui.gated_static import GatedStatic
from shikiru.ui.theme import install_theme
from shikiru.workspace.facts import TaskWorkObserver
from shikiru.workspace.git import Git
from tests.support.render import plain_text
from tests.support.waiting import UI_WAIT_SECONDS, WaitTimeoutError, wait_until

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


class ManualFocusHost(WidgetHostApp):
    """Disable startup autofocus when a test controls a widget's focus explicitly."""

    AUTO_FOCUS = None


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
    await focus_inspector_details(pilot)
    await pilot.press(TASK_DETAIL_VIEWS_KEY)
    await pick_details_entry(pilot, entry_id)


async def focus_inspector_details(pilot: Pilot[None]) -> None:
    panel = pilot.app.screen.query_one("#task-detail-panel", TaskDetailPanel)
    await wait_until(
        lambda: bool(panel.query_one(f"#{TASK_DETAIL_BRIEFING}", GatedStatic).content),
        timeout=UI_WAIT_SECONDS,
    )
    target = _summary_focus_target(panel)
    if pilot.app.focused is not target:
        panel.focus_summary()
    try:
        await wait_until(lambda: pilot.app.focused is target, timeout=UI_WAIT_SECONDS)
    except WaitTimeoutError as error:
        folds = panel.query_one(f"#{TASK_DETAIL_FOLDS}", DetailFolds)
        target_state = f"{type(target).__name__}#{target.id!r}"
        message = (
            f"task detail summary focus did not reach {target_state}; "
            f"current=({_focus_state(pilot, folds)})"
        )
        raise AssertionError(message) from error


async def choose_inspector_details_entry(pilot: Pilot[None], entry_id: str) -> None:
    await choose_details_entry(pilot, entry_id)


def _summary_focus_target(panel: TaskDetailPanel) -> Widget:
    verbs = panel.query_one(f"#{TASK_DETAIL_VERBS}")
    first_verb = next(iter(verbs.displayed_children), None)
    if first_verb is not None:
        return first_verb
    return panel.query_one(f"#{TASK_DETAIL_FOLDS}", DetailFolds).current_fold.header


def _focused_fold(pilot: Pilot[None]) -> DetailFold | None:
    focused = pilot.app.focused
    parent = focused.parent if isinstance(focused, FoldHeader) else None
    return parent if isinstance(parent, DetailFold) else None


def _focus_state(pilot: Pilot[None], folds: DetailFolds) -> str:
    focused = pilot.app.focused
    fold = (
        next((item for item in focused.ancestors_with_self if isinstance(item, DetailFold)), None)
        if focused is not None
        else None
    )
    widget_type = type(focused).__name__ if focused is not None else None
    widget_id = getattr(focused, "id", None)
    focused_view = fold.view_id if fold is not None else None
    return (
        f"widget={widget_type!r} id={widget_id!r} fold={focused_view!r} "
        f"current_fold={folds.current_fold.view_id!r}"
    )


async def pick_details_entry(pilot: Pilot[None], entry_id: str) -> None:
    await wait_until(lambda: _focused_fold(pilot) is not None, timeout=UI_WAIT_SECONDS)
    fold = cast("DetailFold", _focused_fold(pilot))
    folds = fold.folds
    ids = [item.view_id for item in fold.folds.visible_folds]
    step = 1 if ids.index(entry_id) > ids.index(fold.view_id) else -1
    current, target = ids.index(fold.view_id), ids.index(entry_id)
    for expected in (ids[index] for index in range(current + step, target + step, step)):
        source = _focused_fold(pilot)
        before_key = _focus_state(pilot, folds)
        key = "down" if step > 0 else "up"
        await pilot.press(key)
        after_key = _focus_state(pilot, folds)
        try:
            await wait_until(
                lambda expected=expected: (
                    getattr(_focused_fold(pilot), "view_id", None) == expected
                ),
                timeout=UI_WAIT_SECONDS,
            )
        except WaitTimeoutError as error:
            current_state = _focus_state(pilot, folds)
            source_view = source.view_id if source is not None else None
            message = (
                f"detail fold focus did not reach {expected!r} from {source_view!r} after {key!r}; "
                f"before_key=({before_key}); after_key=({after_key}); current=({current_state})"
            )
            raise AssertionError(message) from error
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


def live_work_service(clock: Clock, settings: Settings) -> TaskWorkService:
    """Return the live-work service the dashboard builds, reading real worktrees through git."""
    git = Git(SubprocessRunner(), command_timeout=settings.orchestration.timeouts.git_command)
    return TaskWorkService(
        TaskWorkObserver(git),
        clock,
        cache_ttl_seconds=settings.dashboard.task_work_cache_seconds,
    )


def state_views(
    reader: StateReader,
    project_dir: Path,
    clock: Clock,
    services: StubServices,
    *,
    focus_task: str | None = None,
    settings: Settings | None = None,
) -> StateDashboardViews:
    live_settings = _live_settings(reader, project_dir, settings)
    settings = live_settings.current()
    return StateDashboardViews(
        reader,
        focus_task=focus_task,
        settings=live_settings,
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
            reader,
            project_dir,
            live_settings,
            RoleViewTail(settings.dashboard.last_words_chars),
        ),
        work=live_work_service(clock, settings),
    )


def fixed_settings(reader: StateReader, settings: Settings) -> LiveSettings:
    """Return a settings source that applies `settings` and never reloads."""
    return LiveSettings(reader, lambda: settings)


def with_dashboard(settings: Settings, **values: object) -> Settings:
    """Return `settings` with the dashboard section's `values` replaced."""
    return settings.model_copy(update={"dashboard": settings.dashboard.model_copy(update=values)})


def _live_settings(reader: StateReader, project_dir: Path, fixed: Settings | None) -> LiveSettings:
    if fixed is not None:
        return fixed_settings(reader, fixed)
    return LiveSettings(reader, lambda: load_settings(project_dir))


def widget_context(
    reader: StateReader,
    project_dir: Path,
    clock: Clock,
    *,
    services: StubServices | None = None,
    open_palette: OpenPalette | None = None,
    open_resolution: OpenResolution | None = None,
    focus_task: str | None = None,
    settings: Settings | None = None,
) -> WidgetContext:
    return WidgetContext(
        views=state_views(
            reader,
            project_dir,
            clock,
            services or StubServices(),
            focus_task=focus_task,
            settings=settings,
        ),
        focus_task=focus_task,
        settings=_live_settings(reader, project_dir, settings),
        clock=clock,
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
    open_palette_context: OpenPaletteContext | None = None,
    resolution_screen: ResolutionScreenFactory | None = None,
    focus_task: str | None = None,
    navigate_home: NavigateHome | None = None,
    motion_interval_seconds: float | None = None,
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
        motion_interval_seconds=motion_interval_seconds,
        settings=fixed_settings(reader, with_dashboard(settings, animations=False)),
        open_palette=ignore_palette_action,
        widget_names=widget_names,
        open_palette_context=open_palette_context,
        resolution_screen=resolution_screen,
        navigate_home=navigate_home,
    )
