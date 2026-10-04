"""The `usage` widget: token totals, reported costs and quota windows (06 §5.4)."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from rich.console import Group, RenderableType
from rich.text import Text
from textual.widgets import Static

from shikiru.ui import Gauge
from shikiru.ui.compact import ProjectUsageMetric

if TYPE_CHECKING:
    from collections.abc import Callable

    from textual.app import App
    from textual.widget import Widget

    from shikiru.dashboard.contract import AgentUsageView, WidgetContext

UNKNOWN_VALUE = "\N{EN DASH}"


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
        else:
            coverage = f"usage reported {usage.usage_reported_runs}/{usage.run_count} runs"
            if usage.legacy_unknown_token_runs:
                coverage += f" · legacy provenance unknown {usage.legacy_unknown_token_runs}"
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
    lines.extend(
        Text.assemble(
            "  ",
            Gauge(quota.name, quota.used_pct).render(),
            f" · reset {quota.resets_in} · observed {quota.observed_ago}",
        )
        for quota in usage.quotas
    )
    return Group(*lines)


class UsagePanel(Static):
    """Show token totals and quota gauges for every known agent."""

    def __init__(self, context: WidgetContext) -> None:
        """Hold `context`; usage refreshes after state changes and for live countdowns."""
        super().__init__(id="usage")
        self._widget_context = context
        self._remove_motion_callback: Callable[[], None] | None = None

    async def on_mount(self) -> None:
        """Render current usage, subscribe to state and tick quota age/countdowns."""
        await self._refresh()
        self.run_worker(self._widget_context.on_change(self._refresh_while_active), exclusive=True)
        self._remove_motion_callback = self._widget_context.on_motion(self._refresh_while_active)

    def on_unmount(self) -> None:
        if self._remove_motion_callback is not None:
            self._remove_motion_callback()
            self._remove_motion_callback = None

    async def _refresh(self) -> None:
        usage = self._widget_context.views.usage(self._widget_context.focus_task)
        scope = (
            f"Task recorded usage · {self._widget_context.focus_task}"
            if self._widget_context.focus_task is not None
            else "Project recorded usage · all retained runs"
        )
        lines: list[RenderableType] = []
        if self._widget_context.focus_task is None:
            lines.insert(
                0, ProjectUsageMetric(self._widget_context.views.project_usage(), scope).render()
            )
        else:
            lines.append(Text(scope, style="bold"))
        lines.extend(_agent_block(item) for item in usage)
        if not usage:
            lines.append(Text("No runs or quota windows recorded."))
        self.update(Group(*lines))

    async def _refresh_while_active(self) -> None:
        if not self.is_mounted:
            return
        app = cast("App[None]", self.app)
        if app.is_running and self.screen.is_current:
            await self._refresh()


class UsageWidget:
    """The `usage` dashboard widget plugin (06 §5.4)."""

    name = "usage"

    def create(self, context: WidgetContext) -> Widget:
        """Build the usage panel for `context`."""
        return UsagePanel(context)
