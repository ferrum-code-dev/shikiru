"""The `usage` widget: token totals, reported costs and quota windows."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from rich.console import Group, RenderableType
from rich.text import Text

from shikiru.dashboard.contract import WidgetPlacement
from shikiru.ui.compact import (
    USAGE_BY_AGENT_LABEL,
    USAGE_LIMITS_LABEL,
    AgentQuotaLimits,
    AgentUsageRows,
    ProjectUsageMetric,
    ProjectUsageTotals,
)
from shikiru.ui.gated_static import GatedStatic
from shikiru.ui.glyphs import GLYPH_QUOTA
from shikiru.ui.screen import widget_is_attached, widget_is_current
from shikiru.ui.section import Section
from shikiru.ui.tokens import AMBER, STYLE_BOLD

if TYPE_CHECKING:
    from collections.abc import Callable

    from textual import events
    from textual.app import App
    from textual.widget import Widget

    from shikiru.dashboard.contract import (
        AgentUsageView,
        QuotaBlockView,
        RecordedUsageView,
        WidgetContext,
    )

ENTER_KEY = "enter"
NO_USAGE_RECORDED = "No runs or quota windows recorded."
TASK_USAGE_UNAVAILABLE = "Task usage coverage unavailable"


def _banner_texts(blocks: list[QuotaBlockView]) -> list[Text]:
    return [Text(f"{GLYPH_QUOTA} {block.banner}", style=AMBER) for block in blocks]


def _usage_body(
    totals: RecordedUsageView,
    agents: list[AgentUsageView],
    context: WidgetContext,
    scope: str | None,
    blocks: list[QuotaBlockView],
) -> Group:
    parts: list[RenderableType] = [] if scope is None else [Text(scope, style=STYLE_BOLD)]
    parts.extend(_banner_texts(blocks))
    parts.append(ProjectUsageTotals(totals))
    with_runs = [agent for agent in agents if agent.run_count]
    with_quotas = [agent for agent in agents if agent.quotas]
    if with_runs:
        parts.append(Section(USAGE_BY_AGENT_LABEL, AgentUsageRows(with_runs)))
    if with_quotas:
        gauges = context.settings.current().dashboard.gauges
        limits = AgentQuotaLimits(with_quotas, gauges.quota_warn_pct, gauges.quota_alert_pct)
        parts.append(Section(USAGE_LIMITS_LABEL, limits))
    if not agents:
        parts.append(Text(NO_USAGE_RECORDED))
    return Group(*parts)


class UsagePanel(GatedStatic):
    """Show token totals and quota gauges for every known agent."""

    def __init__(self, context: WidgetContext) -> None:
        """Hold `context`; usage refreshes after state changes and for live countdowns."""
        super().__init__(id="usage")
        self.can_focus = context.placement is WidgetPlacement.PROJECT_USAGE
        self._widget_context = context
        self._rendered: (
            tuple[RecordedUsageView, list[AgentUsageView], list[QuotaBlockView]] | None
        ) = None
        self._home_usage: RecordedUsageView | None = None
        self._home_blocks: list[QuotaBlockView] = []
        self._remove_motion_callback: Callable[[], None] | None = None

    async def on_mount(self) -> None:
        """Render current usage, subscribe to state and tick quota age/countdowns."""
        await self._refresh()
        self.run_worker(self._widget_context.on_change(self._refresh_if_attached), exclusive=True)
        self._remove_motion_callback = self._widget_context.on_motion(self._refresh_on_motion)

    def on_unmount(self) -> None:
        if self._remove_motion_callback is not None:
            self._remove_motion_callback()
            self._remove_motion_callback = None

    def on_resize(self, event: events.Resize) -> None:
        del event
        if self._home_usage is not None:
            self._render_home_usage(self._home_usage, self._home_blocks)

    async def on_click(self, event: events.Click) -> None:
        if self._widget_context.placement is WidgetPlacement.PROJECT_USAGE:
            open_view = self._widget_context.open_contextual_view
            if open_view is not None:
                await open_view("usage")
                event.stop()

    async def on_key(self, event: events.Key) -> None:
        if (
            event.key == ENTER_KEY
            and self._widget_context.placement is WidgetPlacement.PROJECT_USAGE
            and self._widget_context.open_contextual_view is not None
        ):
            await self._widget_context.open_contextual_view("usage")
            event.stop()

    async def _refresh(self, *, motion: bool = False) -> None:
        if self._widget_context.placement is WidgetPlacement.PROJECT_USAGE:
            self._home_usage = self._widget_context.views.project_usage()
            self._home_blocks = self._widget_context.views.quota_blocks()
            self._render_home_usage(self._home_usage, self._home_blocks)
            return
        usage = self._widget_context.views.usage(self._widget_context.focus_task)
        if self._widget_context.focus_task is None:
            project_usage = self._widget_context.views.project_usage()
            blocks = self._widget_context.views.quota_blocks()
            rendering = (project_usage, usage, blocks)
            if motion and rendering == self._rendered:
                return
            self._rendered = rendering
            body = _usage_body(project_usage, usage, self._widget_context, None, blocks)
        else:
            detail = await self._widget_context.views.task_detail(
                self._widget_context.focus_task,
            )
            if not widget_is_attached(self):
                return
            if detail is None or detail.usage is None:
                body = Group(Text(TASK_USAGE_UNAVAILABLE, style=STYLE_BOLD))
            else:
                scope = f"Task {self._widget_context.focus_task}"
                body = _usage_body(detail.usage, usage, self._widget_context, scope, [])
        self.update_if_changed(body)

    def _render_home_usage(self, usage: RecordedUsageView, blocks: list[QuotaBlockView]) -> None:
        metric = ProjectUsageMetric(usage).render_home(width=self.size.width or None)
        self.update_if_changed(Group(*_banner_texts(blocks), metric))

    async def _refresh_if_active(self, *, motion: bool = False) -> None:
        if widget_is_current(self) and cast("App[None]", self.app).is_running:
            await self._refresh(motion=motion)

    async def _refresh_if_attached(self) -> None:
        if widget_is_attached(self):
            await self._refresh()

    async def _refresh_on_motion(self) -> None:
        if self._widget_context.focus_task is None and self._needs_motion_refresh():
            await self._refresh_if_active(motion=True)

    def _needs_motion_refresh(self) -> bool:
        if self._widget_context.placement is WidgetPlacement.PROJECT_USAGE:
            return bool(self._home_blocks)
        return True


class UsageWidget:
    """The `usage` dashboard widget plugin."""

    name = "usage"
    placement = WidgetPlacement.PROJECT_USAGE

    def create(self, context: WidgetContext) -> Widget:
        """Build the usage panel for `context`."""
        return UsagePanel(context)
