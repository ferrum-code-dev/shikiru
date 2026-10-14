from typing import Any

from shikiru.dashboard.contract import TaskSessionView
from shikiru.dashboard.usage_body import Column, usage_rows
from shikiru.domain.levels import UsageLevel

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
    (34, (Column.ROLE, Column.TOKENS, Column.COST)),
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
    assert [row.cells[Column.TOKENS] for row in table.rows] == ["1.1M", DASH, "5", "300.0k"]
    assert [row.cells[Column.COST] for row in table.rows] == ["$0.40", DASH, DASH, "$0.00"]
    assert [row.cells[Column.CONTEXT] for row in table.rows] == ["42%", DASH, DASH, DASH]
    assert table.rows[0].level is UsageLevel.WARN
    for width, columns in WIDTH_COLUMNS:
        assert usage_rows(SESSIONS, width).columns == columns
    assert usage_rows(SESSIONS, 60).widths[Column.MODEL] < len(SESSIONS[0].model)
