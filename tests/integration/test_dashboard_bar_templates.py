"""Home token and resource bars render their operator templates for each recorded state."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

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
from tests.support.cells import (
    ALERT_TEXT,
    AMBER_TEXT,
    CYAN,
    CYAN_BOLD,
    GREEN_BOLD,
    GREY,
    WHITE,
    WHITE_BOLD,
    Cell,
    Piece,
    group,
    line,
    rendered_lines,
)
from tests.support.clock import FakeClock
from tests.support.config import write_minimal_project
from tests.support.dashboard import StubServices, WidgetHostApp, widget_context
from tests.support.store import seed_task
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from shikiru.agents.contracts import CostBasis
    from shikiru.dashboard.contract import WidgetContext
    from shikiru.store.reader import StateReader
    from tests.support.store import Connection

pytestmark = pytest.mark.usefixtures("colour_terminal")
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


def _seed_legacy_only(connection: Connection, clock: FakeClock) -> None:
    task = seed_task(connection, clock, title="Legacy usage")
    runs = RunStore(connection, clock)
    run = runs.start(task.id, Role.IMPLEMENTER, _RUNG, billing_declaration=_SUBSCRIPTION)
    runs.record_usage(run.id, _tokens(None, COST_BASIS_UNKNOWN))
    connection.execute("UPDATE runs SET usage_reported = NULL WHERE id = ?", (run.id,))


def _seed_nothing(connection: Connection, clock: FakeClock) -> None:
    del connection, clock


def _host_group(cpu: str, memory: tuple[Piece, ...]) -> tuple[Piece, ...]:
    return group(("Host", WHITE_BOLD), (" CPU", WHITE), (f" {cpu}", CYAN), *memory)


def _host_memory(value: str) -> tuple[Piece, ...]:
    return (" RAM", WHITE), (f" {value}", CYAN)


def _process_group(label: str, cpu: str, rss: str) -> tuple[Piece, ...]:
    return group(
        (label, WHITE_BOLD),
        (" CPU", WHITE),
        (f" {cpu}", CYAN),
        (" RSS", WHITE),
        (f" {rss}", CYAN),
    )


def _usage_context(reader: StateReader, project_dir: Path, clock: FakeClock) -> WidgetContext:
    return replace(
        widget_context(reader, project_dir, clock),
        placement=WidgetPlacement.PROJECT_USAGE,
    )


_TOKEN_CASES: tuple[tuple[Callable[[Connection, FakeClock], None], list[list[Cell]]], ...] = (
    (
        _seed_billed,
        [
            line(
                group(("1", WHITE_BOLD), (" session", GREY)),
                group(("1.5k", CYAN_BOLD), (" processed tokens", GREY)),
                group(("$4.28", GREEN_BOLD), (" API", GREY)),
            )
        ],
    ),
    (
        _seed_zero_billed,
        [
            line(
                group(("1", WHITE_BOLD), (" session", GREY)),
                group(("1.5k", CYAN_BOLD), (" processed tokens", GREY)),
                group(("$0.00", GREEN_BOLD), (" API", GREY)),
            )
        ],
    ),
    (
        _seed_estimate_only,
        [
            line(
                group(("1", WHITE_BOLD), (" session", GREY)),
                group(("1.5k", CYAN_BOLD), (" processed tokens", GREY)),
                group(("1 cost unreported", GREY)),
            )
        ],
    ),
    (
        _seed_subscription_only,
        [
            line(
                group(("1", WHITE_BOLD), (" session", GREY)),
                group(("1.5k", CYAN_BOLD), (" processed tokens", GREY)),
            )
        ],
    ),
    (
        _seed_unknown_tokens,
        [
            line(
                group(("1", WHITE_BOLD), (" session", GREY)),
                group(("tokens unknown", GREY)),
                group(("1 cost unreported", GREY)),
            )
        ],
    ),
    (
        _seed_nothing,
        [
            line(
                group(("0", WHITE_BOLD), (" sessions", GREY)),
                group(("tokens unknown", GREY)),
            )
        ],
    ),
    (
        _seed_partial_coverage,
        [
            line(
                group(("3", WHITE_BOLD), (" sessions", GREY)),
                group(("3.0k", CYAN_BOLD), (" processed tokens (partial)", GREY)),
                group(("$4.28", GREEN_BOLD), (" API", GREY)),
                group(("1 session without tokens", GREY)),
                group(("1 cost unreported", GREY)),
            ),
            line(group(("1 billing mode unknown", GREY))),
        ],
    ),
    (
        _seed_legacy_only,
        [
            line(
                group(("1", WHITE_BOLD), (" session", GREY)),
                group(("processed unknown", GREY)),
                group(("1 legacy session", GREY)),
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
        await wait_until(lambda: rendered_lines(widget) == expected, timeout=_WAIT_SECONDS)


async def test_home_token_bar_wraps_whole_groups_at_narrow_width(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(_START)
    _seed_partial_coverage(connection, clock)
    expected = [
        line(
            group(("3", WHITE_BOLD), (" sessions", GREY)),
            group(("3.0k", CYAN_BOLD), (" processed tokens (partial)", GREY)),
        ),
        line(
            group(("$4.28", GREEN_BOLD), (" API", GREY)),
            group(("1 session without tokens", GREY)),
        ),
        line(
            group(("1 cost unreported", GREY)),
            group(("1 billing mode unknown", GREY)),
        ),
    ]
    widget = UsageWidget().create(_usage_context(reader, project_dir, clock))
    async with WidgetHostApp(widget).run_test(size=_NARROW):
        await wait_until(lambda: rendered_lines(widget) == expected, timeout=_WAIT_SECONDS)


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
_POPULATED_LINE = line(
    _host_group("5.0%", _host_memory("12.3/24.0 GiB")),
    _process_group("Shikiru", "1.0%", "0.4 GiB"),
    _process_group("Agents", "2.0%", "1.2 GiB"),
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
            line(
                _host_group("5.0%", _host_memory("12.3/24.0 GiB")),
                _process_group("Shikiru", "1.0%", "0.4 GiB"),
                group(("Agents", WHITE_BOLD), (" process tree is empty", GREY)),
            )
        ],
    ),
    (
        replace(_POPULATED_SAMPLE, shikiru_cpu_pct=None, shikiru_rss_gb=None),
        [
            line(
                _host_group("5.0%", _host_memory("12.3/24.0 GiB")),
                group(("Shikiru CPU/RSS unavailable", GREY)),
                _process_group("Agents", "2.0%", "1.2 GiB"),
            )
        ],
    ),
    (
        replace(_POPULATED_SAMPLE, host_ram_used_bytes=None),
        [
            line(
                _host_group("5.0%", ((" RAM used/total unavailable", GREY),)),
                _process_group("Shikiru", "1.0%", "0.4 GiB"),
                _process_group("Agents", "2.0%", "1.2 GiB"),
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
            line(group(("Stale · observed 2s ago · CPU unavailable", AMBER_TEXT))),
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
        [line(group(("Resources failed · CPU unavailable · observed 2s ago", ALERT_TEXT)))],
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
        await wait_until(lambda: rendered_lines(widget) == expected, timeout=_WAIT_SECONDS)


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
        line(_host_group("5.0%", _host_memory("12.3/24.0 GiB"))),
        line(_process_group("Shikiru", "1.0%", "0.4 GiB")),
        line(_process_group("Agents", "2.0%", "1.2 GiB")),
    ]
    widget = ResourcesWidget().create(widget_context(reader, project_dir, clock, services=services))
    async with WidgetHostApp(widget).run_test(size=_NARROW):
        await wait_until(lambda: rendered_lines(widget) == expected, timeout=_WAIT_SECONDS)
