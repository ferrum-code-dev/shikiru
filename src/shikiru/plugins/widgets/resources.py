"""The `resources` widget: host and Shikiru process-tree usage (06 §5.4)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.widgets import Static

from shikiru.dashboard.contract import FOCUS_RESOURCES_VIEW, WidgetPlacement
from shikiru.ui.compact import render_resource_metric

RESOURCE_ENTER_KEY = "enter"

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
            await open_view(FOCUS_RESOURCES_VIEW)
            event.stop()

    async def on_key(self, event: events.Key) -> None:
        if (
            event.key == RESOURCE_ENTER_KEY
            and self._widget_context.placement is WidgetPlacement.RESOURCES
            and self._widget_context.open_contextual_view is not None
        ):
            await self._widget_context.open_contextual_view(FOCUS_RESOURCES_VIEW)
            event.stop()

    def on_resize(self, event: events.Resize) -> None:
        del event
        if self._sample is not None:
            self.update(render_resource_metric(self._sample, width=self.size.width))

    async def _refresh(self) -> None:
        if self._widget_context.placement is WidgetPlacement.DIAGNOSTICS:
            self._sample = self._widget_context.views.resource_sample()
            self.update(
                render_resource_metric(self._sample, width=self.size.width)
                if self._sample is not None
                else "No cached resource sample."
            )
            return
        self._sample = await self._widget_context.views.resources()
        self.update(render_resource_metric(self._sample, width=self.size.width))


class ResourcesWidget:
    """The `resources` dashboard widget plugin (06 §5.4)."""

    name = "resources"
    placement = WidgetPlacement.RESOURCES

    def create(self, context: WidgetContext) -> Widget:
        """Build the resource panel for `context`."""
        return ResourcesPanel(context)
