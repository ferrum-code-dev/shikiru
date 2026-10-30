from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from shikiru.domain.events import EventKind
from shikiru.plugins.widgets.header import HeaderPanel, HeaderWidget
from shikiru.store.events import EventLog
from tests.support.clock import FakeClock
from tests.support.config import MINIMAL_PROJECT_CONFIG, write_minimal_project
from tests.support.dashboard import StubServices, WidgetHostApp, widget_context
from tests.support.render import plain_text
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.store.reader import StateReader
    from tests.support.store import Connection

START = datetime(2026, 1, 1, tzinfo=UTC)
RELOAD_ALERT = "settings not reloaded"


def _plain(content: object) -> str:
    return plain_text(content)


def _write_max_parallel(project_dir: Path, value: str) -> None:
    (project_dir / ".shikiru" / "config.toml").write_text(
        MINIMAL_PROJECT_CONFIG + f"\n[orchestration]\nmax_parallel = {value}\n"
    )


def _start_engine(connection: Connection, clock: FakeClock) -> None:
    EventLog(connection, clock).append(EventKind.ENGINE_STARTED, message="engine started")


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
