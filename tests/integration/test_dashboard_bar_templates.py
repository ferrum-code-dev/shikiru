"""Home token and resource bars render their operator templates for each recorded state."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from textual.color import Color
from textual.geometry import Region

from shikiru.agents.contracts import (
    BILLING_MODE_API,
    BILLING_MODE_SUBSCRIPTION,
    BILLING_SOURCE_OPERATOR_CONFIG,
    COST_BASIS_API_EQUIVALENT,
    COST_BASIS_BILLED,
    COST_BASIS_UNKNOWN,
    UNKNOWN_BILLING_DECLARATION,
    BillingDeclaration,
    Rung,
    Usage,
)
from shikiru.dashboard.contract import DiagnosticSampleState, ResourceView, WidgetPlacement
from shikiru.domain.tasks import Role
from shikiru.plugins.widgets.resources import ResourcesWidget
from shikiru.plugins.widgets.usage import UsageWidget
from shikiru.store.runs import RunStore
from shikiru.ui.tokens import (
    ALERT_COLOR,
    AMBER,
    PRIMARY_TEXT_COLOR,
    SAGE,
    SECONDARY_TEXT_COLOR,
    STEEL_BLUE,
)
from tests.support.clock import FakeClock
from tests.support.config import write_minimal_project
from tests.support.dashboard import StubServices, WidgetHostApp, widget_context
from tests.support.store import seed_task
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from rich.style import Style as RichStyle
    from textual.widget import Widget

    from shikiru.agents.contracts import CostBasis
    from shikiru.dashboard.contract import WidgetContext
    from shikiru.store.reader import StateReader
    from tests.support.store import Connection

Style = tuple[bool, str | None]
Piece = tuple[str, Style]
Cell = tuple[str, Style]

_START = datetime(2026, 1, 1, tzinfo=UTC)
_WIDE = (120, 40)
_NARROW = (48, 20)
_WAIT_SECONDS = 5
_GIB = 1024**3
_INPUT_TOKENS = 1000
_OUTPUT_TOKENS = 500
_BILLED_USD = 4.28
_ESTIMATE_USD = 0.11
_RUNG = Rung("codex", "gpt-6-luna", "low")
_API = BillingDeclaration(BILLING_MODE_API, BILLING_SOURCE_OPERATOR_CONFIG)
_SUBSCRIPTION = BillingDeclaration(BILLING_MODE_SUBSCRIPTION, BILLING_SOURCE_OPERATOR_CONFIG)

_WHITE_BOLD: Style = (True, PRIMARY_TEXT_COLOR)
_WHITE: Style = (False, PRIMARY_TEXT_COLOR)
_CYAN_BOLD: Style = (True, STEEL_BLUE)
_CYAN: Style = (False, STEEL_BLUE)
_GREEN_BOLD: Style = (True, SAGE)
_GREY: Style = (False, SECONDARY_TEXT_COLOR)
_AMBER_TEXT: Style = (False, AMBER)
_ALERT_TEXT: Style = (False, ALERT_COLOR)
_SEPARATOR: Piece = (" · ", _GREY)


def _record_run(
    connection: Connection,
    clock: FakeClock,
    declaration: BillingDeclaration,
    usage: Usage | None = None,
) -> None:
    task = seed_task(connection, clock, title="Recorded usage")
    runs = RunStore(connection, clock)
    run = runs.start(task.id, Role.IMPLEMENTER, _RUNG, billing_declaration=declaration)
    if usage is not None:
        runs.record_usage(run.id, usage)


def _tokens(cost_usd: float | None, cost_basis: CostBasis) -> Usage:
    return Usage(_INPUT_TOKENS, _OUTPUT_TOKENS, 0, 0, cost_usd, cost_basis, None)


def _seed_billed(connection: Connection, clock: FakeClock) -> None:
    _record_run(connection, clock, _API, _tokens(_BILLED_USD, COST_BASIS_BILLED))


def _seed_zero_billed(connection: Connection, clock: FakeClock) -> None:
    _record_run(connection, clock, _API, _tokens(0.0, COST_BASIS_BILLED))


def _seed_estimate_only(connection: Connection, clock: FakeClock) -> None:
    _record_run(connection, clock, _API, _tokens(_ESTIMATE_USD, COST_BASIS_API_EQUIVALENT))


def _seed_subscription_only(connection: Connection, clock: FakeClock) -> None:
    _record_run(connection, clock, _SUBSCRIPTION, _tokens(None, COST_BASIS_UNKNOWN))


def _seed_unknown_tokens(connection: Connection, clock: FakeClock) -> None:
    _record_run(connection, clock, _API)


def _seed_partial_coverage(connection: Connection, clock: FakeClock) -> None:
    _seed_billed(connection, clock)
    _seed_unknown_tokens(connection, clock)
    _record_run(connection, clock, UNKNOWN_BILLING_DECLARATION, _tokens(None, COST_BASIS_UNKNOWN))


def _seed_nothing(connection: Connection, clock: FakeClock) -> None:
    del connection, clock


def _group(*pieces: Piece) -> tuple[Piece, ...]:
    return pieces


def _line(*groups: tuple[Piece, ...]) -> list[Cell]:
    pieces: list[Piece] = []
    for index, group in enumerate(groups):
        if index:
            pieces.append(_SEPARATOR)
        pieces.extend(group)
    return [(char, style) for text, style in pieces for char in text]


def _host_group(cpu: str, memory: tuple[Piece, ...]) -> tuple[Piece, ...]:
    return _group(("Host", _WHITE_BOLD), (" CPU", _WHITE), (f" {cpu}", _CYAN), *memory)


def _host_memory(value: str) -> tuple[Piece, ...]:
    return (" RAM", _WHITE), (f" {value}", _CYAN)


def _process_group(label: str, cpu: str, rss: str) -> tuple[Piece, ...]:
    return _group(
        (label, _WHITE_BOLD),
        (" CPU", _WHITE),
        (f" {cpu}", _CYAN),
        (" RSS", _WHITE),
        (f" {rss}", _CYAN),
    )


def _style_of(style: RichStyle | None) -> Style:
    if style is None:
        return (False, None)
    color = Color.from_rich_color(style.color).hex.lower() if style.color is not None else None
    return (bool(style.bold), color)


def _rendered_lines(widget: Widget) -> list[list[Cell]]:
    region = Region(0, 0, widget.region.width, widget.region.height)
    rendered: list[list[Cell]] = []
    for strip in widget.render_lines(region):
        cells = [(char, _style_of(segment.style)) for segment in strip for char in segment.text]
        while cells and cells[-1][0] == " ":
            cells.pop()
        rendered.append(cells)
    return rendered


def _usage_context(reader: StateReader, project_dir: Path, clock: FakeClock) -> WidgetContext:
    return replace(
        widget_context(reader, project_dir, clock),
        placement=WidgetPlacement.PROJECT_USAGE,
    )


_TOKEN_CASES: tuple[tuple[Callable[[Connection, FakeClock], None], list[list[Cell]]], ...] = (
    (
        _seed_billed,
        [
            _line(
                _group(("1", _WHITE_BOLD), (" session", _GREY)),
                _group(("1.5k", _CYAN_BOLD), (" tokens", _GREY)),
                _group(("$4.28", _GREEN_BOLD), (" API", _GREY)),
            )
        ],
    ),
    (
        _seed_zero_billed,
        [
            _line(
                _group(("1", _WHITE_BOLD), (" session", _GREY)),
                _group(("1.5k", _CYAN_BOLD), (" tokens", _GREY)),
                _group(("$0.00", _GREEN_BOLD), (" API", _GREY)),
            )
        ],
    ),
    (
        _seed_estimate_only,
        [
            _line(
                _group(("1", _WHITE_BOLD), (" session", _GREY)),
                _group(("1.5k", _CYAN_BOLD), (" tokens", _GREY)),
                _group(("1 cost unreported", _GREY)),
            )
        ],
    ),
    (
        _seed_subscription_only,
        [
            _line(
                _group(("1", _WHITE_BOLD), (" session", _GREY)),
                _group(("1.5k", _CYAN_BOLD), (" tokens", _GREY)),
            )
        ],
    ),
    (
        _seed_unknown_tokens,
        [
            _line(
                _group(("1", _WHITE_BOLD), (" session", _GREY)),
                _group(("tokens unknown", _GREY)),
                _group(("1 cost unreported", _GREY)),
            )
        ],
    ),
    (
        _seed_nothing,
        [
            _line(
                _group(("0", _WHITE_BOLD), (" sessions", _GREY)),
                _group(("tokens unknown", _GREY)),
            )
        ],
    ),
    (
        _seed_partial_coverage,
        [
            _line(
                _group(("3", _WHITE_BOLD), (" sessions", _GREY)),
                _group(("3.0k tokens", _GREY)),
                _group(("$4.28", _GREEN_BOLD), (" API", _GREY)),
                _group(("1 session without tokens", _GREY)),
                _group(("1 cost unreported", _GREY)),
                _group(("1 billing mode unknown", _GREY)),
            )
        ],
    ),
)


@pytest.mark.parametrize(("seed", "expected"), _TOKEN_CASES)
async def test_home_token_bar_renders_the_template_for_each_recorded_state(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
    seed: Callable[[Connection, FakeClock], None],
    expected: list[list[Cell]],
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(_START)
    seed(connection, clock)
    widget = UsageWidget().create(_usage_context(reader, project_dir, clock))
    async with WidgetHostApp(widget).run_test(size=_WIDE):
        await wait_until(lambda: _rendered_lines(widget) == expected, timeout=_WAIT_SECONDS)


async def test_home_token_bar_wraps_whole_groups_at_narrow_width(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(_START)
    _seed_partial_coverage(connection, clock)
    expected = [
        _line(
            _group(("3", _WHITE_BOLD), (" sessions", _GREY)),
            _group(("3.0k tokens", _GREY)),
            _group(("$4.28", _GREEN_BOLD), (" API", _GREY)),
        ),
        _line(
            _group(("1 session without tokens", _GREY)),
            _group(("1 cost unreported", _GREY)),
        ),
        _line(_group(("1 billing mode unknown", _GREY))),
    ]
    widget = UsageWidget().create(_usage_context(reader, project_dir, clock))
    async with WidgetHostApp(widget).run_test(size=_NARROW):
        await wait_until(lambda: _rendered_lines(widget) == expected, timeout=_WAIT_SECONDS)


_POPULATED_SAMPLE = ResourceView(
    host_cpu_pct=5.0,
    host_ram_used_pct=50.0,
    free_ram_gb=12.0,
    instance_cpu_pct=3.0,
    instance_rss_gb=1.6,
    state=DiagnosticSampleState.AVAILABLE,
    host_ram_used_bytes=round(12.3 * _GIB),
    host_ram_total_bytes=round(24.0 * _GIB),
    shikiru_cpu_pct=1.0,
    shikiru_rss_gb=0.4,
    agents_cpu_pct=2.0,
    agents_rss_gb=1.2,
    shikiru_process_tree_empty=False,
    agents_process_tree_empty=False,
)
_POPULATED_LINE = _line(
    _host_group("5%", _host_memory("12.3/24.0 GiB")),
    _process_group("Shikiru", "1%", "0.4 GiB"),
    _process_group("Agents", "2%", "1.2 GiB"),
)
_RESOURCE_CASES: tuple[tuple[ResourceView, list[list[Cell]]], ...] = (
    (_POPULATED_SAMPLE, [_POPULATED_LINE]),
    (
        replace(
            _POPULATED_SAMPLE,
            agents_cpu_pct=None,
            agents_rss_gb=None,
            agents_process_tree_empty=True,
        ),
        [
            _line(
                _host_group("5%", _host_memory("12.3/24.0 GiB")),
                _process_group("Shikiru", "1%", "0.4 GiB"),
                _group(("Agents", _WHITE_BOLD), (" process tree is empty", _GREY)),
            )
        ],
    ),
    (
        replace(_POPULATED_SAMPLE, shikiru_cpu_pct=None, shikiru_rss_gb=None),
        [
            _line(
                _host_group("5%", _host_memory("12.3/24.0 GiB")),
                _group(("Shikiru CPU/RSS unavailable", _GREY)),
                _process_group("Agents", "2%", "1.2 GiB"),
            )
        ],
    ),
    (
        replace(_POPULATED_SAMPLE, host_ram_used_bytes=None),
        [
            _line(
                _host_group("5%", ((" RAM used/total unavailable", _GREY),)),
                _process_group("Shikiru", "1%", "0.4 GiB"),
                _process_group("Agents", "2%", "1.2 GiB"),
            )
        ],
    ),
    (
        replace(
            _POPULATED_SAMPLE,
            state=DiagnosticSampleState.STALE,
            sampled_ago="2s",
            error="CPU unavailable",
        ),
        [
            _line(_group(("Stale · observed 2s ago · CPU unavailable", _AMBER_TEXT))),
            _POPULATED_LINE,
        ],
    ),
    (
        replace(
            _POPULATED_SAMPLE,
            state=DiagnosticSampleState.FAILED,
            sampled_ago="2s",
            error="CPU unavailable",
        ),
        [_line(_group(("Resources failed · CPU unavailable · observed 2s ago", _ALERT_TEXT)))],
    ),
)


@pytest.mark.parametrize(("sample", "expected"), _RESOURCE_CASES)
async def test_home_resource_bar_renders_the_template_for_each_sample_state(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
    sample: ResourceView,
    expected: list[list[Cell]],
) -> None:
    del connection
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(_START)
    services = StubServices()
    services.resource_observation = sample
    widget = ResourcesWidget().create(widget_context(reader, project_dir, clock, services=services))
    async with WidgetHostApp(widget).run_test(size=_WIDE):
        await wait_until(lambda: _rendered_lines(widget) == expected, timeout=_WAIT_SECONDS)


async def test_home_resource_bar_wraps_whole_groups_at_narrow_width(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    del connection
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(_START)
    services = StubServices()
    services.resource_observation = _POPULATED_SAMPLE
    expected = [
        _line(_host_group("5%", _host_memory("12.3/24.0 GiB"))),
        _line(_process_group("Shikiru", "1%", "0.4 GiB")),
        _line(_process_group("Agents", "2%", "1.2 GiB")),
    ]
    widget = ResourcesWidget().create(widget_context(reader, project_dir, clock, services=services))
    async with WidgetHostApp(widget).run_test(size=_NARROW):
        await wait_until(lambda: _rendered_lines(widget) == expected, timeout=_WAIT_SECONDS)
