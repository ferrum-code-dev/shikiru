"""The `usage` widget: token totals, reported costs and quota windows (06 §5.4)."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from rich.console import Group, RenderableType
from rich.text import Text
from textual.widgets import Static

from shikiru.ui import Gauge
from shikiru.ui.compact import ProjectAgentUsageDetails, ProjectUsageDetails, ProjectUsageMetric

if TYPE_CHECKING:
    from collections.abc import Callable

    from textual.app import App
    from textual.widget import Widget

    from shikiru.dashboard.contract import AgentUsageView, WidgetContext

UNKNOWN_VALUE = "\N{EN DASH}"


def _agent_block(usage: AgentUsageView) -> Group:
    lines: list[RenderableType] = [ProjectAgentUsageDetails(usage).render()]
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
        self.run_worker(self._widget_context.on_change(self._refresh_if_active), exclusive=True)
        self._remove_motion_callback = self._widget_context.on_motion(self._refresh_on_motion)

    def on_unmount(self) -> None:
        if self._remove_motion_callback is not None:
            self._remove_motion_callback()
            self._remove_motion_callback = None

    async def _refresh(self) -> None:
        usage = self._widget_context.views.usage(self._widget_context.focus_task)
        lines: list[RenderableType] = []
        if self._widget_context.focus_task is None:
            project_usage = self._widget_context.views.project_usage()
            lines.extend(
                (
                    ProjectUsageMetric(project_usage, "Project").render(),
                    ProjectUsageDetails(project_usage).render(),
                )
            )
        else:
            detail = await self._widget_context.views.task_detail(
                self._widget_context.focus_task,
                include_timeline=False,
            )
            if detail is None or detail.usage is None:
                lines.append(Text("Task usage coverage unavailable", style="bold"))
            else:
                lines.extend(
                    (
                        ProjectUsageMetric(
                            detail.usage,
                            f"Task {self._widget_context.focus_task}",
                        ).render(),
                        ProjectUsageDetails(detail.usage).render(),
                    )
                )
        lines.extend(_agent_block(item) for item in usage)
        if not usage:
            lines.append(Text("No runs or quota windows recorded."))
        self.update(Group(*lines))

    async def _refresh_if_active(self) -> None:
        if not self.is_mounted:
            return
        app = cast("App[None]", self.app)
        if app.is_running and self.screen.is_current:
            await self._refresh()

    async def _refresh_on_motion(self) -> None:
        if self._widget_context.focus_task is None:
            await self._refresh_if_active()


class UsageWidget:
    """The `usage` dashboard widget plugin (06 §5.4)."""

    name = "usage"

    def create(self, context: WidgetContext) -> Widget:
        """Build the usage panel for `context`."""
        return UsagePanel(context)
