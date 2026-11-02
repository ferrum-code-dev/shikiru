"""The Project diagnostics popup renders its permanent sections and every configured widget."""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, datetime
from html import unescape
from typing import TYPE_CHECKING

import pytest
from textual.containers import VerticalScroll
from textual.widgets import Button

from shikiru.config.loader import load_settings
from shikiru.dashboard.contract import DiagnosticSampleState, ResourceView
from shikiru.dashboard.diagnostics import (
    ENGINE_SECTION_ID,
    RESOURCES_SECTION_ID,
    ProjectDiagnosticsPanel,
)
from shikiru.domain.events import EventKind
from shikiru.paths import engine_log_path
from shikiru.process import ProcessIdentity
from shikiru.store.events import EventLog
from shikiru.ui.tokens import NEUTRAL_BORDER_COLOR, SAGE
from tests.support.cells import (
    ALERT_TEXT,
    AMBER_TEXT,
    BLANK,
    CYAN_BOLD,
    GREY,
    WHITE_BOLD,
    Cell,
    Piece,
    Style,
    group,
    line,
    rendered_lines,
)
from tests.support.clock import FakeClock
from tests.support.config import write_minimal_project
from tests.support.dashboard import (
    StubServices,
    WidgetHostApp,
    dashboard_app,
    state_views,
)
from tests.support.store import seed_task
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from textual.pilot import Pilot
    from textual.screen import Screen

    from shikiru.dashboard.app import DashboardApp
    from shikiru.dashboard.views import StateDashboardViews
    from shikiru.store.reader import StateReader
    from tests.support.store import Connection

pytestmark = pytest.mark.usefixtures("colour_terminal", "connection")

_START = datetime(2026, 1, 1, tzinfo=UTC)
_WIDE = (60, 30)
_POPUP = (100, 50)
_TINY = (20, 8)
_WAIT_SECONDS = 5
_REFRESH_SECONDS = 0.05
_GIB = 1024**3
_RULE_STYLE: Style = (False, NEUTRAL_BORDER_COLOR)
_GREEN: Style = (False, SAGE)
_AVAILABLE = DiagnosticSampleState.AVAILABLE
_ENGINE_UPTIME_SECONDS = 3 * 3600 + 12 * 60
_UPTIME_IDENTITY = ProcessIdentity(4321, 1_700_000_000.0)

_SAMPLE = ResourceView(
    host_cpu_pct=5.0,
    host_ram_used_pct=51.25,
    free_ram_gb=11.7,
    instance_cpu_pct=3.5,
    instance_rss_gb=1.6,
    sampled_at=_START,
    state=_AVAILABLE,
    sampled_ago="2s",
    host_ram_used_bytes=int(12.3 * _GIB),
    host_ram_total_bytes=24 * _GIB,
    shikiru_cpu_pct=1.2,
    shikiru_rss_gb=0.4,
    agents_cpu_pct=2.3,
    agents_rss_gb=1.2,
    shikiru_process_tree_empty=False,
    agents_process_tree_empty=False,
)


class _Services(StubServices):
    def __init__(
        self,
        sample: ResourceView | None,
        *,
        engine_running: bool = False,
        engine_process_identity: ProcessIdentity | None = None,
    ) -> None:
        super().__init__(
            engine_running=engine_running, engine_process_identity=engine_process_identity
        )
        self.resource_observation = sample
        self.samples_taken = 0

    async def resources(self) -> ResourceView:
        self.samples_taken += 1
        return await super().resources()


def _ink(cells: list[Cell]) -> list[Cell]:
    return [cell for cell in cells if cell[0] != BLANK]


def _text(cells: list[Cell]) -> str:
    return "".join(char for char, _ in cells)


def _styled(text: str, style: Style) -> list[Cell]:
    return [(char, style) for char in text]


def _name_row(name: str, *groups: tuple[Piece, ...]) -> list[Cell]:
    return _ink(_styled(name, WHITE_BOLD) + line(*groups))


