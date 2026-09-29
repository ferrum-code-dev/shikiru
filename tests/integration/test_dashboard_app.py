from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import pytest
from textual.containers import VerticalScroll
from textual.widgets import TextArea

import shikiru.agents.registry as registry_module
from shikiru.clock import SystemClock
from shikiru.config.loader import load_settings
from shikiru.dashboard.activity import RoleViewTail
from shikiru.dashboard.app import DashboardApp, WidgetPluginError
from shikiru.dashboard.contract import DockerView, RepoStatus, ResourceView
from shikiru.dashboard.motion import DashboardMotion
from shikiru.dashboard.palette import PaletteScreen
from shikiru.dashboard.views import StateDashboardViews
from shikiru.domain.steps import first_step
from shikiru.domain.tasks import Mode
from shikiru.operator.commands import OperatorCommands
from shikiru.operator.queries import OperatorQueries
from shikiru.plugins.widgets.strips import StripsPanel
from shikiru.store.commands import CommandQueue
from shikiru.store.reader import StateReader
from shikiru.store.tasks import TaskStore
from tests.support.clock import FakeClock
from tests.support.config import write_minimal_project
from tests.support.store import seed_task
from tests.support.waiting import wait_until

_TEST_START = datetime(2026, 1, 1, tzinfo=UTC)

if TYPE_CHECKING:
    from pathlib import Path

    from rich.text import Text

    from shikiru.store.database import Database
    from tests.support.store import Connection


class _StubServices:
    async def repo_status(self) -> RepoStatus:
        return RepoStatus(branch="main", ahead=0, behind=0, dirty=0)

    def engine_up(self) -> bool:
        return False

    async def resources(self) -> ResourceView:
        return ResourceView(10.0, 40.0, 4.0, 2.0, 0.2)

    async def containers(self) -> DockerView:
        return DockerView(available=False, containers=())


async def _open_palette(action: str) -> None:
    del action


async def _unexpected_pane_selection(pane_id: str) -> None:
    del pane_id
    raise AssertionError


def _views(reader: StateReader, project_dir: Path, sources: _StubServices) -> StateDashboardViews:
    settings = load_settings(project_dir)
    return StateDashboardViews(
        reader,
        focus_task=None,
        settings=settings,
        sources=sources,
        queries=OperatorQueries(
            reader,
            project=settings.project.name,
            project_root=project_dir,
            engine_up=sources.engine_up,
            max_parallel=settings.orchestration.max_parallel,
        ),
        clock=SystemClock(),
        motion=DashboardMotion(reader, project_dir, settings, RoleViewTail()),
    )


def _motion_interval(project_dir: Path) -> float | None:
    dashboard = load_settings(project_dir).dashboard
    return 1 / dashboard.motion.refresh_hz if dashboard.animations else None


