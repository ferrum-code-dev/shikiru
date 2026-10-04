"""The `header` widget: project, engine state, slots and repo (docs/design/06-operator.md §5.4)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.cells import cell_len
from rich.text import Text
from textual.widgets import Static

from shikiru.dashboard.contract import WidgetPlacement
from shikiru.ui.tokens import ALERT_COLOR, SAGE, SECONDARY_TEXT_COLOR, STEEL_BLUE

if TYPE_CHECKING:
    from collections.abc import Callable

    from textual import events
    from textual.widget import Widget

    from shikiru.dashboard.contract import HeaderView, WidgetContext, WorkspaceSummaryView


SECONDS_PER_MINUTE = 60
MINUTES_PER_HOUR = 60
SECONDS_PER_HOUR = SECONDS_PER_MINUTE * MINUTES_PER_HOUR
MIN_HEADER_GAP = 2
MERGE_INTEGRATION = "merge"
PULL_REQUEST_INTEGRATION = "pr"


def _duration_text(seconds: float) -> str:
    total_seconds = int(seconds)
    hours, remainder = divmod(total_seconds, SECONDS_PER_HOUR)
    minutes, remainder = divmod(remainder, SECONDS_PER_MINUTE)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m"
    return f"{remainder}s"


def _header_text(
    view: HeaderView,
    summary: WorkspaceSummaryView,
    *,
    include_alerts: bool,
    width: int,
) -> str:
    uptime = (
        f"Uptime {_duration_text(view.engine_uptime_seconds)}"
        if view.engine_uptime_seconds is not None
        else "Uptime unknown"
    )
    engine = f"Engine running · {uptime}" if view.engine_up else "Engine stopped"
    if not view.engine_up and view.previous_engine_run_seconds is not None:
        engine = f"{engine} · previous {_duration_text(view.previous_engine_run_seconds)}"
    repo = view.repo
    identity = f"SHIKIRU  {view.project} / {repo.branch}"
    identity_width = cell_len(identity)
    engine_width = cell_len(engine)
    gap = " " * max(MIN_HEADER_GAP, width - identity_width - engine_width)
    checkout = "clean checkout" if repo.dirty == 0 else f"{repo.dirty} dirty"
    if repo.ahead or repo.behind:
        checkout = f"{checkout} · +{repo.ahead}/-{repo.behind}"
    integration = _integration_label(view.integration_mode)
    operations = (
        f"Slots {view.occupied_slots}/{view.maximum_slots} · Running {summary.running} · "
        f"Waiting {summary.waiting} · {integration} · {checkout}"
    )
    lines = [f"{identity}{gap}{engine}", operations]
    if include_alerts:
        lines.extend(view.alerts)
    return "\n".join(lines)


def _home_header_text(
    view: HeaderView,
    summary: WorkspaceSummaryView,
    *,
    include_alerts: bool,
    width: int,
) -> Text:
    uptime = (
        f"Uptime {_duration_text(view.engine_uptime_seconds)}"
        if view.engine_uptime_seconds is not None
        else "Uptime unknown"
    )
    engine = "Engine running" if view.engine_up else "Engine stopped"
    if not view.engine_up and view.previous_engine_run_seconds is not None:
        engine = f"{engine} · previous {_duration_text(view.previous_engine_run_seconds)}"
    identity = f"SHIKIRU  {view.project} / {view.repo.branch}"
    engine_metadata = f"{engine} · {uptime}"
    gap = " " * max(MIN_HEADER_GAP, width - cell_len(identity) - cell_len(engine_metadata))
    repo = view.repo
    checkout = "clean checkout" if repo.dirty == 0 else f"{repo.dirty} dirty"
    if repo.ahead or repo.behind:
        checkout = f"{checkout} · +{repo.ahead}/-{repo.behind}"
    operations = Text.assemble(
        ("Slots ", SECONDARY_TEXT_COLOR),
        f"{view.occupied_slots}/{view.maximum_slots} · ",
        ("Running ", SECONDARY_TEXT_COLOR),
        f"{summary.running} · ",
        ("Waiting ", SECONDARY_TEXT_COLOR),
        f"{summary.waiting} · {_integration_label(view.integration_mode)} · {checkout}",
    )
    rendered = Text.assemble(
        ("SHIKIRU", STEEL_BLUE),
        (f"  {view.project} / {view.repo.branch}", SECONDARY_TEXT_COLOR),
        gap,
        (engine, SAGE if view.engine_up else ALERT_COLOR),
        (f" · {uptime}", SECONDARY_TEXT_COLOR),
        "\n\n",
        operations,
    )
    if include_alerts:
        for alert in view.alerts:
            rendered.append("\n")
            rendered.append(alert, style=ALERT_COLOR)
    return rendered


def _integration_label(value: str) -> str:
    if value == MERGE_INTEGRATION:
        return "Local merge"
    if value == PULL_REQUEST_INTEGRATION:
        return "Pull request"
    return value


class HeaderPanel(Static):
    """Shows project, engine, task counts and checkout identity."""

    def __init__(self, context: WidgetContext) -> None:
        """Hold `context`; nothing here does I/O until `on_mount`."""
        super().__init__(id="header")
        self._widget_context = context
        self._remove_motion_callback: Callable[[], None] | None = None

    async def on_mount(self) -> None:
        """Render the current view, then refresh on every state change."""
        await self._refresh()
        self.run_worker(self._widget_context.on_change(self._refresh), exclusive=True)
        self._remove_motion_callback = self._widget_context.on_motion(self._refresh_if_active)

    def on_unmount(self) -> None:
        if self._remove_motion_callback is not None:
            self._remove_motion_callback()
            self._remove_motion_callback = None

    async def _refresh(self) -> None:
        view = await self._widget_context.views.header()
        summary = self._widget_context.views.workspace_summary()
        include_alerts = (
            self._widget_context.placement is not WidgetPlacement.IDENTITY
            or not self._widget_context.home_attention_present
        )
        if self._widget_context.placement is WidgetPlacement.IDENTITY:
            self.update(
                _home_header_text(
                    view,
                    summary,
                    include_alerts=include_alerts,
                    width=self.size.width,
                )
            )
            return
        self.update(
            _header_text(
                view,
                summary,
                include_alerts=include_alerts,
                width=self.size.width,
            )
        )

    async def on_resize(self, event: events.Resize) -> None:
        del event
        await self._refresh()

    async def _refresh_if_active(self) -> None:
        if self.is_mounted and self.is_on_screen and self.screen.is_active:
            await self._refresh()


class HeaderWidget:
    """The `header` dashboard widget plugin (06 §5.4)."""

    name = "header"
    placement = WidgetPlacement.IDENTITY

    def create(self, context: WidgetContext) -> Widget:
        """Build the `HeaderPanel` for `context`."""
        return HeaderPanel(context)