def _grey_row(label: str, *groups: tuple[Piece, ...]) -> list[Cell]:
    return _ink(_styled(label, GREY) + line(*groups))


def _heading(title: str) -> list[Cell]:
    return _ink(_styled(title, GREY))


def _measure(word: str, value: str) -> tuple[Piece, ...]:
    return ((f"{word} ", GREY), (value, CYAN_BOLD))


_HOST = _name_row(
    "Host",
    group(*_measure("CPU", "5.0%")),
    group(("RAM ", GREY), ("12.3", CYAN_BOLD), (" / ", GREY), ("24.0 GiB", CYAN_BOLD)),
)
_SHIKIRU = _name_row("Shikiru", group(*_measure("CPU", "1.2%")), group(*_measure("RSS", "0.4 GiB")))
_AGENTS = _name_row("Agents", group(*_measure("CPU", "2.3%")), group(*_measure("RSS", "1.2 GiB")))
_RULE: list[Cell] = [("─", _RULE_STYLE)] * _WIDE[0]


def _resource_lines(*rows: list[Cell]) -> list[list[Cell]]:
    return [_heading("Resources"), *rows]


_STALE_REFRESH = _grey_row("Refresh", group(("failed · cpu probe failed", AMBER_TEXT)))
_RESOURCE_CASES = [
    pytest.param(
        _SAMPLE,
        _resource_lines(_HOST, _SHIKIRU, _AGENTS),
        id="available",
    ),
    pytest.param(
        replace(_SAMPLE, state=DiagnosticSampleState.STALE, error="cpu probe failed"),
        _resource_lines(_HOST, _SHIKIRU, _AGENTS, _STALE_REFRESH),
        id="stale-keeps-the-last-observation",
    ),
    pytest.param(
        replace(
            _SAMPLE,
            shikiru_cpu_pct=None,
            agents_cpu_pct=None,
            agents_process_tree_empty=True,
        ),
        _resource_lines(
            _HOST,
            _name_row("Shikiru", group(("CPU/RSS unavailable", GREY))),
            _name_row("Agents", group(("process tree is empty", GREY))),
        ),
        id="unavailable-and-empty-trees",
    ),
    pytest.param(
        replace(_SAMPLE, host_ram_used_bytes=None),
        _resource_lines(
            _name_row(
                "Host", group(*_measure("CPU", "5.0%")), group(("RAM used/total unavailable", GREY))
            ),
            _SHIKIRU,
            _AGENTS,
        ),
        id="host-memory-unavailable",
    ),
    pytest.param(
        replace(
            _SAMPLE, state=DiagnosticSampleState.FAILED, error="cpu probe failed", sampled_ago=None
        ),
        _resource_lines(_ink(_styled("failed · cpu probe failed", ALERT_TEXT))),
        id="failed",
    ),
    pytest.param(
        replace(_SAMPLE, state=DiagnosticSampleState.UNAVAILABLE, error="psutil missing"),
        _resource_lines(_grey_row("unavailable · psutil missing")),
        id="unavailable",
    ),
    pytest.param(
        None,
        _resource_lines(_grey_row("unavailable · no sample recorded")),
        id="no-sample",
    ),
]


def _panel(
    reader: StateReader,
    tmp_path: Path,
    services: StubServices,
    clock: FakeClock | None = None,
) -> ProjectDiagnosticsPanel:
    return ProjectDiagnosticsPanel(_views(reader, tmp_path, services, clock), reader.watch, ())


def _views(
    reader: StateReader,
    tmp_path: Path,
    services: StubServices,
    clock: FakeClock | None = None,
) -> StateDashboardViews:
    return state_views(
        reader, write_minimal_project(tmp_path), clock or FakeClock(_START), services
    )


def _section(panel: ProjectDiagnosticsPanel, section_id: str) -> list[list[Cell]]:
    return [
        _ink(cells) for cells in rendered_lines(panel.query_one(f"#{section_id}")) if _ink(cells)
    ]


