"""Project-level diagnostics from the dashboard's existing observations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.containers import VerticalGroup

from shikiru.dashboard.diagnostics_sections import engine_section, resources_section
from shikiru.ui.gated_static import GatedStatic
from shikiru.ui.screen import widget_is_attached

if TYPE_CHECKING:
    from collections.abc import Callable

    from textual.app import ComposeResult
    from textual.widget import Widget
    from textual.worker import Worker

    from shikiru.dashboard.contract import DashboardViews, OnChangeRegistration

DIAGNOSTICS_REFRESH_GROUP = "project-diagnostics-refresh"
RESOURCES_SECTION_ID = "project-diagnostics-resources"
ENGINE_SECTION_ID = "project-diagnostics-engine"


class ProjectDiagnosticsPanel(VerticalGroup):
    """Show the cached Resources and Engine sections above every configured diagnostics widget."""

    DEFAULT_CSS = """
    ProjectDiagnosticsPanel { margin-top: 1; }
    """

    def __init__(
        self,
        views: DashboardViews,
        on_change: OnChangeRegistration,
        widgets: tuple[Widget, ...],
    ) -> None:
        """Use existing dashboard projections and the configured diagnostics widgets in order."""
        super().__init__(id="project-diagnostics")
        self._views = views
        self._on_change = on_change
        self._widgets = widgets
        self._resources = GatedStatic(id=RESOURCES_SECTION_ID, markup=False)
        self._engine = GatedStatic(id=ENGINE_SECTION_ID, markup=False)
        self._change_worker: Worker[None] | None = None
        self._unsubscribe_resources: Callable[[], None] | None = None

    def compose(self) -> ComposeResult:
        """Build the permanent sections, then the configured diagnostics widgets."""
        yield self._resources
        yield self._engine
        yield from self._widgets

    def on_mount(self) -> None:
        """Render the cached observations and follow the Resources bar's refreshes and commits."""
        self._refresh()
        self._unsubscribe_resources = self._views.on_resources_refreshed(self._refresh)
        self._change_worker = self.run_worker(
            self._on_change(self._refresh_if_attached),
            group=DIAGNOSTICS_REFRESH_GROUP,
        )

    def on_unmount(self) -> None:
        if self._unsubscribe_resources is not None:
            self._unsubscribe_resources()
            self._unsubscribe_resources = None
        if self._change_worker is not None:
            self._change_worker.cancel()

    async def _refresh_if_attached(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        if not widget_is_attached(self):
            return
        sample = self._views.resource_sample()
        status = self._views.engine_status()
        fault_count = len(self._views.engine_faults())
        self._resources.update_if_changed(resources_section(sample), key=sample)
        self._engine.update_if_changed(
            engine_section(status, fault_count), key=(status, fault_count)
        )
