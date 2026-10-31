"""The `header` widget: project, engine state, slots and repo."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from rich.cells import cell_len
from rich.text import Text

from shikiru.dashboard.contract import DiagnosticSampleState, WidgetPlacement
from shikiru.ui.age import format_duration
from shikiru.ui.gated_static import GatedStatic
from shikiru.ui.screen import widget_is_attached, widget_is_current
from shikiru.ui.tokens import (
    ALERT_COLOR,
    AMBER,
    PRIMARY_TEXT_COLOR,
    SAGE,
    SECONDARY_TEXT_COLOR,
    STEEL_BLUE,
    STYLE_BOLD,
    SUCCESS_COLOR,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from rich.console import Console, ConsoleOptions, RenderResult
    from textual import events
    from textual.widget import Widget

    from shikiru.dashboard.contract import HeaderView, WidgetContext, WorkspaceSummaryView


MIN_HEADER_GAP = 2
RUNTIME_SEPARATOR_WIDTH = 3
ENGINE_RUNNING_MARKER = "●"
ENGINE_STOPPED_MARKER = "○"
MERGE_INTEGRATION = "merge"
SQUASH_INTEGRATION = "squash"
PULL_REQUEST_INTEGRATION = "pr"


@dataclass(frozen=True, slots=True)
class _HeaderContent:
    view: HeaderView
    summary: WorkspaceSummaryView
    include_alerts: bool

    @property
    def render_key(self) -> object:
        return (
            replace(self.view, engine_uptime_seconds=None, previous_engine_run_seconds=None),
            self.summary,
            self.include_alerts,
            _runtime_label(self.view),
        )

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        del console
        yield _home_header_text(
            self.view,
            self.summary,
            include_alerts=self.include_alerts,
            width=options.max_width,
        )


def _home_header_text(
    view: HeaderView,
    summary: WorkspaceSummaryView,
    *,
    include_alerts: bool,
    width: int,
) -> Text:
    identity = f"SHIKIRU {view.project}"
    if view.repo.root is not None:
        identity = f"{identity} ({view.repo.root})"
    identity_width = cell_len(identity)
    engine_label = (
        f"{ENGINE_RUNNING_MARKER} Engine running"
        if view.engine_up
        else f"{ENGINE_STOPPED_MARKER} Engine stopped"
    )
    runtime_label = _runtime_label(view)
    runtime_width = cell_len(engine_label) + cell_len(runtime_label) + RUNTIME_SEPARATOR_WIDTH
    checkout = _checkout_text(view)
    compact_header = identity_width + runtime_width + MIN_HEADER_GAP > width
    identity_text = Text.assemble(
        ("SHIKIRU", f"{STYLE_BOLD} {STEEL_BLUE}"),
        (f" {view.project}", PRIMARY_TEXT_COLOR),
    )
    if view.repo.root is not None:
        identity_text.append(f" ({view.repo.root})", style=SECONDARY_TEXT_COLOR)
    rendered = _fold_text(identity_text, width)
    if compact_header:
        rendered.append("\n")
        rendered.append_text(_runtime_text(engine_label, runtime_label, view.engine_up))
        rendered.append("\n")
        rendered.append_text(checkout)
    else:
        gap = " " * max(MIN_HEADER_GAP, width - identity_width - runtime_width)
        rendered.append(gap)
        rendered.append_text(_runtime_text(engine_label, runtime_label, view.engine_up))
        rendered.append("\n")
        rendered.append_text(checkout)
    rendered.append("\n")
    if view.repo.state is DiagnosticSampleState.STALE:
        age = view.repo.sampled_ago or "age unavailable"
        rendered.append(f"Checkout stale · sampled {age}", style=SECONDARY_TEXT_COLOR)
        rendered.append("\n")
        if view.repo.error is not None:
            rendered.append(f"Checkout read failed · {view.repo.error}", style=ALERT_COLOR)
            rendered.append("\n")
    elif view.repo.state is DiagnosticSampleState.FAILED:
        rendered.append("Checkout unavailable", style=SECONDARY_TEXT_COLOR)
        if view.repo.error is not None:
            rendered.append(f" · {view.repo.error}", style=ALERT_COLOR)
        rendered.append("\n")
    rendered.append("\n")
    rendered.append_text(
        Text.assemble(
            ("Slots ", SECONDARY_TEXT_COLOR),
            f"{view.occupied_slots}/{view.maximum_slots} · ",
            ("Running ", SECONDARY_TEXT_COLOR),
            f"{summary.running} · ",
            ("Waiting ", SECONDARY_TEXT_COLOR),
            f"{summary.waiting}",
        )
    )
    rendered.append("\n")
    if include_alerts:
        for alert in view.alerts:
            rendered.append(alert, style=ALERT_COLOR)
            rendered.append("\n")
    return rendered


def _fold_text(value: Text, width: int) -> Text:
    if width <= 0:
        return value
    offsets: list[int] = []
    current_width = 0
    for index, character in enumerate(value.plain):
        character_width = cell_len(character)
        if current_width and current_width + character_width > width:
            offsets.append(index)
            current_width = 0
        current_width += character_width
    rendered = Text()
    for index, line in enumerate(value.divide(offsets)):
        if index:
            rendered.append("\n")
        rendered.append_text(line)
    return rendered


def _runtime_text(engine: str, runtime: str, running: bool) -> Text:
    rendered = Text(engine, style=SAGE if running else ALERT_COLOR)
    rendered.append(f" · {runtime}", style=SECONDARY_TEXT_COLOR)
    return rendered


def _runtime_label(view: HeaderView) -> str:
    if view.engine_up:
        if view.engine_uptime_seconds is None:
            return "Uptime unavailable"
        return f"Uptime {format_duration(view.engine_uptime_seconds)}"
    if view.previous_engine_run_seconds is None:
        return "Last unavailable"
    return f"Last {format_duration(view.previous_engine_run_seconds)}"


def _checkout_text(view: HeaderView) -> Text:
    repo = view.repo
    if repo.state is DiagnosticSampleState.FAILED or repo.branch is None:
        rendered = Text("branch unavailable", style=SECONDARY_TEXT_COLOR)
    elif repo.dirty == 0:
        state = " (previously clean)" if repo.state is DiagnosticSampleState.STALE else " (clean)"
        rendered = Text.assemble((repo.branch, PRIMARY_TEXT_COLOR), (state, PRIMARY_TEXT_COLOR))
    elif repo.dirty is None or repo.added_lines is None or repo.deleted_lines is None:
        rendered = Text(f"{repo.branch} (line counts unavailable)", style=AMBER)
    else:
        rendered = Text.assemble(
            (f"{repo.branch} (", AMBER),
            (f"+{repo.added_lines}", SUCCESS_COLOR),
            (" ", AMBER),
            (f"-{repo.deleted_lines}", ALERT_COLOR),
            (")", AMBER),
        )
    rendered.append(f" · {_integration_label(view.integration_mode)}", style=SECONDARY_TEXT_COLOR)
    if (
        repo.state is not DiagnosticSampleState.FAILED
        and repo.branch is not None
        and repo.dirty != 0
    ):
        if repo.binary_entries is None:
            rendered.append(" · binary count unavailable", style=SECONDARY_TEXT_COLOR)
        elif repo.binary_entries:
            rendered.append(f" · {repo.binary_entries} binary uncounted", style=AMBER)
        if repo.untracked_paths is None:
            rendered.append(" · untracked count unavailable", style=SECONDARY_TEXT_COLOR)
        elif repo.untracked_paths:
            rendered.append(
                f" · {repo.untracked_paths} untracked paths excluded from line totals",
                style=AMBER,
            )
        if (
            repo.dirty is not None
            and repo.dirty > 0
            and repo.added_lines == 0
            and repo.deleted_lines == 0
            and not repo.binary_entries
            and not repo.untracked_paths
        ):
            rendered.append(f" · {repo.dirty} changed paths", style=AMBER)
    return rendered


def _integration_label(value: str) -> str:
    if value == MERGE_INTEGRATION:
        return "Local merge"
    if value == SQUASH_INTEGRATION:
        return "Local squash and merge"
    if value == PULL_REQUEST_INTEGRATION:
        return "Pull request"
    return value


class HeaderPanel(GatedStatic):
    """Shows project, engine, task counts and checkout identity."""

    def __init__(self, context: WidgetContext) -> None:
        """Hold `context`; nothing here does I/O until `on_mount`."""
        super().__init__(id="header")
        self._widget_context = context
        self._remove_motion_callback: Callable[[], None] | None = None

    async def on_mount(self) -> None:
        """Render the current view, then refresh on every state change."""
        await self._refresh()
        self.run_worker(self._widget_context.on_change(self._refresh_if_attached), exclusive=True)
        self._remove_motion_callback = self._widget_context.on_motion(self._refresh_if_active)

    def on_unmount(self) -> None:
        if self._remove_motion_callback is not None:
            self._remove_motion_callback()
            self._remove_motion_callback = None

    async def _refresh(self) -> None:
        view = await self._widget_context.views.header()
        if not widget_is_attached(self):
            return
        summary = self._widget_context.views.workspace_summary()
        include_alerts = (
            self._widget_context.placement is not WidgetPlacement.IDENTITY
            or not self._widget_context.home_attention_present
        )
        content = _HeaderContent(view, summary, include_alerts)
        self.update_if_changed(content, key=content.render_key)

    async def on_resize(self, event: events.Resize) -> None:
        del event
        if widget_is_current(self):
            await self._refresh()

    async def _refresh_if_active(self) -> None:
        if widget_is_current(self):
            await self._refresh()

    async def _refresh_if_attached(self) -> None:
        if widget_is_attached(self):
            await self._refresh()


class HeaderWidget:
    """The `header` dashboard widget plugin."""

    name = "header"
    placement = WidgetPlacement.IDENTITY

    def create(self, context: WidgetContext) -> Widget:
        """Build the `HeaderPanel` for `context`."""
        return HeaderPanel(context)