def _text_of(panel: ProjectDiagnosticsPanel, section_id: str) -> str:
    return "\n".join(_text(cells) for cells in _section(panel, section_id))


@pytest.mark.parametrize(("sample", "expected"), _RESOURCE_CASES)
async def test_resources_section_shows_each_sample_state_in_the_approved_rows(
    reader: StateReader,
    tmp_path: Path,
    sample: ResourceView | None,
    expected: list[list[Cell]],
) -> None:
    panel = _panel(reader, tmp_path, _Services(sample))
    async with WidgetHostApp(panel).run_test(size=_WIDE):
        await wait_until(
            lambda: len(_section(panel, RESOURCES_SECTION_ID)) > 1, timeout=_WAIT_SECONDS
        )
        assert _section(panel, RESOURCES_SECTION_ID) == expected


def _engine_lines(status: list[Cell], faults: list[Cell]) -> list[list[Cell]]:
    return [_RULE, _heading("Engine"), status, faults]


_NONE_OPEN = _grey_row("Faults", group(("None open", GREY)))
_ENGINE_CASES = [
    pytest.param(
        False,
        _engine_lines(_grey_row("Status", group(("Not running", ALERT_TEXT))), _NONE_OPEN),
        id="not-running",
    ),
    pytest.param(
        True,
        _engine_lines(
            _grey_row("Status", group(("Running", _GREEN)), group(("uptime unavailable", GREY))),
            _NONE_OPEN,
        ),
        id="running-without-recorded-start",
    ),
]


@pytest.mark.parametrize(("engine_running", "expected"), _ENGINE_CASES)
async def test_engine_section_shows_the_lock_state(
    reader: StateReader,
    tmp_path: Path,
    engine_running: bool,
    expected: list[list[Cell]],
) -> None:
    panel = _panel(reader, tmp_path, _Services(_SAMPLE, engine_running=engine_running))
    async with WidgetHostApp(panel).run_test(size=_WIDE):
        await wait_until(lambda: len(_section(panel, ENGINE_SECTION_ID)) > 1, timeout=_WAIT_SECONDS)
        assert _section(panel, ENGINE_SECTION_ID) == expected


def _record_fault(
    connection: Connection, clock: FakeClock, task_id: str | None, **fields: object
) -> None:
    EventLog(connection, clock).append(
        EventKind.ENGINE_FAULT,
        task_id=task_id,
        message="fault",
        data={"scope": "merge", "error": "timeout", "recoverable": True, "repeat": 3, **fields},
    )


async def test_engine_section_shows_the_recorded_uptime_and_the_open_fault_count(
    reader: StateReader, tmp_path: Path, connection: Connection
) -> None:
    clock = FakeClock(_START)
    EventLog(connection, clock).append(
        EventKind.ENGINE_STARTED,
        message="engine started",
        data={
            "engine_pid": _UPTIME_IDENTITY.pid,
            "engine_process_started_at": _UPTIME_IDENTITY.started_at,
        },
    )
    task = seed_task(connection, clock, title="Faulty")
    _record_fault(connection, clock, task.id)
    _record_fault(connection, clock, None, scope="resource sample")
    clock.advance(_ENGINE_UPTIME_SECONDS)
    services = _Services(_SAMPLE, engine_running=True, engine_process_identity=_UPTIME_IDENTITY)
    panel = _panel(reader, tmp_path, services, clock)
    async with WidgetHostApp(panel).run_test(size=_WIDE):
        await wait_until(lambda: len(_section(panel, ENGINE_SECTION_ID)) > 1, timeout=_WAIT_SECONDS)
        assert _section(panel, ENGINE_SECTION_ID) == _engine_lines(
            _grey_row("Status", group(("Running", _GREEN)), group(("up 3h 12m", GREY))),
            _grey_row("Faults", group(("2 open", AMBER_TEXT))),
        )


