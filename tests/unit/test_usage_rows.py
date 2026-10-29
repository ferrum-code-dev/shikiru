from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

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
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
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
        input_tokens=250_000,
        output_tokens=50_000,
        cost_usd=0.0,
    ),
    _session(
        "leader",
        "opus-5",
        usage_reported=True,
        input_tokens=1_000_000,
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
        input_tokens=5,
        cost_usd=0.9,
        billing_mode="subscription",
    ),
)

WIDTH_COLUMNS = (
    (100, (Column.ROLE, Column.MODEL, Column.TOKENS, Column.COST, Column.CONTEXT)),
    (44, (Column.ROLE, Column.TOKENS, Column.COST, Column.CONTEXT)),
    (34, (Column.ROLE, Column.TOKENS)),
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
    assert [row.cells[Column.TOKENS] for row in table.rows] == [
        "1M / 100k",
        DASH,
        "5 / 0",
        "250k / 50k",
    ]
    assert [row.cells[Column.COST] for row in table.rows] == ["$0.40", DASH, DASH, "$0.00"]
    assert [row.cells[Column.CONTEXT] for row in table.rows] == ["42%", DASH, DASH, DASH]
    assert table.rows[0].level is UsageLevel.WARN
    for width, columns in WIDTH_COLUMNS:
        assert usage_rows(SESSIONS, width).columns == columns
    assert usage_rows(SESSIONS, 60).widths[Column.MODEL] < len(SESSIONS[0].model)


def _heading_text(view: Any) -> Text:
    console = Console(width=100)
    body = UsageBody(view)
    group = cast("Group", next(iter(body.__rich_console__(console, console.options))))
    return cast("Text", group.renderables[0])


def test_usage_body_starts_with_the_totals_not_its_fold_title() -> None:
    view: Any = SimpleNamespace(usage=object(), sessions=SESSIONS)
    console = Console(width=100, record=True, color_system=None)

    console.print(UsageBody(view))

    first = console.export_text().splitlines()[0]
    assert first.startswith("$")
    assert first.endswith("tokens")
    heading = _heading_text(view)
    painted = {(heading.plain[span.start : span.end], str(span.style)) for span in heading.spans}
    assert (first.split(" · ")[-1].removesuffix(" tokens"), BOLD_STEEL) in painted
    assert (" tokens", SECONDARY_TEXT_COLOR) in painted