async def test_dashboard_app_rejects_an_unregistered_widget_name(
    database: Database, connection: Connection, tmp_path: Path
) -> None:
    del connection
    project_dir = write_minimal_project(tmp_path)
    reader = StateReader(database, SystemClock())
    try:
        app = DashboardApp(
            focus_task=None,
            views=_views(reader, project_dir, _StubServices()),
            on_change=reader.watch,
            clock=SystemClock(),
            motion_interval_seconds=_motion_interval(project_dir),
            resources_refresh_seconds=load_settings(
                project_dir
            ).orchestration.resources.sample_seconds,
            docker_refresh_seconds=load_settings(project_dir).dashboard.docker_refresh_seconds,
            open_palette=_open_palette,
            widget_names=["no-such-widget"],
        )
        with pytest.raises(WidgetPluginError):
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
    reader = StateReader(database, SystemClock())
    settings = load_settings(project_dir)
    try:
        app = DashboardApp(
            focus_task=None,
            views=_views(reader, project_dir, _StubServices()),
            on_change=reader.watch,
            clock=SystemClock(),
            motion_interval_seconds=_motion_interval(project_dir),
            resources_refresh_seconds=settings.orchestration.resources.sample_seconds,
            docker_refresh_seconds=settings.dashboard.docker_refresh_seconds,
            open_palette=_open_palette,
            widget_names=settings.dashboard.widgets,
        )
        async with app.run_test(size=(72, 64)) as pilot:
            await pilot.pause()
            dashboard = app.query_one("#dashboard-panels", VerticalScroll)
            strips = app.query_one(StripsPanel)
            escalations = app.query_one("#escalations")
            docker = app.query_one("#docker")
            assert not escalations.display
            assert not docker.display
            assert dashboard.scroll_y == 0
            assert strips.region.y < app.size.height
            assert task.id in cast("Text", strips.content).plain
    finally:
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
        app = DashboardApp(
            focus_task=None,
            views=_views(reader, project_dir, _StubServices()),
            on_change=reader.watch,
            clock=SystemClock(),
            motion_interval_seconds=_motion_interval(project_dir),
            resources_refresh_seconds=settings.orchestration.resources.sample_seconds,
            docker_refresh_seconds=settings.dashboard.docker_refresh_seconds,
            open_palette=_open_palette,
            widget_names=settings.dashboard.widgets,
        )
        async with app.run_test(size=(30, 24)) as pilot:
            await pilot.pause()
            dashboard = app.query_one("#dashboard-panels", VerticalScroll)
            assert dashboard.size.height > 0
            strips = app.query_one(StripsPanel)
            usage = app.query_one("#usage")
            resources = app.query_one("#resources")
            assert dashboard.scroll_y == 0
            assert strips.region.y < app.size.height
            assert task.id in cast("Text", strips.content).plain
            dashboard.scroll_to_widget(usage, animate=False)
            dashboard.scroll_to_widget(resources, animate=False)
            await pilot.pause()
            assert resources.region.y < dashboard.region.bottom
            assert resources.region.bottom > dashboard.region.y
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
    )
    opened: list[str] = []
    app_instance: DashboardApp | None = None

    async def open_palette(action: str) -> None:
        opened.append(action)
        if app_instance is None:
            message = "dashboard app has not been initialized"
            raise RuntimeError(message)
        await app_instance.push_screen(PaletteScreen(commands, queries, _unexpected_pane_selection))

    try:
        app_instance = DashboardApp(
            focus_task=None,
            views=_views(reader, project_dir, _StubServices()),
            on_change=reader.watch,
            clock=SystemClock(),
            motion_interval_seconds=_motion_interval(project_dir),
            resources_refresh_seconds=load_settings(
                project_dir
            ).orchestration.resources.sample_seconds,
            docker_refresh_seconds=load_settings(project_dir).dashboard.docker_refresh_seconds,
            open_palette=open_palette,
            widget_names=[],
        )
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


class _FakeDistribution:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeEntryPoint:
    def __init__(self, name: str, dist_name: str) -> None:
        self.name = name
        self.dist = _FakeDistribution(dist_name)

    def load(self) -> None:
        message = "an ambiguous widget entry point must never be loaded"
        raise AssertionError(message)


async def test_dashboard_app_rejects_an_ambiguous_widget_name(
    database: Database,
    connection: Connection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del connection
    duplicates = [_FakeEntryPoint("dup", "dist-one"), _FakeEntryPoint("dup", "dist-two")]

    def _fake_entry_points(*, group: str) -> list[_FakeEntryPoint]:
        del group
        return duplicates

    monkeypatch.setattr(registry_module, "entry_points", _fake_entry_points)
    project_dir = write_minimal_project(tmp_path)
    reader = StateReader(database, SystemClock())
    try:
        app = DashboardApp(
            focus_task=None,
            views=_views(reader, project_dir, _StubServices()),
            on_change=reader.watch,
            clock=SystemClock(),
            motion_interval_seconds=_motion_interval(project_dir),
            resources_refresh_seconds=load_settings(
                project_dir
            ).orchestration.resources.sample_seconds,
            docker_refresh_seconds=load_settings(project_dir).dashboard.docker_refresh_seconds,
            open_palette=_open_palette,
            widget_names=["dup"],
        )
        with pytest.raises(WidgetPluginError, match="dist-one") as excinfo:
            async with app.run_test():
                pass
        assert "dist-two" in str(excinfo.value)
    finally:
        reader.close()
