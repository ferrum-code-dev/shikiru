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

UNKNOWN_VALUE = "\N{EN DASH}"
API_EQUIVALENT_LABEL = "API-equivalent"


def _agent_block(usage: AgentUsageView) -> Group:
    known_token_runs = usage.usage_reported_runs + usage.legacy_token_runs
    if known_token_runs:
        tokens = (
            f"input {usage.input_tokens:,} · output {usage.output_tokens:,} · "
            f"cache read {usage.cache_read_tokens:,} · cache write {usage.cache_write_tokens:,}"
        )
        if usage.legacy_token_runs:
            unknown_runs = usage.run_count - known_token_runs
            coverage = (
                f"usage reported {usage.usage_reported_runs} · legacy recorded "
                f"{usage.legacy_token_runs} · token usage unknown "
                f"{unknown_runs}/{usage.run_count} runs"
            )
            if usage.legacy_unknown_token_runs:
                coverage += f" · legacy provenance unknown {usage.legacy_unknown_token_runs}"
        elif usage.usage_reported_runs:
            coverage = f"usage reported {usage.usage_reported_runs}/{usage.run_count} runs"
            if usage.legacy_unknown_token_runs:
                coverage += f" · legacy provenance unknown {usage.legacy_unknown_token_runs}"
        elif usage.legacy_unknown_token_runs:
            coverage = (
                "legacy usage provenance unknown "
                f"{usage.legacy_unknown_token_runs}/{usage.run_count} runs"
            )
        else:
            coverage = (
                f"usage not reported (0/{usage.run_count} runs)"
                if usage.run_count
                else "no session usage recorded"
            )
    else:
        tokens = f"tokens {UNKNOWN_VALUE}"
        if usage.legacy_unknown_token_runs:
            coverage = (
                "legacy usage provenance unknown "
                f"{usage.legacy_unknown_token_runs}/{usage.run_count} runs"
            )
        else:
            coverage = (
                f"usage not reported (0/{usage.run_count} runs)"
                if usage.run_count
                else "no session usage recorded"
            )
    lines = [Text(f"{usage.agent} · {tokens} · {coverage}", style="bold")]
    if usage.run_count:
        lines.extend(_cost_lines(usage))
    lines.extend(
        Text.assemble(
            "  ",
            Gauge(quota.name, quota.used_pct).render(),
            f" · reset {quota.resets_in} · observed {quota.observed_ago}",
        )
        for quota in usage.quotas
    )
    return Group(*lines)


def _cost_lines(usage: AgentUsageView) -> list[Text]:
    return [
        Text(
            _basis_cost_text(
                "Billed",
                usage.billed_cost_usd,
                usage.billed_cost_runs,
                usage.legacy_billed_cost_runs,
                usage.run_count,
            )
        ),
        Text(
            _basis_cost_text(
                API_EQUIVALENT_LABEL,
                usage.api_equivalent_cost_usd,
                usage.api_equivalent_cost_runs,
                usage.legacy_api_equivalent_cost_runs,
                usage.run_count,
            )
        ),
        Text(_unknown_cost_text(usage)),
    ]


def _basis_cost_text(
    label: str,
    cost: float | None,
    runs: int,
    legacy_runs: int,
    total_runs: int,
) -> str:
    amount = UNKNOWN_VALUE if cost is None else f"${cost:.2f}"
    if total_runs == 0:
        coverage = "no runs"
    else:
        coverage = f"{runs}/{total_runs} runs"
        if runs < total_runs:
            coverage = f"partial · {coverage}"
    legacy = f" · legacy recorded {legacy_runs}" if legacy_runs else ""
    return f"{label} subtotal · {amount} · {coverage}{legacy}"


def _unknown_cost_text(usage: AgentUsageView) -> str:
    if usage.run_count == 0:
        return "No session runs recorded."
    return f"Unknown cost · {usage.unknown_cost_runs}/{usage.run_count} runs"


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
        self._widget_context.on_motion(self._refresh)

    async def _refresh(self) -> None:
        usage = self._widget_context.views.usage(self._widget_context.focus_task)
        scope = (
            f"Task recorded usage · {self._widget_context.focus_task}"
            if self._widget_context.focus_task is not None
            else "Project recorded usage · all retained runs"
        )
        self.update(
            Group(Text(scope, style="bold"), *[_agent_block(item) for item in usage])
            if usage
            else Group(Text(scope, style="bold"), Text("No runs or quota windows recorded."))
        )


class UsageWidget:
    """The `usage` dashboard widget plugin (06 §5.4)."""

    name = "usage"

    def create(self, context: WidgetContext) -> Widget:
        """Build the usage panel for `context`."""
        return UsagePanel(context)
