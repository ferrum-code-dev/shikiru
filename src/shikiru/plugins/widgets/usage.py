"""The `usage` widget: token totals, reported costs and quota windows (06 §5.4)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Group
from rich.text import Text
from textual.widgets import Static

from shikiru.ui import Gauge

if TYPE_CHECKING:
    from textual.widget import Widget

    from shikiru.dashboard.contract import AgentUsageView, WidgetContext

USAGE_REFRESH_SECONDS = 1
UNKNOWN_VALUE = "\N{EN DASH}"
API_EQUIVALENT_LABEL = "API-equivalent"


def _agent_block(usage: AgentUsageView) -> Group:
    cost = _cost_text(usage)
    lines = [
        Text(
            f"{usage.agent} · input {usage.input_tokens:,} · output {usage.output_tokens:,} · "
            f"cache read {usage.cache_read_tokens:,} · cache write {usage.cache_write_tokens:,} · "
            f"cost {cost}",
            style="bold",
        )
    ]
    lines.extend(
        Text.assemble(
            "  ",
            Gauge(quota.name, quota.used_pct).render(),
            f" · reset {quota.resets_in} · observed {quota.observed_ago}",
        )
        for quota in usage.quotas
    )
    return Group(*lines)


def _cost_text(usage: AgentUsageView) -> str:
    if usage.cost_usd is None:
        if usage.cost_basis == "unknown":
            return UNKNOWN_VALUE
        return f"{UNKNOWN_VALUE} ({usage.cost_basis})"
    amount = f"${usage.cost_usd:.2f}"
    if usage.cost_basis == "api_equivalent":
        return f"{amount} {API_EQUIVALENT_LABEL}"
    if usage.cost_basis == "billed":
        return f"{amount} billed"
    message = f"a priced cost cannot be displayed with basis {usage.cost_basis!r}"
    raise ValueError(message)


class UsagePanel(Static):
    """Show token totals and quota gauges for every known agent."""

    def __init__(self, context: WidgetContext) -> None:
        """Hold `context`; usage refreshes after state changes and for live countdowns."""
        super().__init__(id="usage")
        self._widget_context = context

    async def on_mount(self) -> None:
        """Render current usage, subscribe to state and tick quota age/countdowns."""
        await self._refresh()
        self.run_worker(self._widget_context.on_change(self._refresh), exclusive=True)
        self.set_interval(USAGE_REFRESH_SECONDS, self._refresh)

    async def _refresh(self) -> None:
        usage = self._widget_context.views.usage()
        self.update(
            Group(*[_agent_block(item) for item in usage]) if usage else "no usage reported"
        )


class UsageWidget:
    """The `usage` dashboard widget plugin (06 §5.4)."""

    name = "usage"

    def create(self, context: WidgetContext) -> Widget:
        """Build the usage panel for `context`."""
        return UsagePanel(context)