async def test_values_follow_the_resources_bar_refresh_without_any_commit_or_new_sample(
    reader: StateReader, tmp_path: Path
) -> None:
    services = _Services(_SAMPLE)
    views = _views(reader, tmp_path, services)
    panel = ProjectDiagnosticsPanel(views, reader.watch, ())
    async with WidgetHostApp(panel).run_test(size=_WIDE) as pilot:
        await wait_until(
            lambda: "5.0%" in _text_of(panel, RESOURCES_SECTION_ID), timeout=_WAIT_SECONDS
        )
        services.resource_observation = replace(_SAMPLE, host_cpu_pct=42.0, sampled_ago="1m")
        services.engine_running = True
        await pilot.pause()
        assert "5.0%" in _text_of(panel, RESOURCES_SECTION_ID)
        assert services.samples_taken == 0

        await views.resources()
        await pilot.pause()

        assert "42%" in _text_of(panel, RESOURCES_SECTION_ID)
        assert "Running" in _text_of(panel, ENGINE_SECTION_ID)
        assert services.samples_taken == 1


_REFRESH_OUTCOMES = [
    pytest.param(_SAMPLE, id="sampled-or-fresh-cache"),
    pytest.param(
        replace(_SAMPLE, state=DiagnosticSampleState.STALE, error="cpu probe failed"), id="stale"
    ),
    pytest.param(
        replace(_SAMPLE, state=DiagnosticSampleState.FAILED, error="cpu probe failed"), id="failed"
    ),
]


@pytest.mark.parametrize("outcome", _REFRESH_OUTCOMES)
async def test_resource_subscribers_hear_every_refresh_until_they_unsubscribe(
    reader: StateReader, tmp_path: Path, outcome: ResourceView
) -> None:
    services = _Services(outcome)
    views = _views(reader, tmp_path, services)
    heard: list[str] = []
    unsubscribe = views.on_resources_refreshed(lambda: heard.append("refreshed"))

    await views.resources()
    await views.resources()
    unsubscribe()
    await views.resources()

    assert heard == ["refreshed", "refreshed"]


async def _open_popup(app: DashboardApp, pilot: Pilot[None]) -> None:
    await wait_until(lambda: bool(app.query("#resources")), timeout=_WAIT_SECONDS)
    assert await pilot.click("#resources")
    await wait_until(lambda: bool(app.screen.query("#project-diagnostics")), timeout=_WAIT_SECONDS)


def _exported_text(app: DashboardApp) -> str:
    svg = app.export_screenshot()
    elements = re.findall(r"<text\b[^>]*>(.*?)</text>", svg, flags=re.DOTALL)
    return " ".join(" ".join(unescape(item).replace("\xa0", " ") for item in elements).split())


@pytest.mark.parametrize(
    ("widgets", "expected_ids"),
    [
        pytest.param(["disk", "docker"], ["disk", "docker"], id="disk-then-docker"),
        pytest.param(["docker", "disk"], ["docker", "disk"], id="docker-then-disk"),
        pytest.param(["disk"], ["disk"], id="docker-disabled-disappears"),
        pytest.param([], [], id="none-configured"),
    ],
)
async def test_every_configured_diagnostics_widget_is_rendered_in_configured_order(
    reader: StateReader,
    tmp_path: Path,
    widgets: list[str],
    expected_ids: list[str],
) -> None:
    app = dashboard_app(
        reader,
        write_minimal_project(tmp_path),
        FakeClock(_START),
        widget_names=["queue", "resources", *widgets],
        services=_Services(_SAMPLE),
    )
    async with app.run_test(size=_POPUP) as pilot:
        await _open_popup(app, pilot)
        panel = app.screen.query_one("#project-diagnostics")
        assert [child.id for child in panel.children] == [
            RESOURCES_SECTION_ID,
            ENGINE_SECTION_ID,
            *expected_ids,
        ]
        await pilot.pause()
        screen_text = _exported_text(app)
        assert ("Docker" in screen_text) is ("docker" in expected_ids)
        assert "not configured" not in screen_text


