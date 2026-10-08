"""The `resources` widget: host and Shikiru process-tree usage (06 §5.4)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console
from rich.text import Text
from textual.widgets import Static

from shikiru.dashboard.contract import FOCUS_PROJECT_DIAGNOSTICS_VIEW, WidgetPlacement
from shikiru.ui.compact import render_resource_metric
from shikiru.ui.screen import widget_is_attached

RESOURCE_ENTER_KEY = "enter"
RESOURCE_TOP_BORDER_ROWS = 1

if TYPE_CHECKING:
    from textual import events
    from textual.widget import Widget

    from shikiru.dashboard.contract import ResourceView, WidgetContext


class ResourcesPanel(Static):
    """Show host use and the engine plus every pane process tree."""

    def __init__(self, context: WidgetContext) -> None:
        """Hold `context`; resource sampling starts on mount."""
        super().__init__(id="resources")
        self._widget_context = context
        self._sample: ResourceView | None = None

    async def on_mount(self) -> None:
        """Render and resample resources at the configured cadence."""
        await self._refresh()
        if self._widget_context.placement is WidgetPlacement.RESOURCES:
            self.set_interval(self._widget_context.resources_refresh_seconds, self._refresh)

    async def on_click(self, event: events.Click) -> None:
        if self._widget_context.placement is not WidgetPlacement.RESOURCES:
            return
        open_view = self._widget_context.open_contextual_view
        if open_view is not None:
            await open_view(FOCUS_PROJECT_DIAGNOSTICS_VIEW)
            event.stop()

    async def on_key(self, event: events.Key) -> None:
        if (
            event.key == RESOURCE_ENTER_KEY
            and self._widget_context.placement is WidgetPlacement.RESOURCES
            and self._widget_context.open_contextual_view is not None
        ):
            await self._widget_context.open_contextual_view(FOCUS_PROJECT_DIAGNOSTICS_VIEW)
            event.stop()

    def on_resize(self, event: events.Resize) -> None:
        del event
        if self._sample is not None:
            self._render_sample_at_current_width()

    async def _refresh(self) -> None:
        if not widget_is_attached(self):
            return
        if self._widget_context.placement is WidgetPlacement.DIAGNOSTICS:
            self._sample = self._widget_context.views.resource_sample()
            self._render_sample_at_current_width()
            return
        sample = await self._widget_context.views.resources()
        if not widget_is_attached(self):
            return
        self._sample = sample
        self._render_sample_at_current_width()

    def _render_sample_at_current_width(self) -> None:
        sample = self._sample
        if self._widget_context.placement is WidgetPlacement.DIAGNOSTICS:
            self._update_content(_diagnostics_content(sample, self.size.width))
            return
        if sample is None:
            return
        self._update_content(render_resource_metric(sample, width=self.size.width))

    def _update_content(self, content: Text) -> None:
        self.update(content)
        if self.size.width:
            rendered_lines = content.wrap(Console(), self.size.width)
            self.styles.min_height = len(rendered_lines) + RESOURCE_TOP_BORDER_ROWS
            if self.parent is not None:
                self.parent.refresh(layout=True)


class ResourcesWidget:
    """The `resources` dashboard widget plugin (06 §5.4)."""

    name = "resources"
    placement = WidgetPlacement.RESOURCES

    def create(self, context: WidgetContext) -> Widget:
        """Build the resource panel for `context`."""
        return ResourcesPanel(context)


def _diagnostics_content(sample: ResourceView | None, width: int) -> Text:
    metric = render_resource_metric(sample, width=width)
    age = f"Observed {sample.sampled_ago} ago. " if sample and sample.sampled_ago else ""
    explanation = (
        f"{age}CPU is percent of host capacity. Host RAM is used/total; Shikiru and Agents RAM "
        "are process RSS/host total, not a per-process limit. Shikiru excludes agent trees."
    )
    return Text.assemble(metric, "\n", Text(explanation))
