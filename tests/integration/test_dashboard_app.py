from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from shikiru.clock import SystemClock
from shikiru.config.loader import load_settings
from shikiru.dashboard.app import DashboardApp, WidgetPluginError
from shikiru.domain.commands import Submit
from shikiru.store.commands import CommandQueue
from shikiru.store.reader import StateReader
from shikiru.store.tasks import TaskStore
from tests.support.clock import FakeClock
from tests.support.config import write_minimal_project

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from shikiru.dashboard.contract import RepoStatus
    from shikiru.store.database import Database
    from tests.support.store import Connection

START = datetime(2026, 1, 1, tzinfo=UTC)


class _StubServices:
    async def repo_status(self) -> Sequence[RepoStatus]:
        return []

    def engine_up(self) -> bool:
        return False


async def test_dashboard_app_composes_configured_widgets_in_order(
    database: Database, connection: Connection, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    queue = CommandQueue(connection, clock)
    queued = queue.append(Submit(task_id="", title="Add retry", request="Add retry"))
    assert isinstance(queued.command, Submit)
    TaskStore(connection, clock).insert_from_submit(queued.command)

    reader = StateReader(database, SystemClock())
    try:
        app = DashboardApp(
            state=reader,
            project_dir=project_dir,
            focus_task=None,
            services=_StubServices(),
            settings=load_settings(project_dir),
            widget_names=["header", "queue"],
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            mounted_types = [type(widget).__name__ for widget in app.screen.children]
            assert mounted_types == ["HeaderPanel", "QueuePanel"]
    finally:
        reader.close()


async def test_dashboard_app_rejects_an_unregistered_widget_name(
    database: Database, connection: Connection, tmp_path: Path
) -> None:
    del connection
    project_dir = write_minimal_project(tmp_path)
    reader = StateReader(database, SystemClock())
    try:
        app = DashboardApp(
            state=reader,
            project_dir=project_dir,
            focus_task=None,
            services=_StubServices(),
            settings=load_settings(project_dir),
            widget_names=["no-such-widget"],
        )
        with pytest.raises(WidgetPluginError):
            async with app.run_test():
                pass
    finally:
        reader.close()