async def test_only_the_first_section_of_the_popup_goes_without_a_rule(
    reader: StateReader, tmp_path: Path
) -> None:
    app = dashboard_app(
        reader,
        write_minimal_project(tmp_path),
        FakeClock(_START),
        widget_names=["queue", "resources", "disk"],
        services=_Services(_SAMPLE),
    )
    async with app.run_test(size=_POPUP) as pilot:
        await _open_popup(app, pilot)
        screen = app.screen
        await wait_until(lambda: len(_section_lines(screen, "disk")) > 1, timeout=_WAIT_SECONDS)
        content = screen.query_one("#context-widget-content")
        assert screen.query_one(f"#{RESOURCES_SECTION_ID}").region.y == content.region.y
        first_lines = {
            section_id: _section_lines(screen, section_id)[0]
            for section_id in (RESOURCES_SECTION_ID, ENGINE_SECTION_ID, "disk")
        }
        assert first_lines[RESOURCES_SECTION_ID] == "Resources"
        assert set(first_lines[ENGINE_SECTION_ID]) == set(first_lines["disk"]) == {"─"}


def _section_lines(screen: Screen[object], section_id: str) -> list[str]:
    widget = screen.query_one(f"#{section_id}")
    return [_text(_ink(row)) for row in rendered_lines(widget) if _ink(row)]


async def test_the_faults_section_takes_no_room_until_a_fault_is_open(
    reader: StateReader, tmp_path: Path, connection: Connection
) -> None:
    clock = FakeClock(_START)
    task = seed_task(connection, clock, title="Faulty")
    project_dir = write_minimal_project(tmp_path)
    settings = load_settings(
        project_dir, [f"orchestration.resources.sample_seconds={_REFRESH_SECONDS}"]
    )
    app = dashboard_app(
        reader,
        project_dir,
        clock,
        widget_names=["queue", "resources", "faults"],
        services=_Services(_SAMPLE),
        settings=settings,
    )
    async with app.run_test(size=_POPUP) as pilot:
        await _open_popup(app, pilot)
        faults = app.screen.query_one("#faults")
        await pilot.pause()
        assert faults.region.height == 0
        assert "Faults None open" in " ".join(_text_of_engine(app).split())
        _record_fault(connection, clock, task.id)
        await wait_until(lambda: faults.region.height > 0, timeout=_WAIT_SECONDS)
        cells = [_ink(row) for row in rendered_lines(faults) if _ink(row)]
        texts = [_text(row) for row in cells]
        assert texts[1] == "Faults"
        assert texts[2] == f"{task.id}·merge·timeout·recoverable·\N{MULTIPLICATION SIGN}3"
        assert texts[3].startswith("first")
        assert "latest" in "".join(texts[3:])
        headline = cells[2]
        assert all(style == WHITE_BOLD for _, style in headline[: len(task.id)])
        assert all(style == CYAN_BOLD for _, style in headline[-2:])
        assert "Faults 1 open" in " ".join(_text_of_engine(app).split())


def _text_of_engine(app: DashboardApp) -> str:
    engine = app.screen.query_one(f"#{ENGINE_SECTION_ID}")
    return "\n".join(_text(row) for row in rendered_lines(engine))


async def test_the_popup_is_usable_at_the_smallest_terminal_size(
    reader: StateReader, tmp_path: Path
) -> None:
    app = dashboard_app(
        reader,
        write_minimal_project(tmp_path),
        FakeClock(_START),
        widget_names=["queue", "resources", "disk"],
        services=_Services(_SAMPLE),
    )
    async with app.run_test(size=_TINY) as pilot:
        await _open_popup(app, pilot)
        content = app.screen.query_one("#context-widget-content", VerticalScroll)
        await pilot.pause()
        assert content.max_scroll_y > 0
        seen: list[str] = []
        for offset in range(int(content.max_scroll_y) + 1):
            content.scroll_to(y=offset, animate=False, immediate=True)
            await pilot.pause()
            seen.append(_exported_text(app))
        reached = " ".join(seen)
        for fact in ("Resources", "Host", "Engine", "Not", "running", "None open", "Disk"):
            assert fact in reached
        sections = app.screen.query("#project-diagnostics > *")
        assert all(
            len(_text(row)) <= section.region.width
            for section in sections
            for row in rendered_lines(section)
        )


