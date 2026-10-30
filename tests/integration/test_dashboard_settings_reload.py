from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

import pytest

from shikiru.agents.contracts import QuotaWindow
from shikiru.config.loader import load_settings
from shikiru.dashboard.contract import (
    DiagnosticSampleState,
    DockerSummaryView,
    DockerView,
    ElsewhereView,
)
from shikiru.dashboard.live_settings import LiveSettings
from shikiru.domain.events import EventKind
from shikiru.plugins.widgets.docker import DockerPanel, DockerWidget
from shikiru.plugins.widgets.header import HeaderPanel, HeaderWidget
from shikiru.plugins.widgets.usage import UsagePanel
from shikiru.store.events import EventLog
from shikiru.store.quotas import QuotaStore
from shikiru.store.retention import StateRetention
from shikiru.ui.tokens import AMBER, SAGE
from tests.support.cells import foregrounds, rendered_lines
from tests.support.clock import FakeClock
from tests.support.config import MINIMAL_PROJECT_CONFIG, write_minimal_project
from tests.support.dashboard import StubServices, WidgetHostApp, widget_context
from tests.support.render import plain_text
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from textual.widget import Widget

    from shikiru.config.settings import Settings
    from shikiru.domain.events import Event
    from shikiru.store.reader import StateReader
    from tests.support.store import Connection

START = datetime(2026, 1, 1, tzinfo=UTC)
RELOAD_ALERT = "settings not reloaded"
QUOTA_AGENT = "claude"
QUOTA_WINDOW = "5h"
QUOTA_USED_PCT = 40.0
GAUGE_TEXT = "40%"
QUOTA_WARN_CONFIG = "\n[dashboard.gauges]\nquota_warn_pct = 30\n"
PRUNE_AFTER_SECONDS = 3600
DOCKER_TOTAL = 5
DOCKER_RUNNING_BEFORE = 1
DOCKER_RUNNING_AFTER = 4
DOCKER_LONG_REFRESH_CONFIG = "\n[dashboard]\ndocker_refresh_seconds = 3600\n"
DOCKER_SHORT_REFRESH_CONFIG = "\n[dashboard]\ndocker_refresh_seconds = 0.05\n"


class _DockerServices(StubServices):
    def __init__(self) -> None:
        super().__init__()
        self.running = DOCKER_RUNNING_BEFORE

    async def containers(self) -> DockerView:
        summary = DockerSummaryView(
            running=self.running,
            total=DOCKER_TOTAL,
            cpu_pct=None,
            memory_bytes=None,
            problems=(),
            top=(),
            elsewhere=ElsewhereView(0, None, None),
            usage_available=True,
        )
        return DockerView(
            available=True,
            summary=summary,
            state=DiagnosticSampleState.AVAILABLE,
            sampled_ago="0s",
        )


def _plain(content: object) -> str:
    return plain_text(content)


def _painted(widget: Widget) -> str:
    return "\n".join("".join(char for char, _ in cells) for cells in rendered_lines(widget))


def _write_config(project_dir: Path, extra: str) -> None:
    (project_dir / ".shikiru" / "config.toml").write_text(MINIMAL_PROJECT_CONFIG + extra)


def _write_max_parallel(project_dir: Path, value: str) -> None:
    _write_config(project_dir, f"\n[orchestration]\nmax_parallel = {value}\n")


def _start_engine(connection: Connection, clock: FakeClock) -> Event:
    return EventLog(connection, clock).append(EventKind.ENGINE_STARTED, message="engine started")


def _record_quota(connection: Connection) -> None:
    window = QuotaWindow(
        QUOTA_WINDOW, QUOTA_USED_PCT, START + timedelta(hours=2), START - timedelta(minutes=2)
    )
    QuotaStore(connection).record(QUOTA_AGENT, window)


def _load_starting_an_engine_on_the_first_call(
    project_dir: Path, connection: Connection, clock: FakeClock
) -> Callable[[], Settings]:
    calls = 0

    def load() -> Settings:
        nonlocal calls
        calls += 1
        settings = load_settings(project_dir)
        if calls == 1:
            _write_max_parallel(project_dir, "2")
            _start_engine(connection, clock)
        return settings

    return load


