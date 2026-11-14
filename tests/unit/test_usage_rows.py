from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest
from rich.console import Console

from shikiru.dashboard.contract import TaskSessionView
from shikiru.dashboard.usage_body import Column, UsageBody, usage_rows
from shikiru.domain.levels import UsageLevel
from shikiru.ui.section import BOLD_STEEL
from shikiru.ui.tokens import SECONDARY_TEXT_COLOR

if TYPE_CHECKING:
    from rich.console import Group
    from rich.text import Text

DASH = "\N{EM DASH}"


def _session(role: str, model: str, **fields: Any) -> TaskSessionView:
    values: dict[str, Any] = {
        "role": role,
        "agent": "claude",
        "model": model,
        "effort": None,
        "session_id": None,
        "status": "live",
        "uncached_input_tokens": None,
        "output_tokens": None,
        "cache_read_tokens": None,
        "cache_write_tokens": None,
        "cost_usd": None,
        "cost_basis": "billed",
        "context_used_pct": None,
        **fields,
    }
    return TaskSessionView(**values)


SESSIONS = (
    _session(
        "implementer",
        "a-rather-long-model-name",
        status="closed",
        current_lineage=False,
        usage_reported=True,
        uncached_input_tokens=200_000,
        cache_read_tokens=40_000,
        cache_write_tokens=10_000,
        output_tokens=50_000,
        cost_usd=0.0,
    ),
    _session(
        "leader",
        "opus-5",
        usage_reported=True,
        uncached_input_tokens=800_000,
        cache_read_tokens=150_000,
        cache_write_tokens=50_000,
        output_tokens=100_000,
        cost_usd=0.4,
        context_used_pct=42.4,
        context_level=UsageLevel.WARN,
    ),
    _session("implementer", "sonnet-5", usage_reported=False, context_used_pct=71),
    _session(
        "implementer",
        "sub-model",
        usage_reported=True,
        uncached_input_tokens=5,
        cache_read_tokens=0,
        cache_write_tokens=0,
        output_tokens=0,
        cost_usd=0.9,
        billing_mode="subscription",
    ),
)

WIDTH_COLUMNS = (
    (100, (Column.ROLE, Column.MODEL, Column.PROCESSED, Column.COST, Column.CONTEXT)),
    (44, (Column.ROLE, Column.PROCESSED, Column.COST, Column.CONTEXT)),
    (34, (Column.ROLE, Column.PROCESSED)),
)


def test_usage_table_orders_current_first_humanises_values_and_drops_columns_by_width() -> None:
    table = usage_rows(SESSIONS, 100)

    assert [row.cells[Column.MODEL] for row in table.rows] == [
        "opus-5",
        "sonnet-5",
        "sub-model",
        "a-rather-long-model-name",
    ]
    assert [row.old for row in table.rows] == [False, False, False, True]
    assert [row.cells[Column.PROCESSED] for row in table.rows] == [
        "1.1M",
        DASH,
        "5",
        "300k",
    ]
    assert [row.cells[Column.COST] for row in table.rows] == ["$0.40", DASH, DASH, "$0.00"]
    assert [row.cells[Column.CONTEXT] for row in table.rows] == ["42%", DASH, DASH, DASH]
    assert table.rows[0].level is UsageLevel.WARN
    for width, columns in WIDTH_COLUMNS:
        assert usage_rows(SESSIONS, width).columns == columns
    assert usage_rows(SESSIONS, 60).widths[Column.MODEL] < len(SESSIONS[0].model)


@pytest.mark.parametrize(
    ("categories", "processed"),
    [
        ((1_200, 300, 40, 500), "2.04k"),
        ((1_200, None, 40, 500), DASH),
        ((None, 300, 40, 500), DASH),
        ((1_200, 300, None, 500), DASH),
        ((1_200, 300, 40, None), DASH),
    ],
)
def test_processed_cell_shows_a_sum_only_when_every_category_is_reported(
    categories: tuple[int | None, ...], processed: str
) -> None:
    uncached, cache_read, cache_write, output = categories
    session = _session(
        "leader",
        "opus-5",
        usage_reported=True,
        uncached_input_tokens=uncached,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        output_tokens=output,
    )

    assert usage_rows((session,), 100).rows[0].cells[Column.PROCESSED] == processed


def test_processed_cell_labels_ambiguous_legacy_usage() -> None:
    session = _session("leader", "old-model", legacy_token_totals=True)

    assert usage_rows((session,), 100).rows[0].cells[Column.PROCESSED] == "legacy"


def _heading_text(view: Any) -> Text:
    console = Console(width=100)
    body = UsageBody(view)
    group = cast("Group", next(iter(body.__rich_console__(console, console.options))))
    return cast("Text", group.renderables[0])


def test_usage_body_starts_with_the_processed_total_and_its_cost() -> None:
    view: Any = SimpleNamespace(usage=object(), sessions=SESSIONS)
    console = Console(width=100, record=True, color_system=None)

    console.print(UsageBody(view))

    first = console.export_text().splitlines()[0]
    assert first.startswith("$")
    assert first.endswith("processed tokens (partial)")
    heading = _heading_text(view)
    painted = {(heading.plain[span.start : span.end], str(span.style)) for span in heading.spans}
    assert (
        first.split(" · ")[-1].removesuffix(" processed tokens (partial)"),
        BOLD_STEEL,
    ) in painted
    assert (" processed tokens (partial)", SECONDARY_TEXT_COLOR) in painted


def test_usage_body_labels_each_category_and_shows_unreported_ones_as_unknown() -> None:
    session = _session(
        "leader",
        "opus-5",
        usage_reported=True,
        uncached_input_tokens=1_200,
        cache_read_tokens=None,
        cache_write_tokens=None,
        output_tokens=300,
    )
    view: Any = SimpleNamespace(usage=object(), sessions=(session,))
    console = Console(width=100, record=True, color_system=None)

    console.print(UsageBody(view))

    text = console.export_text()
    assert f"uncached input 1.2k · cache read {DASH} · cache write {DASH} · output 300" in text
    assert "processed tokens" not in text
    assert "Processed = uncached input + cache read + cache write + output." in text
