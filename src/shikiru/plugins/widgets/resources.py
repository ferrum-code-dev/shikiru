"""The `resources` widget: host and Shikiru process-tree usage."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console

from shikiru.dashboard.contract import FOCUS_PROJECT_DIAGNOSTICS_VIEW, WidgetPlacement
from shikiru.ui.compact import render_resource_metric
from shikiru.ui.gated_static import GatedStatic
from shikiru.ui.screen import widget_is_attached

RESOURCE_ENTER_KEY = "enter"

if TYPE_CHECKING:
    from rich.text import Text
    from textual import events
    from textual.widget import Widget

    from shikiru.dashboard.contract import ResourceView, WidgetContext


class ResourcesPanel(GatedStatic):
    """Show host use and the engine plus every pane process tree."""

    def __init__(self, context: WidgetContext) -> None:
        """Hold `context`; resource sampling starts on mount."""
        super().__init__(id="resources")
        self.can_focus = context.placement is WidgetPlacement.RESOURCES
        self._widget_context = context
        self._sample: ResourceView | None = None
        self._applied_min_height: int | None = None

    async def on_mount(self) -> None:
        """Render and resample resources at the configured cadence."""
        await self._refresh()
        if self._widget_context.placement is WidgetPlacement.RESOURCES:
            resources = self._widget_context.settings.current().orchestration.resources
            self.set_interval(resources.sample_seconds, self._refresh)

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
        sample = await self._widget_context.views.resources()
        if not widget_is_attached(self):
            return
        self._sample = sample
        self._render_sample_at_current_width()

    def _render_sample_at_current_width(self) -> None:
        sample = self._sample
        if sample is None:
            return
        self._update_content(render_resource_metric(sample, width=self.size.width))

    def _update_content(self, content: Text) -> None:
        self.update_if_changed(content)
        if self.size.width:
            rendered_lines = content.wrap(Console(), self.size.width)
            min_height = len(rendered_lines)
            if min_height != self._applied_min_height:
                self._applied_min_height = min_height
                self.styles.min_height = min_height
                if self.parent is not None:
                    self.parent.refresh(layout=True)


class ResourcesWidget:
    """The `resources` dashboard widget plugin."""

    name = "resources"
    placement = WidgetPlacement.RESOURCES

    def create(self, context: WidgetContext) -> Widget:
        """Build the resource panel for `context`."""
        return ResourcesPanel(context)