async def test_opening_faults_without_the_faults_widget_says_so_and_falls_back_to_command_history(
    reader: StateReader, tmp_path: Path, connection: Connection
) -> None:
    clock = FakeClock(_START)
    _record_fault(connection, clock, None)
    app = dashboard_app(
        reader,
        write_minimal_project(tmp_path),
        clock,
        widget_names=["header", "queue", "escalations", "resources"],
        services=_Services(_SAMPLE),
    )
    async with app.run_test(size=_POPUP) as pilot:
        home = app.screen
        await wait_until(lambda: bool(app.query("#escalations")), timeout=_WAIT_SECONDS)
        assert await pilot.click("#escalations")
        status = home.query_one("#workspace-command-status", Button)
        await wait_until(
            lambda: app.screen is not home and str(status.label).startswith("Faults not"),
            timeout=_WAIT_SECONDS,
        )


def _unbroken(text: str) -> str:
    return "".join(text.split())


def _faults_lines(app: DashboardApp) -> list[str]:
    rows = rendered_lines(app.screen.query_one("#faults"))
    return [_text(row) for row in rows if _ink(row)]


@pytest.mark.parametrize(
    ("scope", "recoverable", "resolution"),
    [
        ("base push main", True, "Shikiru retries it automatically"),
        ("merge", False, "fix the code named in the traceback"),
        ("remote check origin", True, "could not read the repository's remote configuration"),
    ],
)
async def test_the_faults_panel_says_how_each_fault_resolves_and_where_its_evidence_is(
    reader: StateReader,
    tmp_path: Path,
    connection: Connection,
    *,
    scope: str,
    recoverable: bool,
    resolution: str,
) -> None:
    clock = FakeClock(_START)
    project_dir = write_minimal_project(tmp_path)
    _record_fault(
        connection,
        clock,
        None,
        scope=scope,
        error="CommandFailed",
        recoverable=recoverable,
        repeat=1276,
    )
    app = dashboard_app(
        reader,
        project_dir,
        clock,
        widget_names=["queue", "resources", "faults"],
        services=_Services(_SAMPLE),
    )
    async with app.run_test(size=_POPUP) as pilot:
        await _open_popup(app, pilot)
        faults = app.screen.query_one("#faults")
        await wait_until(lambda: faults.region.height > 0, timeout=_WAIT_SECONDS)
        flat = " ".join(" ".join(_faults_lines(app)).split())
        assert resolution in flat
        assert _unbroken(str(engine_log_path(project_dir))) in _unbroken(
            " ".join(_faults_lines(app))
        )


async def test_a_base_push_fault_names_the_base_and_keeps_the_push_from_being_forced(
    reader: StateReader, tmp_path: Path, connection: Connection
) -> None:
    clock = FakeClock(_START)
    _record_fault(
        connection,
        clock,
        None,
        scope="base push main",
        error="CommandFailed",
        recoverable=True,
        repeat=1276,
    )
    app = dashboard_app(
        reader,
        write_minimal_project(tmp_path),
        clock,
        widget_names=["queue", "resources", "faults"],
        services=_Services(_SAMPLE),
    )
    async with app.run_test(size=_POPUP) as pilot:
        await _open_popup(app, pilot)
        faults = app.screen.query_one("#faults")
        await wait_until(lambda: faults.region.height > 0, timeout=_WAIT_SECONDS)
        flat = " ".join(" ".join(_faults_lines(app)).split())
        assert "Git could not push main to the remote" in flat
        assert "Pushes never force" in flat
        assert "pending work stays unpushed" in flat
