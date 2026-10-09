"""Project-level diagnostics from the dashboard's existing observations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text
from textual.containers import VerticalGroup
from textual.widgets import Static

from shikiru.ui.compact import render_resource_metric
from shikiru.ui.screen import widget_is_attached
from shikiru.ui.tokens import SECONDARY_TEXT_COLOR, STEEL_BLUE

if TYPE_CHECKING:
    from textual import events
    from textual.app import ComposeResult
    from textual.widget import Widget
    from textual.worker import Worker

    from shikiru.dashboard.contract import (
        DashboardViews,
        EngineFaultView,
        OnChangeRegistration,
        ResourceView,
    )

DIAGNOSTICS_REFRESH_GROUP = "project-diagnostics-refresh"
RESOURCE_PANEL_ID = "resources"
RESOURCE_SCOPE_ID = "project-diagnostics-scope"
RESOURCE_DETAILS_ID = "project-diagnostics-resource-details"
ENGINE_LOCK_ID = "project-diagnostics-engine-lock"
ENGINE_FAULTS_ID = "project-diagnostics-engine-faults"
DOCKER_FACTS_ID = "project-diagnostics-docker"
RESOURCE_GUIDANCE = (
    "CPU % of host capacity\nHost RAM used/total\nProcess RSS/host total\nShikiru excludes agents."
)
RESOURCE_SCOPE = "Scope · current Shikiru project"
ENGINE_LOCK_HELD = "Engine lock · held"
ENGINE_LOCK_NOT_HELD = "Engine lock · not held"
ENGINE_FAULTS_NONE = "Open engine faults · 0 recorded"
DOCKER_NOT_CONFIGURED = "Docker · not configured"
UNKNOWN_SAMPLE_AGE = "sample age unavailable"
RECORDED_REPEATS = "repeats"
FIRST_RECORDED = "First recorded"
LATEST_RECORDED = "latest"
REFRESH_TARGET_IDS = (
    RESOURCE_PANEL_ID,
    RESOURCE_DETAILS_ID,
    ENGINE_LOCK_ID,
    ENGINE_FAULTS_ID,
)


class ProjectDiagnosticsPanel(VerticalGroup):
    """Show cached project resources, engine lock, open faults and configured Docker facts."""

    def __init__(
        self,
        views: DashboardViews,
        on_change: OnChangeRegistration,
        docker_widget: Widget | None,
    ) -> None:
        """Use existing dashboard projections and the configured Docker widget."""
        super().__init__(id="project-diagnostics")
        self._views = views
        self._on_change = on_change
        self._docker_widget = docker_widget
        self._resource: ResourceView | None = None
        self._change_worker: Worker[None] | None = None

    def compose(self) -> ComposeResult:
        """Build the project scope and independent diagnostic fact rows."""
        yield Static(RESOURCE_SCOPE, id=RESOURCE_SCOPE_ID, markup=False)
        yield Static("", id=RESOURCE_PANEL_ID, markup=False)
        yield Static("", id=RESOURCE_DETAILS_ID, markup=False)
        yield Static("", id=ENGINE_LOCK_ID, markup=False)
        yield Static("", id=ENGINE_FAULTS_ID, markup=False)
        if self._docker_widget is None:
            yield Static(DOCKER_NOT_CONFIGURED, id=DOCKER_FACTS_ID, markup=False)
        else:
            yield self._docker_widget

    def on_mount(self) -> None:
        """Render existing resource, engine-lock and open-fault observations."""
        self._refresh_project_facts()
        self._change_worker = self.run_worker(
            self._on_change(self._refresh_project_facts_if_attached),
            group=DIAGNOSTICS_REFRESH_GROUP,
        )

    def on_unmount(self) -> None:
        if self._change_worker is not None:
            self._change_worker.cancel()

    def on_resize(self, event: events.Resize) -> None:
        del event
        if self.is_mounted:
            self._update_resource()

    async def _refresh_project_facts_if_attached(self) -> None:
        if self._ready_for_refresh():
            self._refresh_project_facts()

    def _refresh_project_facts(self) -> None:
        if not self._ready_for_refresh():
            return
        self._resource = self._views.resource_sample()
        engine_up = self._views.engine_up()
        faults = self._views.engine_faults()
        self._update_resource()
        self.query_one(f"#{ENGINE_LOCK_ID}", Static).update(
            ENGINE_LOCK_HELD if engine_up else ENGINE_LOCK_NOT_HELD
        )
        self.query_one(f"#{ENGINE_FAULTS_ID}", Static).update(_faults_content(faults))

    def _update_resource(self) -> None:
        if not self._ready_for_refresh():
            return
        resource_panel = self.query_one(f"#{RESOURCE_PANEL_ID}", Static)
        width = resource_panel.size.width
        resource_panel.update(render_resource_metric(self._resource, width=width or None))
        details = Text()
        details.append_text(_resource_observation(self._resource))
        details.append("\n")
        details.append(RESOURCE_GUIDANCE, style=SECONDARY_TEXT_COLOR)
        self.query_one(f"#{RESOURCE_DETAILS_ID}", Static).update(details)

    def _ready_for_refresh(self) -> bool:
        return widget_is_attached(self) and all(
            self.query(f"#{widget_id}") for widget_id in REFRESH_TARGET_IDS
        )


def _resource_observation(sample: ResourceView | None) -> Text:
    if sample is None:
        return Text(
            "Resource sample unavailable · no observation recorded", style=SECONDARY_TEXT_COLOR
        )
    if sample.sampled_ago is not None:
        return Text(
            f"Resource sample observed {sample.sampled_ago} ago", style=SECONDARY_TEXT_COLOR
        )
    if sample.sampled_at is None:
        return Text("Resource sample time unavailable", style=SECONDARY_TEXT_COLOR)
    return Text(UNKNOWN_SAMPLE_AGE, style=SECONDARY_TEXT_COLOR)


def _faults_content(faults: tuple[EngineFaultView, ...]) -> Text:
    if not faults:
        return Text(ENGINE_FAULTS_NONE, style=SECONDARY_TEXT_COLOR)
    content = Text(f"Open engine faults · {len(faults)} recorded\n", style=STEEL_BLUE)
    for index, fault in enumerate(faults):
        target = fault.task_id or "project"
        recoverability = "recoverable" if fault.recoverable else "unrecoverable"
        content.append(
            f"{target} · {fault.scope} · {fault.error} · {recoverability} · "
            f"{RECORDED_REPEATS} {fault.repeats}\n"
        )
        content.append(f"{FIRST_RECORDED} {fault.first_seen} · {LATEST_RECORDED} {fault.last_seen}")
        if index < len(faults) - 1:
            content.append("\n")
    return content
