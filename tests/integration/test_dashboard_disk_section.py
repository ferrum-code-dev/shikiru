"""The Disk section renders free and total space, the run size and every sampling state."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from shikiru.dashboard.contract import DiagnosticSampleState, DiskView
from shikiru.domain.levels import UsageLevel
from shikiru.plugins.widgets.disk import DiskPanel
from shikiru.ui.tokens import ALERT_COLOR, AMBER
from tests.support.cells import (
    ALERT_TEXT,
    AMBER_TEXT,
    BLANK,
    CYAN_BOLD,
    GREY,
    Cell,
    Piece,
    Style,
    group,
    line,
    rendered_lines,
)
from tests.support.clock import FakeClock
from tests.support.config import write_minimal_project
from tests.support.dashboard import StubServices, WidgetHostApp, widget_context
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.store.reader import StateReader

pytestmark = pytest.mark.usefixtures("colour_terminal", "connection")

_START = datetime(2026, 1, 1, tzinfo=UTC)
_WIDE = (60, 20)
_COMPACT = (30, 20)
_TINY = (20, 8)
_WAIT_SECONDS = 5
_REFRESH_SECONDS = 0.05
_SLOW_SECONDS = 3600
_GIB = 1024**3
_TOTAL_BYTES = 500 * _GIB
_SHIKIRU_BYTES = int(1.2 * _GIB)
_AMBER_BOLD: Style = (True, AMBER)
_ALERT_BOLD: Style = (True, ALERT_COLOR)
_MARKER = "\N{DOUBLE EXCLAMATION MARK}"


def _view(
    free_bytes: int,
    state: DiagnosticSampleState = DiagnosticSampleState.AVAILABLE,
    *,
    error: str | None = None,
) -> DiskView:
    return DiskView(
        state=state,
        free_bytes=free_bytes,
        total_bytes=_TOTAL_BYTES,
        shikiru_bytes=_SHIKIRU_BYTES,
        sampled_ago="3s",
        error=error,
    )


class _Services(StubServices):
    def __init__(self, view: DiskView) -> None:
        super().__init__()
        self.view = view

    async def disk(self) -> DiskView:
        return self.view


def _ink(cells: list[Cell]) -> list[Cell]:
    return [cell for cell in cells if cell[0] != BLANK]


def _row(label: str, *groups: tuple[Piece, ...]) -> list[Cell]:
    return _ink([(char, GREY) for char in label] + line(*groups))


def _heading(trailing: Piece | None = None) -> list[Cell]:
    cells = [(char, GREY) for char in "Disk"]
    if trailing is not None:
        cells += [(char, trailing[1]) for char in trailing[0]]
    return _ink(cells)


def _free_row(free: str, free_style: Style, *, marker: bool = False) -> list[Cell]:
    pieces = [(free, free_style), (" of ", GREY), ("500.0 GiB", CYAN_BOLD)]
    if marker:
        pieces.append((f" {_MARKER}", free_style))
    return _row("Free", group(*pieces))


_SHIKIRU_ROW = _row(".shikiru", group(("1.2 GiB", CYAN_BOLD)))
_MEASURING_ROW = _row(".shikiru", group(("measuring…", GREY)))
_REFRESH_ROW = _row("Refresh", group(("failed · project vanished", AMBER_TEXT)))

_AVAILABLE_LINES = [_heading(), _free_row("120.0 GiB", CYAN_BOLD), _SHIKIRU_ROW]
_STALE_LINES = [
    _heading(("stale · observed 3s ago", AMBER_TEXT)),
    _free_row("120.0 GiB", CYAN_BOLD),
    _SHIKIRU_ROW,
    _REFRESH_ROW,
]
_UNAVAILABLE_LINES = [_heading(), _row("unavailable · project is missing")]
_FAILED_LINES = [
    _heading(),
    _ink([(char, ALERT_TEXT) for char in "failed · project is unreadable"]),
]

_AVAILABLE = DiagnosticSampleState.AVAILABLE
_WIDE_CASES = [
    pytest.param(_view(120 * _GIB), _AVAILABLE_LINES, id="available"),
    pytest.param(
        replace(_view(120 * _GIB), shikiru_bytes=None),
        [_heading(), _free_row("120.0 GiB", CYAN_BOLD), _MEASURING_ROW],
        id="size-not-yet-measured",
    ),
    pytest.param(
        _view(120 * _GIB, DiagnosticSampleState.STALE, error="project vanished"),
        _STALE_LINES,
        id="stale-keeps-last-observation",
    ),
    pytest.param(
        DiskView(
            state=DiagnosticSampleState.UNAVAILABLE,
            error="project is missing",
        ),
        _UNAVAILABLE_LINES,
        id="unavailable",
    ),
    pytest.param(
        DiskView(state=DiagnosticSampleState.FAILED, error="project is unreadable"),
        _FAILED_LINES,
        id="failed",
    ),
]


@pytest.mark.parametrize(
    ("free_gib", "level", "expected"),
    [
        pytest.param(120, None, _free_row("120.0 GiB", CYAN_BOLD), id="none"),
        pytest.param(
            45, UsageLevel.WARN, _free_row("45.0 GiB", _AMBER_BOLD, marker=True), id="warn"
        ),
        pytest.param(
            20, UsageLevel.ALERT, _free_row("20.0 GiB", _ALERT_BOLD, marker=True), id="alert"
        ),
    ],
)
async def test_low_free_space_ends_with_a_marker_in_the_level_colour(
    reader: StateReader,
    tmp_path: Path,
    free_gib: int,
    level: UsageLevel | None,
    expected: list[Cell],
) -> None:
    view = replace(_view(free_gib * _GIB), free_level=level)
    panel = _panel(reader, tmp_path, view)
    async with WidgetHostApp(panel).run_test(size=_WIDE):
        assert (await _settled(panel))[1] == expected


def _panel(reader: StateReader, tmp_path: Path, view: DiskView, **context: float) -> DiskPanel:
    base = widget_context(
        reader, write_minimal_project(tmp_path), FakeClock(_START), services=_Services(view)
    )
    return DiskPanel(replace(base, **context))


async def _settled(panel: DiskPanel) -> list[list[Cell]]:
    await wait_until(lambda: any(rendered_lines(panel)), timeout=_WAIT_SECONDS)
    return [_ink(cells) for cells in rendered_lines(panel) if _ink(cells)]


def _plain(panel: DiskPanel) -> list[str]:
    return ["".join(char for char, _ in cells) for cells in rendered_lines(panel)]


@pytest.mark.parametrize(("view", "expected"), _WIDE_CASES)
async def test_each_state_renders_the_approved_rows_and_styles(
    reader: StateReader, tmp_path: Path, view: DiskView, expected: list[list[Cell]]
) -> None:
    panel = _panel(reader, tmp_path, view)
    async with WidgetHostApp(panel).run_test(size=_WIDE):
        assert await _settled(panel) == expected


async def test_a_new_sample_replaces_the_rendering_at_the_refresh_interval(
    reader: StateReader, tmp_path: Path
) -> None:
    services = _Services(_view(120 * _GIB))
    base = widget_context(
        reader, write_minimal_project(tmp_path), FakeClock(_START), services=services
    )
    panel = DiskPanel(replace(base, disk_refresh_seconds=_REFRESH_SECONDS))
    async with WidgetHostApp(panel).run_test(size=_WIDE):
        await wait_until(lambda: "120.0 GiB" in "\n".join(_plain(panel)), timeout=_WAIT_SECONDS)
        services.view = _view(45 * _GIB)
        await wait_until(lambda: "45.0 GiB" in "\n".join(_plain(panel)), timeout=_WAIT_SECONDS)


async def test_an_unmeasured_size_is_re_read_on_the_short_cadence_not_the_disk_one(
    reader: StateReader, tmp_path: Path
) -> None:
    services = _Services(replace(_view(120 * _GIB), shikiru_bytes=None))
    base = widget_context(
        reader, write_minimal_project(tmp_path), FakeClock(_START), services=services
    )
    panel = DiskPanel(
        replace(
            base, disk_refresh_seconds=_SLOW_SECONDS, resources_refresh_seconds=_REFRESH_SECONDS
        )
    )
    async with WidgetHostApp(panel).run_test(size=_WIDE):
        await wait_until(lambda: "measuring" in "\n".join(_plain(panel)), timeout=_WAIT_SECONDS)
        services.view = _view(120 * _GIB)
        await wait_until(lambda: "1.2 GiB" in "\n".join(_plain(panel)), timeout=_WAIT_SECONDS)


_COMPACT_LAYOUT = [
    "Disk",
    "Free",
    "120.0 GiB of 500.0 GiB",
    ".shikiru",
    "1.2 GiB",
]
_TINY_LAYOUT = [
    "Disk",
    "Free",
    "120.0 GiB of 500.0",
    "GiB",
    ".shikiru",
    "1.2 GiB",
]


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        pytest.param(_COMPACT, _COMPACT_LAYOUT, id="compact"),
        pytest.param(_TINY, _TINY_LAYOUT, id="20x8"),
    ],
)
async def test_narrow_widths_stack_the_rows_and_keep_every_figure(
    reader: StateReader, tmp_path: Path, size: tuple[int, int], expected: list[str]
) -> None:
    panel = _panel(reader, tmp_path, _view(120 * _GIB))
    async with WidgetHostApp(panel).run_test(size=size):
        await _settled(panel)
        assert [text.rstrip() for text in _plain(panel)][: len(expected)] == expected
