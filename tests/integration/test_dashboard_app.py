from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import pytest
from textual.containers import VerticalScroll
from textual.widgets import Button, DataTable, Static, TextArea

import shikiru.agents.registry as registry_module
from shikiru.clock import SystemClock
from shikiru.config.loader import load_settings
from shikiru.dashboard.app import DashboardApp, WidgetPluginError
from shikiru.dashboard.contract import WIDGET_ENTRY_POINT_GROUP
from shikiru.dashboard.palette import PaletteScreen
from shikiru.domain.steps import Transition, first_step
from shikiru.domain.tasks import Hold, Mode, Status
from shikiru.operator.commands import OperatorCommands
from shikiru.operator.queries import OperatorQueries
from shikiru.plugins.widgets.strips import StripsPanel
from shikiru.store.commands import CommandQueue
from shikiru.store.reader import StateReader
from shikiru.store.tasks import TaskStore
from tests.support.clock import FakeClock
from tests.support.config import write_minimal_project
from tests.support.dashboard import StubServices, ignore_palette_action, state_views
from tests.support.entry_points import FakeDistribution, ambiguous_entry_points
from tests.support.store import seed_task
from tests.support.waiting import wait_until

_TEST_START = datetime(2026, 1, 1, tzinfo=UTC)
_EXTERNAL_WIDGET_NAME = "outside-widget"


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


def _rendered_text(widget: Static) -> str:
    return cast("Text", widget.render()).plain


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

    from shikiru.dashboard.contract import OpenPalette, WidgetContext
    from shikiru.store.database import Database
    from tests.support.store import Connection


async def _unexpected_pane_selection(pane_id: str) -> None:
    del pane_id
    raise AssertionError


async def _focus_activity_table(
    app: DashboardApp, pilot: Pilot[None]
) -> tuple[Static, VerticalScroll, DataTable[object]]:
    await pilot.press("tab")
    await pilot.pause()
    activity_tab = app.query_one("#section-tab-activity", Button)
    footer = app.query_one("#workspace-footer-copy", Static)
    footer_controls = app.query_one("#workspace-footer-controls")
    dashboard = app.query_one("#dashboard-panels", VerticalScroll)
    dialogue_table = cast("DataTable[object]", app.query_one("#dialogue-table", DataTable))
    assert app.focused is activity_tab
    assert _rendered_text(footer) == "> Activity"
    assert footer_controls.region.bottom <= app.size.height
    assert dashboard.size.height >= 7
    assert dialogue_table.region.height > 0
    assert dialogue_table.region.y >= dashboard.region.y
    assert dialogue_table.region.bottom <= dashboard.region.bottom

    dashboard.scroll_to(y=dashboard.scroll_y + 1, animate=False)
    await pilot.pause()
    section_scroll = dashboard.scroll_y
    assert await pilot.click("#dialogue-table")
    await pilot.pause()
    assert app.focused is dialogue_table
    assert dashboard.scroll_y == section_scroll
    assert _rendered_text(footer) == "> Activity"
    assert _rendered_text(app.query_one("#section-heading-activity", Static)) == "> Activity"
    return footer, dashboard, dialogue_table