async def test_header_applies_edited_max_parallel_when_the_engine_starts_again(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    _write_max_parallel(project_dir, "1")
    clock = FakeClock(START)
    context = widget_context(reader, project_dir, clock, services=StubServices(engine_running=True))
    widget = cast("HeaderPanel", HeaderWidget().create(context))
    async with WidgetHostApp(widget).run_test() as pilot:
        await pilot.pause()
        assert "Slots 0/1" in _plain(widget.content)

        _write_max_parallel(project_dir, "2")
        _start_engine(connection, clock)

        await wait_until(lambda: "Slots 0/2" in _plain(widget.content), timeout=5.0)


async def test_header_keeps_applied_settings_when_a_reload_is_invalid(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    _write_max_parallel(project_dir, "1")
    clock = FakeClock(START)
    context = widget_context(reader, project_dir, clock, services=StubServices(engine_running=True))
    widget = cast("HeaderPanel", HeaderWidget().create(context))
    async with WidgetHostApp(widget).run_test() as pilot:
        await pilot.pause()

        _write_max_parallel(project_dir, '"two"')
        _start_engine(connection, clock)

        await wait_until(lambda: RELOAD_ALERT in _plain(widget.content), timeout=5.0)
        assert "Slots 0/1" in _plain(widget.content)

        _write_max_parallel(project_dir, "2")
        _start_engine(connection, clock)

        await wait_until(lambda: "Slots 0/2" in _plain(widget.content), timeout=5.0)
        assert RELOAD_ALERT not in _plain(widget.content)


@pytest.mark.usefixtures("colour_terminal")
async def test_usage_gauges_apply_edited_thresholds_when_the_engine_starts_again(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    _record_quota(connection)
    context = replace(
        widget_context(reader, project_dir, clock),
        settings=LiveSettings(reader, lambda: load_settings(project_dir)),
    )
    panel = UsagePanel(context)
    async with WidgetHostApp(panel).run_test():
        await wait_until(lambda: GAUGE_TEXT in _painted(panel), timeout=5.0)
        assert set(foregrounds(panel, GAUGE_TEXT)) == {SAGE}

        _write_config(project_dir, QUOTA_WARN_CONFIG)
        _start_engine(connection, clock)

        await wait_until(lambda: set(foregrounds(panel, GAUGE_TEXT)) == {AMBER}, timeout=5.0)


async def test_docker_refresh_applies_an_edited_cadence_when_the_engine_starts_again(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    _write_config(project_dir, DOCKER_LONG_REFRESH_CONFIG)
    clock = FakeClock(START)
    services = _DockerServices()
    context = widget_context(reader, project_dir, clock, services=services)
    panel = cast("DockerPanel", DockerWidget().create(context))
    async with WidgetHostApp(panel).run_test():
        await wait_until(
            lambda: f"{DOCKER_RUNNING_BEFORE} of {DOCKER_TOTAL}" in _plain(panel.content),
            timeout=5.0,
        )

        services.running = DOCKER_RUNNING_AFTER
        _write_config(project_dir, DOCKER_SHORT_REFRESH_CONFIG)
        _start_engine(connection, clock)

        await wait_until(
            lambda: f"{DOCKER_RUNNING_AFTER} of {DOCKER_TOTAL}" in _plain(panel.content),
            timeout=5.0,
        )


def test_a_start_during_the_first_load_is_applied_on_the_next_read(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    _write_max_parallel(project_dir, "1")
    clock = FakeClock(START)
    settings = LiveSettings(
        reader, _load_starting_an_engine_on_the_first_call(project_dir, connection, clock)
    )
    assert settings.current().orchestration.max_parallel == 2


def test_pruning_the_applied_start_keeps_the_applied_settings_until_a_later_start(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    _write_max_parallel(project_dir, "1")
    clock = FakeClock(START)
    applied = _start_engine(connection, clock)
    settings = LiveSettings(reader, lambda: load_settings(project_dir))
    assert settings.current().orchestration.max_parallel == 1

    _write_max_parallel(project_dir, "2")
    EventLog(connection, clock).append(EventKind.ENGINE_STOPPED, message="engine stopped")
    clock.advance(PRUNE_AFTER_SECONDS)
    pruned = StateRetention(connection).delete_engine_events(
        clock.now(), delivered_up_to=applied.id
    )
    assert pruned == 1
    assert settings.current().orchestration.max_parallel == 1

    _start_engine(connection, clock)
    assert settings.current().orchestration.max_parallel == 2
