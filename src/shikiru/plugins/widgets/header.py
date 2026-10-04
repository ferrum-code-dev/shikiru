"""The `header` widget: project, engine state, slots and repo (docs/design/06-operator.md §5.4)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.widgets import Static

from shikiru.dashboard.contract import WidgetPlacement

if TYPE_CHECKING:
    from collections.abc import Callable

    from textual.widget import Widget

    from shikiru.dashboard.contract import HeaderView, WidgetContext, WorkspaceSummaryView


SECONDS_PER_MINUTE = 60
MINUTES_PER_HOUR = 60
SECONDS_PER_HOUR = SECONDS_PER_MINUTE * MINUTES_PER_HOUR


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
) -> str:
    uptime = (
        f"uptime {_duration_text(view.engine_uptime_seconds)}"
        if view.engine_uptime_seconds is not None
        else "uptime unknown"
    )
    engine = f"Engine running · {uptime}" if view.engine_up else "Engine stopped"
    if not view.engine_up and view.previous_engine_run_seconds is not None:
        engine = f"{engine} · previous {_duration_text(view.previous_engine_run_seconds)}"
    identity = (
        f"Shikiru · {view.project} · {engine} · slots {view.occupied_slots}/{view.maximum_slots}"
    )
    if view.integration_mode:
        identity = f"{identity} · integration {view.integration_mode}"
    repo = view.repo
    sampled = f" · sampled {repo.sampled_ago} ago" if repo.sampled_ago else ""
    repo_line = f"{repo.branch} · +{repo.ahead}/-{repo.behind} · {repo.dirty} dirty{sampled}"
    operations = (
        f"Running {summary.running} · Waiting {summary.waiting} · Attention {summary.attention}"
    )
    lines = [identity, operations, repo_line]
    if include_alerts:
        lines.extend(view.alerts)
    return "\n".join(lines)


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
        self.update(
            _header_text(
                view,
                summary,
                include_alerts=self._widget_context.placement is not WidgetPlacement.IDENTITY,
            )
        )

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
