"""The Project usage popup carries one title and the approved Totals, By agent and Limits layout."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from textual.containers import VerticalScroll
from textual.widgets import Static

from shikiru.agents.contracts import (
    BILLING_MODE_API,
    BILLING_MODE_SUBSCRIPTION,
    BILLING_SOURCE_OPERATOR_CONFIG,
    COST_BASIS_API_EQUIVALENT,
    COST_BASIS_BILLED,
    COST_BASIS_UNKNOWN,
    UNKNOWN_BILLING_DECLARATION,
    BillingDeclaration,
    QuotaWindow,
    Rung,
    Usage,
)
from shikiru.domain.tasks import Role
from shikiru.plugins.widgets.usage import UsagePanel
from shikiru.store.quotas import QuotaStore
from shikiru.store.runs import RunStore
from shikiru.ui.glyphs import GAUGE_GLYPHS
from shikiru.ui.tokens import AMBER, DUSTY_ROSE, NEUTRAL_BORDER_COLOR, SAGE
from tests.support.cells import (
    AMBER_TEXT,
    BLANK,
    CYAN_BOLD,
    GREEN_BOLD,
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
    WidgetHostApp,
    dashboard_app,
    open_usage_details,
    widget_context,
)
from tests.support.store import seed_task
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from shikiru.agents.contracts import CostBasis
    from shikiru.store.reader import StateReader
    from tests.support.store import Connection

pytestmark = pytest.mark.usefixtures("colour_terminal")

_START = datetime(2026, 1, 1, tzinfo=UTC)
_WIDE = (100, 40)
_COMPACT = (48, 40)
_TINY = (20, 8)
_WAIT_SECONDS = 5
_PROJECT_USAGE_TITLE = "Project usage"
_API = BillingDeclaration(BILLING_MODE_API, BILLING_SOURCE_OPERATOR_CONFIG)
_SUBSCRIPTION = BillingDeclaration(BILLING_MODE_SUBSCRIPTION, BILLING_SOURCE_OPERATOR_CONFIG)
_MILLION = 1_000_000
_OBSERVED_AGO = timedelta(minutes=2)
_OLDER_OBSERVED_AGO = timedelta(hours=1)

_DIM: Style = (False, "#969ca2")
_RULE_STYLE: Style = (False, NEUTRAL_BORDER_COLOR)
_RULE_CHARACTER = "\N{BOX DRAWINGS LIGHT HORIZONTAL}"
_OK: Style = (False, SAGE)
_WARN: Style = (False, AMBER)
_ALERT: Style = (False, DUSTY_ROSE)
_HALF = GAUGE_GLYPHS[2]
_FULL = GAUGE_GLYPHS[-1]


def _record_run(
    connection: Connection,
    clock: FakeClock,
    agent: str,
    declaration: BillingDeclaration,
    usage: Usage | None = None,
) -> None:
    task = seed_task(connection, clock, title="Recorded usage")
    runs = RunStore(connection, clock)
    rung = Rung(agent, "model", "low")
    run = runs.start(task.id, Role.IMPLEMENTER, rung, billing_declaration=declaration)
    if usage is not None:
        runs.record_usage(run.id, usage)


def _tokens(
    cost_usd: float | None,
    cost_basis: CostBasis,
    *,
    input_tokens: int = 1000,
    output_tokens: int = 500,
    cache_read: int = 0,
    cache_write: int = 0,
) -> Usage:
    return Usage(input_tokens, output_tokens, cache_read, cache_write, cost_usd, cost_basis, None)


def _quota(
    connection: Connection,
    agent: str,
    window: str,
    used_pct: float,
    resets_in: timedelta,
    *,
    observed_ago: timedelta = _OBSERVED_AGO,
) -> None:
    QuotaStore(connection).record(
        agent, QuotaWindow(window, used_pct, _START + resets_in, _START - observed_ago)
    )


def _seed_full(connection: Connection, clock: FakeClock) -> None:
    half = _MILLION // 2
    _record_run(
        connection,
        clock,
        "claude",
        _API,
        _tokens(1.0, COST_BASIS_BILLED, input_tokens=_MILLION, output_tokens=half),
    )
    _record_run(
        connection,
        clock,
        "claude",
        _API,
        _tokens(3.28, COST_BASIS_BILLED, cache_read=9 * _MILLION, cache_write=300_000),
    )
    _record_run(connection, clock, "claude", _SUBSCRIPTION, _tokens(None, COST_BASIS_UNKNOWN))
    _record_run(connection, clock, "codex", _SUBSCRIPTION, _tokens(6.5, COST_BASIS_API_EQUIVALENT))
    _record_run(connection, clock, "codex", UNKNOWN_BILLING_DECLARATION)
    _quota(connection, "claude", "5h", 38.0, timedelta(hours=1, minutes=40))
    _quota(connection, "claude", "weekly", 91.0, timedelta(days=3, hours=2))
    _quota(connection, "codex", "5h", 62.0, timedelta(hours=2, minutes=15))


def _seed_subscription_only(connection: Connection, clock: FakeClock) -> None:
    _record_run(connection, clock, "claude", _SUBSCRIPTION, _tokens(None, COST_BASIS_UNKNOWN))


def _seed_api_only(connection: Connection, clock: FakeClock) -> None:
    _record_run(connection, clock, "codex", _API, _tokens(4.28, COST_BASIS_BILLED))


def _seed_estimate_only(connection: Connection, clock: FakeClock) -> None:
    _record_run(connection, clock, "codex", _API, _tokens(0.11, COST_BASIS_API_EQUIVALENT))


def _seed_unknown_only(connection: Connection, clock: FakeClock) -> None:
    _record_run(connection, clock, "codex", UNKNOWN_BILLING_DECLARATION)


def _seed_zero_cost_unknown_mode(connection: Connection, clock: FakeClock) -> None:
    _record_run(
        connection, clock, "codex", UNKNOWN_BILLING_DECLARATION, _tokens(0.0, COST_BASIS_BILLED)
    )


def _seed_subscription_spend(connection: Connection, clock: FakeClock) -> None:
    _record_run(connection, clock, "claude", _SUBSCRIPTION, _tokens(2.5, COST_BASIS_BILLED))


def _seed_partial_coverage(connection: Connection, clock: FakeClock) -> None:
    _seed_api_only(connection, clock)
    _record_run(connection, clock, "codex", _API)
    _record_run(
        connection, clock, "codex", UNKNOWN_BILLING_DECLARATION, _tokens(None, COST_BASIS_UNKNOWN)
    )


def _seed_mixed_observation_ages(connection: Connection, clock: FakeClock) -> None:
    del clock
    _quota(connection, "claude", "5h", 10.0, timedelta(hours=1))
    _quota(
        connection, "claude", "weekly", 20.0, timedelta(days=1), observed_ago=_OLDER_OBSERVED_AGO
    )


def _ink(cells: list[Cell]) -> list[Cell]:
    return [cell for cell in cells if cell[0] != BLANK]


def _row(label: str, *groups: tuple[Piece, ...]) -> list[Cell]:
    return _ink([(char, GREY) for char in label] + line(*groups))


def _heading(label: str) -> list[Cell]:
    return _row(label)


def _rule(width: int) -> list[Cell]:
    return [(_RULE_CHARACTER, _RULE_STYLE)] * width


def _count(count: int, word: str) -> tuple[Piece, ...]:
    return group((str(count), WHITE_BOLD), (f" {word}", GREY))


_PARTIAL = " (partial)"


def _tokens_total(total: str, suffix: str = "") -> tuple[Piece, ...]:
    return group((total, CYAN_BOLD), (f" processed tokens{suffix}", GREY))


_NO_TOKENS = group(("tokens unknown", GREY))


def _api_total(amount: str) -> tuple[Piece, ...]:
    return group((amount, GREEN_BOLD), (" API", GREY))


def _billed(amount: str) -> tuple[Piece, ...]:
    return group((amount, GREEN_BOLD), (" billed", GREY))


def _estimate(amount: str, qualifier: str = "") -> tuple[Piece, ...]:
    return group((f"est. {amount}", AMBER_TEXT), (qualifier, GREY))


def _detail(label: str, amount: str) -> tuple[Piece, ...]:
    return group((f"{label} ", GREY), (amount, CYAN_BOLD))


_TOKENS_1K = (
    _detail("uncached input", "1.0k"),
    _detail("cache read", "0"),
    _detail("cache write", "0"),
    _detail("output", "500"),
)
_PARTIAL_TOKENS_2K = (
    _detail("uncached input", f"2.0k{_PARTIAL}"),
    _detail("cache read", f"0{_PARTIAL}"),
    _detail("cache write", f"0{_PARTIAL}"),
    _detail("output", f"1.0k{_PARTIAL}"),
)


def _gauge(window: str, glyph_and_pct: str, style: Style, resets: str) -> tuple[Piece, ...]:
    return group((window, _DIM), (f" {glyph_and_pct}", style), (f" resets {resets}", GREY))


def _agent_row(name: str, *groups: tuple[Piece, ...]) -> list[Cell]:
    return _ink([(char, WHITE_BOLD) for char in name] + line(*groups))


def _limit_row(name: str, window: tuple[Piece, ...]) -> list[Cell]:
    return _ink([(char, WHITE_BOLD) for char in name] + line(window))


_FULL_LAYOUT = [
    _row(
        "Totals",
        _count(5, "sessions"),
        _tokens_total("10.8M", _PARTIAL),
        _api_total("$4.28"),
    ),
    _row(
        "Tokens",
        _detail("uncached input", f"1.0M{_PARTIAL}"),
        _detail("cache read", f"9.0M{_PARTIAL}"),
        _detail("cache write", f"300.0k{_PARTIAL}"),
    ),
    _row("", _detail("output", f"501.5k{_PARTIAL}")),
    _row("Coverage", _count(4, "reported"), _count(1, "without tokens")),
    _row(
        "Billing",
        _count(2, "subscription"),
        _count(2, "API billed"),
        _count(1, "unknown"),
    ),
    _row("", _estimate("$6.50", " API-equivalent (1 session)")),
    _rule(_WIDE[0]),
    _heading("By agent"),
    _agent_row(
        "claude",
        _count(3, "runs"),
        _tokens_total("10.8M"),
        _billed("$4.28"),
        group(("1 cost unknown", GREY)),
    ),
    _agent_row(
        "codex",
        _count(2, "runs"),
        _tokens_total("1.5k", _PARTIAL),
        group(("1 without tokens", GREY)),
        _estimate("$6.50"),
        group(("1 cost unknown", GREY)),
    ),
    _rule(_WIDE[0]),
    _heading("Limits"),
    _limit_row("claude", _gauge("5h", f"{_HALF} 38%", _OK, "1h 40m")),
    _limit_row("", _gauge("weekly", f"{_FULL} 91%", _ALERT, "3d 2h 0m")),
    _limit_row("codex", _gauge("5h", f"{_HALF} 62%", _WARN, "2h 15m")),
    _row("observed 2m ago"),
]


async def _settled_lines(widget: UsagePanel) -> list[list[Cell]]:
    await wait_until(lambda: any(rendered_lines(widget)), timeout=_WAIT_SECONDS)
    return [_ink(cells) for cells in rendered_lines(widget)]


def _plain_lines(widget: UsagePanel) -> list[str]:
    return ["".join(char for char, _ in cells) for cells in rendered_lines(widget)]


async def test_panel_renders_totals_by_agent_and_limits_in_the_operator_styles(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    clock = FakeClock(_START)
    _seed_full(connection, clock)
    panel = UsagePanel(widget_context(reader, write_minimal_project(tmp_path), clock))
    async with WidgetHostApp(panel).run_test(size=_WIDE):
        assert await _settled_lines(panel) == _FULL_LAYOUT
        text = "\n".join(_plain_lines(panel))
        totals, rest = text.split("By agent")
        by_agent, limits = rest.split("Limits")
        for fact in ("API billed", "subscription", "API-equivalent", "Coverage", "Billing"):
            assert text.count(fact) == 1
        assert "sessions" in totals
        assert "sessions" not in by_agent + limits
        assert "runs" not in totals + limits
        assert "resets" not in totals + by_agent
        assert not any(
            word in text
            for word in ("billing_mode", "operator_config", "provider_reported", "New sessions")
        )


def _single_agent_state(
    name: str, totals: list[list[Cell]], agent_groups: tuple[tuple[Piece, ...], ...]
) -> list[list[Cell]]:
    return [*totals, _rule(_WIDE[0]), _heading("By agent"), _agent_row(name, *agent_groups)]


_STATE_CASES: tuple[tuple[Callable[[Connection, FakeClock], None], list[list[Cell]]], ...] = (
    (
        _seed_subscription_only,
        _single_agent_state(
            "claude",
            [
                _row("Totals", _count(1, "session"), _tokens_total("1.5k")),
                _row("Tokens", *_TOKENS_1K),
                _row("Billing", _count(1, "subscription")),
            ],
            (_count(1, "run"), _tokens_total("1.5k"), group(("cost unknown", GREY))),
        ),
    ),
    (
        _seed_api_only,
        _single_agent_state(
            "codex",
            [
                _row("Totals", _count(1, "session"), _tokens_total("1.5k"), _api_total("$4.28")),
                _row("Tokens", *_TOKENS_1K),
                _row("Billing", _count(1, "API billed")),
            ],
            (_count(1, "run"), _tokens_total("1.5k"), _billed("$4.28")),
        ),
    ),
    (
        _seed_estimate_only,
        _single_agent_state(
            "codex",
            [
                _row("Totals", _count(1, "session"), _tokens_total("1.5k")),
                _row("Tokens", *_TOKENS_1K),
                _row("Billing", _count(1, "API unreported")),
                _row("", _estimate("$0.11", " API-equivalent (1 session)")),
            ],
            (_count(1, "run"), _tokens_total("1.5k"), _estimate("$0.11")),
        ),
    ),
    (
        _seed_unknown_only,
        _single_agent_state(
            "codex",
            [
                _row("Totals", _count(1, "session"), _NO_TOKENS),
                _row("Billing", _count(1, "unknown")),
            ],
            (_count(1, "run"), _NO_TOKENS, group(("cost unknown", GREY))),
        ),
    ),
    (
        _seed_zero_cost_unknown_mode,
        _single_agent_state(
            "codex",
            [
                _row("Totals", _count(1, "session"), _tokens_total("1.5k")),
                _row("Tokens", *_TOKENS_1K),
                _row("Billing", _count(1, "unknown")),
            ],
            (_count(1, "run"), _tokens_total("1.5k"), _billed("$0.00")),
        ),
    ),
    (
        _seed_subscription_spend,
        _single_agent_state(
            "claude",
            [
                _row("Totals", _count(1, "session"), _tokens_total("1.5k"), _billed("$2.50")),
                _row("Tokens", *_TOKENS_1K),
                _row("Billing", _count(1, "subscription")),
            ],
            (_count(1, "run"), _tokens_total("1.5k"), _billed("$2.50")),
        ),
    ),
    (
        _seed_partial_coverage,
        _single_agent_state(
            "codex",
            [
                _row(
                    "Totals",
                    _count(3, "sessions"),
                    _tokens_total("3.0k", _PARTIAL),
                    _api_total("$4.28"),
                ),
                _row("Tokens", *_PARTIAL_TOKENS_2K[:3]),
                _row("", _PARTIAL_TOKENS_2K[3]),
                _row("Coverage", _count(2, "reported"), _count(1, "without tokens")),
                _row(
                    "Billing",
                    _count(1, "API billed"),
                    _count(1, "API unreported"),
                    _count(1, "unknown"),
                ),
            ],
            (
                _count(3, "runs"),
                _tokens_total("3.0k", _PARTIAL),
                group(("1 without tokens", GREY)),
                _billed("$4.28"),
                group(("2 cost unknown", GREY)),
            ),
        ),
    ),
)


@pytest.mark.parametrize(("seed", "expected"), _STATE_CASES)
async def test_panel_states_keep_unknown_partial_and_estimate_facts_honest(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
    seed: Callable[[Connection, FakeClock], None],
    expected: list[list[Cell]],
) -> None:
    clock = FakeClock(_START)
    seed(connection, clock)
    panel = UsagePanel(widget_context(reader, write_minimal_project(tmp_path), clock))
    async with WidgetHostApp(panel).run_test(size=_WIDE):
        assert await _settled_lines(panel) == expected


async def test_panel_without_runs_says_so_under_the_unknown_totals(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    del connection
    panel = UsagePanel(widget_context(reader, write_minimal_project(tmp_path), FakeClock(_START)))
    async with WidgetHostApp(panel).run_test(size=_WIDE):
        await _settled_lines(panel)
        assert _plain_lines(panel)[:2] == [
            "Totals   0 sessions · tokens unknown",
            "No runs or quota windows recorded.",
        ]


_SHARED_AGE_LIMITS = [
    f"claude 5h {GAUGE_GLYPHS[2]} 38% resets 1h 40m",
    f"       weekly {GAUGE_GLYPHS[4]} 91% resets 3d 2h 0m",
    f"codex  5h {GAUGE_GLYPHS[2]} 62% resets 2h 15m",
    "observed 2m ago",
]
_MIXED_AGE_LIMITS = [
    f"claude 5h {GAUGE_GLYPHS[0]} 10% resets 1h 0m · observed 2m ago",
    f"       weekly {GAUGE_GLYPHS[1]} 20% resets 1d 0h 0m · observed 1h ago",
]


@pytest.mark.parametrize(
    ("seed", "expected"),
    [
        pytest.param(_seed_full, _SHARED_AGE_LIMITS, id="shared-age-prints-once"),
        pytest.param(
            _seed_mixed_observation_ages, _MIXED_AGE_LIMITS, id="differing-ages-per-window"
        ),
    ],
)
async def test_observation_age_is_printed_once_when_shared_and_per_window_otherwise(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
    seed: Callable[[Connection, FakeClock], None],
    expected: list[str],
) -> None:
    clock = FakeClock(_START)
    seed(connection, clock)
    panel = UsagePanel(widget_context(reader, write_minimal_project(tmp_path), clock))
    async with WidgetHostApp(panel).run_test(size=_WIDE):
        await _settled_lines(panel)
        lines = _plain_lines(panel)
        assert lines[lines.index("Limits") + 1 :] == expected


_COMPACT_LAYOUT = [
    "Totals   5 sessions",
    "         10.8M processed tokens (partial)",
    "         $4.28 API",
    "Tokens   uncached input 1.0M (partial)",
    "         cache read 9.0M (partial)",
    "         cache write 300.0k (partial)",
    "         output 501.5k (partial)",
    "Coverage 4 reported · 1 without tokens",
    "Billing  2 subscription · 2 API billed",
    "         1 unknown",
    "         est. $6.50 API-equivalent (1 session)",
    _RULE_CHARACTER * _COMPACT[0],
    "By agent",
    "claude 3 runs · 10.8M processed tokens",
    "       $4.28 billed · 1 cost unknown",
    "codex  2 runs · 1.5k processed tokens (partial)",
    "       1 without tokens · est. $6.50",
    "       1 cost unknown",
    _RULE_CHARACTER * _COMPACT[0],
    "Limits",
    f"claude 5h {GAUGE_GLYPHS[2]} 38% resets 1h 40m",
    f"       weekly {GAUGE_GLYPHS[4]} 91% resets 3d 2h 0m",
    f"codex  5h {GAUGE_GLYPHS[2]} 62% resets 2h 15m",
    "observed 2m ago",
]


_TINY_LAYOUT = [
    "Totals",
    "5 sessions",
    "10.8M processed",
    "tokens (partial)",
    "$4.28 API",
    "Tokens",
    "uncached input",
    "1.0M (partial)",
    "cache read 9.0M",
    "(partial)",
    "cache write 300.0k",
    "(partial)",
    "output 501.5k",
    "(partial)",
    "Coverage",
    "4 reported",
    "1 without tokens",
    "Billing",
    "2 subscription",
    "2 API billed",
    "1 unknown",
    "est. $6.50",
    "API-equivalent (1",
    "session)",
    _RULE_CHARACTER * (_TINY[0] - 2),
    "By agent",
    "claude",
    "3 runs",
    "10.8M processed",
    "tokens",
    "$4.28 billed",
    "1 cost unknown",
    "codex",
    "2 runs",
    "1.5k processed",
    "tokens (partial)",
    "1 without tokens",
    "est. $6.50",
    "1 cost unknown",
    _RULE_CHARACTER * (_TINY[0] - 2),
    "Limits",
    "claude",
    f"5h {GAUGE_GLYPHS[2]} 38% resets 1h",
    "40m",
    f"weekly {GAUGE_GLYPHS[4]} 91%",
    "resets 3d 2h 0m",
    "codex",
    f"5h {GAUGE_GLYPHS[2]} 62% resets 2h",
    "15m",
    "observed 2m ago",
]


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        pytest.param(_COMPACT, _COMPACT_LAYOUT, id="wraps-only-at-group-separators"),
        pytest.param(_TINY, _TINY_LAYOUT, id="stacks-labels-above-whole-values"),
    ],
)
async def test_narrow_widths_wrap_rows_without_splitting_values(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
    size: tuple[int, int],
    expected: list[str],
) -> None:
    clock = FakeClock(_START)
    _seed_full(connection, clock)
    panel = UsagePanel(widget_context(reader, write_minimal_project(tmp_path), clock))
    async with WidgetHostApp(panel).run_test(size=size):
        await _settled_lines(panel)
        assert _plain_lines(panel) == expected


async def test_tiny_popup_is_titled_and_keeps_every_section_label_in_a_scrolling_body(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    _seed_full(connection, FakeClock(_START))
    app = dashboard_app(
        reader, write_minimal_project(tmp_path), FakeClock(_START), widget_names=["usage"]
    )
    async with app.run_test(size=_TINY) as pilot:
        await open_usage_details(pilot)
        await wait_until(lambda: bool(app.screen.query(UsagePanel)), timeout=_WAIT_SECONDS)
        panel = app.screen.query_one(UsagePanel)
        await _settled_lines(panel)
        title = app.screen.query_one("#context-widget-title", Static)
        assert str(title.render()) == _PROJECT_USAGE_TITLE
        lines = _plain_lines(panel)
        assert all(label in lines for label in ("Totals", "By agent", "Limits"))
        assert app.screen.query_one("#context-widget-content", VerticalScroll).max_scroll_y > 0


_HALF_GAUGE = f"7d {GAUGE_GLYPHS[2]} 50%"
_UNKNOWN_GAUGE = f"7d {GAUGE_GLYPHS[0]} --%"


@pytest.mark.parametrize(
    ("resets_in", "expected"),
    [
        (timedelta(hours=135, minutes=46), f"{_HALF_GAUGE} resets 5d 15h 46m"),
        (timedelta(hours=24), f"{_HALF_GAUGE} resets 1d 0h 0m"),
        (timedelta(hours=23, minutes=59, seconds=59), f"{_HALF_GAUGE} resets 23h 59m"),
        (timedelta(minutes=5, seconds=7), f"{_HALF_GAUGE} resets 5m 7s"),
        (timedelta(seconds=-30), f"{_UNKNOWN_GAUGE} resets unknown"),
        (None, f"{_HALF_GAUGE} resets unknown"),
    ],
    ids=["multi-day", "exactly-a-day", "just-under-a-day", "minutes", "expired", "no-reset"],
)
async def test_reset_countdown_shows_days_from_a_day_and_an_unknown_share_once_past(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
    resets_in: timedelta | None,
    expected: str,
) -> None:
    clock = FakeClock(_START)
    _record_run(connection, clock, "claude", _SUBSCRIPTION, _tokens(None, COST_BASIS_UNKNOWN))
    QuotaStore(connection).record(
        "claude",
        QuotaWindow(
            "7d",
            50.0,
            _START + resets_in if resets_in is not None else None,
            _START - _OBSERVED_AGO,
        ),
    )
    panel = UsagePanel(widget_context(reader, write_minimal_project(tmp_path), clock))
    async with WidgetHostApp(panel).run_test(size=_WIDE):
        await _settled_lines(panel)
        text = "\n".join(_plain_lines(panel))
        assert expected in text


async def test_an_expired_full_window_is_drawn_as_unknown_and_keeps_its_stored_reading(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    clock = FakeClock(_START)
    _record_run(connection, clock, "claude", _SUBSCRIPTION, _tokens(None, COST_BASIS_UNKNOWN))
    _quota(connection, "claude", "5h", 100.0, -timedelta(minutes=5))
    panel = UsagePanel(widget_context(reader, write_minimal_project(tmp_path), clock))
    async with WidgetHostApp(panel).run_test(size=_WIDE):
        await _settled_lines(panel)
        text = "\n".join(_plain_lines(panel))
        assert f"5h {GAUGE_GLYPHS[0]} --% resets unknown" in text
        assert "100%" not in text
        assert reader.list_quotas(["claude"])["claude"][0].used_pct == 100.0