def _app(
    reader: StateReader,
    project_dir: Path,
    *,
    widget_names: list[str],
    open_palette: OpenPalette = ignore_palette_action,
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
        app = _app(reader, project_dir, widget_names=["no-such-widget"])
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
        app = _app(reader, project_dir, widget_names=settings.dashboard.widgets)
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
        app = _app(reader, project_dir, widget_names=settings.dashboard.widgets)
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
            footer = app.query_one("#workspace-footer-copy", Static)
            footer_controls = app.query_one("#workspace-footer-controls")
            help_button = app.query_one("#workspace-help", Button)
            expand_button = app.query_one("#workspace-expand", Button)
            strips = app.query_one(StripsPanel)
            assert app.focused is app.query_one("#section-tab-workspace", Button)
            assert _rendered_text(footer) == "> Workspace"
            assert footer.region.height == 1
            assert footer.region.bottom <= app.size.height
            assert footer_controls.region.bottom <= app.size.height
            assert help_button.region.right <= app.size.width
            assert help_button.region.bottom <= app.size.height
            assert expand_button.region.right <= app.size.width
            strip_text = _rendered_text(strips)
            assert task_id in strip_text
            assert "Escalated" in strip_text
            if size[0] > 36:
                assert "PAUSED" in strip_text

            workspace_screen = app.screen
            assert await pilot.click("#workspace-help")
            await pilot.pause()
            assert app.screen is not workspace_screen
            assert "Workspace" in _rendered_text(
                app.screen.query_one("#workspace-help-context", Static)
            )
            await pilot.press("escape")
            await pilot.pause()
            assert app.screen is workspace_screen

            assert await pilot.click("#workspace-expand")
            await pilot.pause()
            back_button = app.query_one("#workspace-return", Button)
            assert back_button.region.right <= app.size.width
            assert back_button.region.bottom <= app.size.height
            assert await pilot.click("#workspace-return")
            await pilot.pause()
            assert app.focused is expand_button
    finally:
        reader.close()


async def test_dashboard_activity_widget_focus_expands_and_restores_scroll(
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
            footer, dashboard, dialogue_table = await _focus_activity_table(app, pilot)
            section_scroll = dashboard.scroll_y

            await pilot.press("z")
            await pilot.pause()
            assert app.query_one("#section-activity").display
            assert not app.query_one("#section-workspace").display
            assert not app.query_one("#section-usage").display
            assert app.focused is app.query_one("#workspace-return", Button)
            assert _rendered_text(footer) == "Activity expanded"
            assert dialogue_table.region.height > 0

            await pilot.press("escape")
            await pilot.pause()
            assert app.focused is dialogue_table
            assert app.query_one("#section-usage").display
            assert dashboard.scroll_y == section_scroll
            assert _rendered_text(footer) == "> Activity"

            workspace_screen = app.screen
            await pilot.press("?")
            await pilot.pause()
            assert app.screen is not workspace_screen
            assert app.screen.query_one("#workspace-help-copy", Static).region.height > 0
            assert "Activity" in _rendered_text(
                app.screen.query_one("#workspace-help-context", Static)
            )
            await pilot.press("escape")
            await pilot.pause()
            assert app.screen is workspace_screen
    finally:
        reader.close()


async def test_dashboard_mouse_navigation_expands_and_restores_external_widget(
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
            assert await pilot.click("#section-tab-extensions", offset=(7, 1))
            await pilot.pause()
            extension_tab = app.query_one("#section-tab-extensions", Button)
            assert app.focused is extension_tab
            footer = app.query_one("#workspace-footer-copy", Static)
            footer_text = _rendered_text(footer)
            assert footer_text.startswith(">")
            assert "Extensions" in footer_text

            workspace_screen = app.screen
            assert await pilot.click("#workspace-help", offset=(4, 1))
            await pilot.pause()
            assert app.screen is not workspace_screen
            assert app.screen.query_one("#workspace-help-copy", Static).region.height > 0
            assert await pilot.click("#workspace-help-close", offset=(6, 1))
            await pilot.pause()
            assert app.screen is workspace_screen
            assert app.focused is app.query_one("#workspace-help", Button)

            assert await pilot.click("#workspace-expand", offset=(5, 1))
            await pilot.pause()
            assert not app.query_one("#section-usage").display
            assert app.query_one("#section-extensions").display
            extension_panel = app.query_one("#section-extensions").query_one(
                "#external-widget-panel", Static
            )
            assert extension_panel.region.width > 0

            assert await pilot.click("#workspace-return", offset=(3, 1))
            await pilot.pause()
            assert app.focused is app.query_one("#workspace-expand", Button)
            assert app.query_one("#section-usage").display
            assert app.query_one("#section-extensions").display
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
        await app_instance.push_screen(PaletteScreen(commands, queries, _unexpected_pane_selection))

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
