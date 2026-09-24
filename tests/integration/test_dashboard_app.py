from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import shikiru.agents.registry as registry_module
from shikiru.clock import SystemClock
from shikiru.config.loader import load_settings
from shikiru.dashboard.app import DashboardApp, WidgetPluginError
from shikiru.dashboard.contract import RepoStatus
from shikiru.store.reader import StateReader
from tests.support.config import write_minimal_project

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.store.database import Database
    from tests.support.store import Connection


class _StubServices:
    async def repo_status(self) -> RepoStatus:
        return RepoStatus(branch="main", ahead=0, behind=0, dirty=0)

    def engine_up(self) -> bool:
        return False


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


async def test_dashboard_mounts_the_default_widgets(
    database: Database, connection: Connection, tmp_path: Path
) -> None:
    del connection
    project_dir = write_minimal_project(tmp_path)
    reader = StateReader(database, SystemClock())
    settings = load_settings(project_dir)
    try:
        app = DashboardApp(
            state=reader,
            project_dir=project_dir,
            focus_task=None,
            services=_StubServices(),
            settings=settings,
            widget_names=settings.dashboard.widgets,
        )
        async with app.run_test():
            pass
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
            state=reader,
            project_dir=project_dir,
            focus_task=None,
            services=_StubServices(),
            settings=load_settings(project_dir),
            widget_names=["dup"],
        )
        with pytest.raises(WidgetPluginError, match="dist-one") as excinfo:
            async with app.run_test():
                pass
        assert "dist-two" in str(excinfo.value)
    finally:
        reader.close()
