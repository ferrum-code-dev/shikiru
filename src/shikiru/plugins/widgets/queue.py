"""The `queue` widget: searchable task browser (docs/design/06-operator.md §5.4)."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import TYPE_CHECKING, cast

from rich.text import Text
from textual.containers import Horizontal, HorizontalScroll, Vertical
from textual.widgets import Button, Input, ListItem, ListView, Static

from shikiru.dashboard.contract import (
    TASK_STATUS_QUEUED,
    TASK_TERMINAL_STATUSES,
    TaskBrowserContextRow,
    TaskBrowserFilter,
    TaskBrowserRow,
    TaskBrowserView,
    WidgetPlacement,
)
from shikiru.ui.screen import widget_is_current
from shikiru.ui.tokens import (
    ALERT_COLOR,
    AMBER,
    PRIMARY_TEXT_COLOR,
    SAGE,
    SECONDARY_TEXT_COLOR,
    STEEL_BLUE,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from rich.console import Console, ConsoleOptions, RenderResult
    from textual import events
    from textual.app import App, ComposeResult
    from textual.widget import Widget

    from shikiru.dashboard.contract import WidgetContext

BROWSER_PAGE_SIZE = 24
RECENT_TASK_LIMIT = BROWSER_PAGE_SIZE
TASK_ROW_OUTLINE_MIN_WIDTH = 18
TASK_STATE_DECISION_NEEDED = "decision needed"
WAIT_REASON_DEPENDENCY = "dependency"
FOCUS_LIST_CLASS = "focus-list"
NO_TASK_ROW_OUTLINE_CLASS = "no-task-row-outline"
TASK_STATUS_DONE = "done"
TASK_STATUS_CANCELLED = "cancelled"
TASK_HOLD_PAUSED = "paused"
TASK_HOLD_INTERVENTION = "intervention"
TASK_MARKER_DECISION = "!"
TASK_MARKER_OPERATOR = "@"
TASK_MARKER_PAUSED = "="
TASK_MARKER_DONE = "✓"
TASK_MARKER_CANCELLED = "○"
TASK_MARKER_QUEUED = "·"
TASK_MARKER_CURRENT = "\N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK}"
TASK_STATUS_RUNNING = "running"
TASK_STATUS_INTEGRATING = "integrating"
TASK_STATUS_ESCALATED = "escalated"
TASK_MARKER_STALL = "!"
TASK_MARKER_RECENT = "Recent"
TASK_RECENT_HEADING_ID = "task-browser-recent-heading"
TASK_RECENT_LIST_ID = "task-browser-recent-list"
QUEUE_REFRESH_WORKER = "task-browser-refresh"
STEP_LABELS = {
    "triage": "Triaging",
    "plan": "Planning",
    "implement": "Implementing",
    "consult": "Consulting",
    "verify": "Verifying",
    "integrate": "Integrating",
}
WAIT_REASON_LABELS = {
    "paused": "paused",
    WAIT_REASON_DEPENDENCY: "dependency",
    "slot": "slot",
    "resources": "resources",
    "quota": "quota",
    "PR open": "PR open",
}
SEARCH_INPUT_ID = "task-browser-search"
TASK_LIST_ID = "task-browser-list"
TASK_PREVIEW_ID = "task-browser-preview"
COUNT_ID = "task-browser-count"
EMPTY_ID = "task-browser-empty"
FAMILY_CONTEXT_ID = "task-browser-family-context"
CLEAR_BUTTON_ID = "task-browser-clear"
MORE_BUTTON_ID = "task-browser-more"
REVEAL_BUTTON_ID = "task-browser-reveal"
ESCAPE_KEY = "escape"
MOVE_STEPS = {"up": -1, "down": 1}
FILTER_BUTTONS = (
    (TaskBrowserFilter.ACTIVE, "task-filter-active", "Active"),
    (TaskBrowserFilter.WAITING, "task-filter-waiting", "Waiting"),
    (TaskBrowserFilter.HISTORY, "task-filter-history", "History"),
    (TaskBrowserFilter.ALL, "task-filter-all", "All"),
)
FILTER_BY_BUTTON_ID = {button_id: task_filter for task_filter, button_id, _ in FILTER_BUTTONS}
FILTER_LABELS = {task_filter: label for task_filter, _, label in FILTER_BUTTONS}


class _FocusTaskCount:
    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        del console, options
        yield Text("TASKS", style=STEEL_BLUE)


class _BrowserListItem(ListItem):
    def __init__(
        self,
        row: TaskBrowserRow,
        *,
        focus_layout: bool,
    ) -> None:
        self._status_bright = False
        render = (
            _focus_row_text(row, status_bright=self._status_bright)
            if focus_layout
            else _row_text(row)
        )
        super().__init__(Static(render, markup=False), id=f"task-row-{row.task_id}")
        self.task_id = row.task_id
        self.row = row
        self.focus_layout = focus_layout

    def update_row(self, row: TaskBrowserRow) -> bool:
        if row == self.row:
            return False
        self.row = row
        self._status_bright = False
        self._render_row()
        return True

    def update_status_motion(self, status_bright: bool) -> None:
        if not self.focus_layout or not self.row.agent_processing:
            return
        if self._status_bright == status_bright:
            return
        self._status_bright = status_bright
        self._render_row()

    def _render_row(self) -> None:
        render = (
            _focus_row_text(self.row, status_bright=self._status_bright)
            if self.focus_layout
            else _row_text(self.row)
        )
        self.query_one(Static).update(render)


class _TaskBrowserListView(ListView):
    def __init__(
        self,
        *,
        widget_id: str,
        focus_layout: bool,
        move_selection: Callable[[int], Awaitable[None]],
        select_task: Callable[[str], None],
    ) -> None:
        super().__init__(id=widget_id)
        self._focus_layout = focus_layout
        self._move_selection = move_selection
        self._select_task = select_task

    def action_cursor_up(self) -> None:
        if not self._focus_layout:
            super().action_cursor_up()
            return
        self.run_worker(self._move_selection(MOVE_STEPS["up"]))

    def action_cursor_down(self) -> None:
        if not self._focus_layout:
            super().action_cursor_down()
            return
        self.run_worker(self._move_selection(MOVE_STEPS["down"]))

    def action_select_cursor(self) -> None:
        if not self._focus_layout:
            super().action_select_cursor()
            return
        item = self.highlighted_child
        if isinstance(item, _BrowserListItem):
            self._select_task(item.task_id)


def _row_text(row: TaskBrowserRow) -> str:
    state = row.status.replace("_", " ")
    identity = [row.task_id]
    if row.priority != 0:
        identity.append(f"priority {row.priority}")
    identity.append(row.title)
    state_details = [state]
    if row.step:
        state_details.append(row.step)
    if row.acting_role:
        state_details.append(row.acting_role)
    signals: list[str] = []
    if row.open_decision:
        signals.append("decision")
    if row.diagnosed_stall:
        signals.append("stalled")
    if row.holds:
        signals.extend(row.holds)
    if row.wait_reason and row.wait_reason not in signals:
        signals.append(row.wait_reason)
    return f"{' · '.join(identity)}\n{' · '.join((*state_details, *signals))}"


def _focus_row_text(row: TaskBrowserRow, *, status_bright: bool = False) -> Text:
    marker, marker_style = _focus_row_marker(row)
    stage = STEP_LABELS.get(row.step or "")
    state = _focus_row_state(row, stage)
    details = _focus_row_details(row, stage, state)
    if row.agent_processing and status_bright:
        details[0] = (details[0][0], f"{details[0][1]} bold")
    content = Text.assemble(
        (f"{marker} ", marker_style),
        (f"{row.task_id} · {row.title}", PRIMARY_TEXT_COLOR),
        "\n  ",
    )
    for index, (detail, style) in enumerate(details):
        if index:
            content.append(" · ", style=SECONDARY_TEXT_COLOR)
        content.append(detail, style=style)
    return content


def _focus_row_marker(row: TaskBrowserRow) -> tuple[str, str]:
    marker = TASK_MARKER_CURRENT
    style = STEEL_BLUE
    if row.open_decision:
        marker, style = TASK_MARKER_DECISION, AMBER
    elif row.diagnosed_stall:
        marker, style = TASK_MARKER_STALL, ALERT_COLOR
    elif TASK_HOLD_INTERVENTION in row.holds:
        marker = TASK_MARKER_OPERATOR
    elif TASK_HOLD_PAUSED in row.holds:
        marker, style = TASK_MARKER_PAUSED, AMBER
    elif row.status == TASK_STATUS_DONE:
        marker, style = TASK_MARKER_DONE, SAGE
    elif row.status == TASK_STATUS_CANCELLED:
        marker, style = TASK_MARKER_CANCELLED, SECONDARY_TEXT_COLOR
    elif row.status == TASK_STATUS_QUEUED:
        marker, style = TASK_MARKER_QUEUED, SECONDARY_TEXT_COLOR
    return marker, style


def _focus_row_state(row: TaskBrowserRow, stage: str | None) -> str:
    if row.wait_reason:
        wait_reason = WAIT_REASON_LABELS.get(row.wait_reason, row.wait_reason)
        if row.wait_reason == WAIT_REASON_DEPENDENCY and row.dependencies:
            waiting = f"waiting on dependency {', '.join(row.dependencies)}"
        else:
            waiting = f"waiting on {wait_reason}"
        return f"{stage} · {waiting}" if stage else waiting
    state = stage or row.status.replace("_", " ")
    if row.status == TASK_STATUS_QUEUED:
        state = "queued"
    elif row.status == TASK_STATUS_DONE:
        state = "Done"
    elif row.status == TASK_STATUS_CANCELLED:
        state = "Cancelled"
    elif row.status == TASK_STATUS_ESCALATED:
        state = TASK_STATE_DECISION_NEEDED if row.open_decision else "escalated"
    elif row.status == TASK_STATUS_RUNNING:
        state = stage or "running"
    elif row.status == TASK_STATUS_INTEGRATING:
        state = stage or "integrating"
    return state


def _focus_row_details(
    row: TaskBrowserRow,
    stage: str | None,
    state: str,
) -> list[tuple[str, str]]:
    state_style = (
        SECONDARY_TEXT_COLOR
        if row.status in TASK_TERMINAL_STATUSES or row.status == TASK_STATUS_QUEUED
        else AMBER
        if row.wait_reason or row.holds or row.open_decision
        else STEEL_BLUE
    )
    details: list[tuple[str, str]] = [(state, state_style)]
    if row.acting_role and not stage:
        details.append((row.acting_role.title(), SECONDARY_TEXT_COLOR))
    if (
        row.status not in TASK_TERMINAL_STATUSES
        and row.dependencies
        and row.wait_reason != WAIT_REASON_DEPENDENCY
    ):
        details.append((f"depends on {', '.join(row.dependencies)}", SECONDARY_TEXT_COLOR))
    details.extend((hold, AMBER) for hold in row.holds)
    if row.diagnosed_stall:
        details.append(("stalled", ALERT_COLOR))
    if row.open_decision and TASK_STATE_DECISION_NEEDED not in state:
        details.append((TASK_STATE_DECISION_NEEDED, AMBER))
    return details


class QueuePanel(Vertical):
    """Render task search, filters, selection and a compact retained task preview."""

    DEFAULT_CSS = """
    #queue { height: auto; }
    #task-browser-tools { height: 3; }
    #task-browser-search { width: 1fr; }
    #task-browser-clear { width: auto; min-width: 8; }
    #task-browser-filters { height: 3; }
    #task-browser-filters Button {
        width: auto; min-width: 1; margin-right: 1;
        border: round $neutral-border; background: $surface; color: $muted-text;
    }
    #task-browser-filters Button:focus {
        border: round $focus; color: $primary-text; text-style: bold reverse;
    }
    #task-browser-count { height: 1; margin-bottom: 1; color: $steel-blue; }
    #task-browser-list {
        height: 12; min-height: 2; border: none; padding: 0; background: $background;
        scrollbar-size-vertical: 1;
    }
    #task-browser-list > ListItem {
        height: auto; min-height: 2; margin-bottom: 1; padding: 0;
        border: none; background: $background; color: $primary-text;
    }
    #task-browser-list.focus-list > ListItem { margin-bottom: 0; }
    #task-browser-list > ListItem:last-child { margin-bottom: 0; }
    #task-browser-list > ListItem > Static {
        height: auto; padding: 0; color: $primary-text;
        text-wrap: wrap; text-overflow: clip;
    }
    #task-browser-list > ListItem.-highlight {
        outline: solid $selection-outline; background: $selected-surface; color: $primary-text;
        text-style: none;
    }
    #task-browser-list.focus-list > ListItem.-highlight {
        outline: none; border: solid $selection-outline;
    }
    #task-browser-list.no-task-row-outline.focus-list > ListItem.-highlight {
        border: none;
    }
    #task-browser-list > ListItem.-highlight > Static {
        background: $selected-surface; color: $primary-text; text-style: none;
    }
    #task-browser-recent-heading {
        display: none; height: 2; min-height: 2; margin: 0; border-top: solid $neutral-border;
        padding: 0; color: $muted-text;
    }
    #task-browser-recent-list {
        display: none; height: auto; min-height: 2; max-height: 8; border: none;
        padding: 0; background: $background; scrollbar-size-vertical: 1;
    }
    #task-browser-recent-list > ListItem {
        height: auto; min-height: 2; margin: 0; padding: 0;
        border: none; background: $background; color: $primary-text;
    }
    #task-browser-recent-list > ListItem > Static {
        height: auto; padding: 0; color: $primary-text;
        text-wrap: wrap; text-overflow: clip;
    }
    #task-browser-recent-list > ListItem.-highlight {
        outline: none; border: solid $selection-outline;
        background: $selected-surface; color: $primary-text; text-style: none;
    }
    #task-browser-recent-list.no-task-row-outline > ListItem.-highlight { border: none; }
    #task-browser-recent-list > ListItem.-highlight > Static {
        background: $selected-surface; color: $primary-text; text-style: none;
    }
    #task-browser-list:focus { background-tint: transparent; }
    #task-browser-empty { height: auto; padding: 1; color: $muted-text; }
    #task-browser-family-context { height: auto; padding: 0 1; color: $secondary-text; }
    #task-browser-more { width: auto; min-width: 1; }
    #task-browser-preview { height: auto; padding: 1; border: round $neutral-border; }
    #task-browser-reveal { width: auto; min-width: 1; }
    """

    def __init__(self, context: WidgetContext) -> None:
        super().__init__(id="queue")
        self._widget_context = context
        self._focus_layout = context.placement is WidgetPlacement.TASK_LIST
        self._filter = TaskBrowserFilter.ALL
        self._search = ""
        self._selected_task_id = context.focus_task
        self._selection_cleared = False
        self._loaded_page_count = 1
        self._view: TaskBrowserView | None = None
        self._refreshing = False
        self._refresh_lock = asyncio.Lock()
        self._list_render_versions: dict[str, int] = {}
        self._status_bright = False
        self._remove_motion_callback: Callable[[], None] | None = None

    def compose(self) -> ComposeResult:
        search = Input(placeholder="Search task ID or title", id=SEARCH_INPUT_ID)
        if self._focus_layout:
            search.display = False
            yield search
        else:
            with Horizontal(id="task-browser-tools"):
                yield search
                yield Button("Clear", id=CLEAR_BUTTON_ID)
            with HorizontalScroll(id="task-browser-filters"):
                for _, button_id, label in FILTER_BUTTONS:
                    yield Button(label, id=button_id)
        yield Static("", id=COUNT_ID, markup=False)
        yield Static("", id=EMPTY_ID, markup=False)
        yield Static("", id=FAMILY_CONTEXT_ID, markup=False)
        task_list = _TaskBrowserListView(
            widget_id=TASK_LIST_ID,
            focus_layout=self._focus_layout,
            move_selection=self._move_selection,
            select_task=self.select_task,
        )
        if self._focus_layout:
            task_list.add_class(FOCUS_LIST_CLASS)
        yield task_list
        recent_heading = Static(TASK_MARKER_RECENT, id=TASK_RECENT_HEADING_ID, markup=False)
        recent_heading.display = False
        recent_list = _TaskBrowserListView(
            widget_id=TASK_RECENT_LIST_ID,
            focus_layout=self._focus_layout,
            move_selection=self._move_selection,
            select_task=self.select_task,
        )
        if self._focus_layout:
            recent_list.add_class(FOCUS_LIST_CLASS)
        recent_list.display = False
        yield recent_heading
        yield recent_list
        more = Button("Load more", id=MORE_BUTTON_ID)
        if self._focus_layout:
            more.display = False
            yield more
            preview = Static("", id=TASK_PREVIEW_ID, markup=False)
            preview.display = False
            yield preview
        else:
            yield more
            yield Static(
                "Select a task to keep its context here.",
                id=TASK_PREVIEW_ID,
                markup=False,
            )
            yield Button("Reveal selected task", id=REVEAL_BUTTON_ID)

    async def on_mount(self) -> None:
        self._sync_task_row_outline()
        await self._refresh()
        self.run_worker(
            self._widget_context.on_change(self._refresh_if_current),
            exclusive=True,
        )
        self._remove_motion_callback = self._widget_context.on_motion(self._refresh_while_active)

    def on_resize(self, event: events.Resize) -> None:
        del event
        self._sync_task_row_outline()

    def on_unmount(self) -> None:
        if self._remove_motion_callback is not None:
            self._remove_motion_callback()
            self._remove_motion_callback = None

    async def _refresh_while_active(self) -> None:
        if (
            not self.is_mounted
            or not widget_is_current(self)
            or not cast("App[object]", self.app).is_running
        ):
            return
        await self._refresh()
        if not self._render_targets_are_attached():
            return
        list_views = self._browser_list_views()
        if not self._widget_context.animations or not self._widget_context.views.engine_up():
            self._status_bright = False
            for list_view in list_views:
                for item in list_view.children:
                    if isinstance(item, _BrowserListItem):
                        item.update_status_motion(False)
            return
        self._status_bright = not self._status_bright
        for list_view in list_views:
            for item in list_view.children:
                if isinstance(item, _BrowserListItem):
                    item.update_status_motion(self._status_bright)

    async def _refresh_if_current(self) -> None:
        if widget_is_current(self):
            await self._refresh()

    def refresh_after_resume(self) -> None:
        if widget_is_current(self):
            self.run_worker(
                self._refresh_if_current(),
                group=QUEUE_REFRESH_WORKER,
                exclusive=True,
            )

    def _sync_task_row_outline(self) -> None:
        if not self._focus_layout or not self.is_mounted:
            return
        for list_view in self._browser_list_views():
            list_view.set_class(
                list_view.size.width < TASK_ROW_OUTLINE_MIN_WIDTH,
                NO_TASK_ROW_OUTLINE_CLASS,
            )

    async def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != SEARCH_INPUT_ID:
            return
        await self._refresh(search=event.value, reset_pages=True)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == SEARCH_INPUT_ID:
            self.query_one(f"#{TASK_LIST_ID}", ListView).focus()

    def open_search(self) -> None:
        """Focus the task search from contextual keyboard navigation."""
        search = self.query_one(f"#{SEARCH_INPUT_ID}", Input)
        search.display = True
        search.focus()

    @property
    def selected_task_missing(self) -> bool:
        return self._view is not None and self._view.selected_task_missing

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        selected_filter = FILTER_BY_BUTTON_ID.get(button_id)
        if selected_filter is not None:
            await self._refresh(task_filter=selected_filter, reset_pages=True)
            self.query_one(f"#{TASK_LIST_ID}", ListView).focus()
        elif button_id == CLEAR_BUTTON_ID:
            search = self.query_one(f"#{SEARCH_INPUT_ID}", Input)
            search.value = ""
            if not self._search:
                await self._refresh(search="", reset_pages=True)
            search.focus()
        elif button_id == MORE_BUTTON_ID:
            await self._refresh(append_page=True)
            self.query_one(f"#{TASK_LIST_ID}", ListView).focus()
        elif button_id == REVEAL_BUTTON_ID:
            await self._reveal_selected_task()

    async def activate_filter(self, task_filter: TaskBrowserFilter) -> None:
        """Navigate to a task category while retaining search and selected identity."""
        await self._refresh(task_filter=task_filter, reset_pages=True)
        self.query_one(f"#{TASK_LIST_ID}", ListView).focus()

    async def load_more(self) -> None:
        """Append the next bounded browser page."""
        await self._refresh(append_page=True)

    async def reveal_selected_task(self) -> None:
        """Reveal the selected task in its matching filter and clear the search."""
        await self._reveal_selected_task()

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.list_view.id not in {TASK_LIST_ID, TASK_RECENT_LIST_ID} or self._refreshing:
            return
        if isinstance(event.item, _BrowserListItem):
            self.select_task(event.item.task_id)

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id in {TASK_LIST_ID, TASK_RECENT_LIST_ID} and isinstance(
            event.item, _BrowserListItem
        ):
            self.select_task(event.item.task_id)
            if not self._focus_layout:
                await self._open_task(event.item.task_id)

    def on_key(self, event: events.Key) -> None:
        focused = cast("App[object]", self.app).focused
        if event.key == ESCAPE_KEY and isinstance(focused, Input) and focused.id == SEARCH_INPUT_ID:
            event.stop()
            if focused.value:
                focused.value = ""
            else:
                if self._focus_layout:
                    focused.display = False
                self.query_one(f"#{TASK_LIST_ID}", ListView).focus()
            return
        if isinstance(focused, Input):
            return

    async def _move_selection(self, step: int) -> None:
        rows = tuple(
            item
            for row in (self._view.rows if self._view is not None else ())
            for item in self._browser_row_items(row.task_id)
        )
        if not rows:
            return
        current = next(
            (index for index, item in enumerate(rows) if item.task_id == self._selected_task_id),
            None,
        )
        next_index = (
            (len(rows) - 1 if step < 0 else 0) if current is None else (current + step) % len(rows)
        )
        item = rows[next_index]
        self.select_task(item.task_id)
        list_view = cast("ListView", item.parent)
        list_view.index = list_view.children.index(item)
        list_view.scroll_to_widget(item, animate=False, immediate=True)

    async def _open_task(self, task_id: str) -> None:
        open_detail = self._widget_context.open_task_detail
        if open_detail is not None:
            await open_detail(task_id)

    def select_task(self, task_id: str) -> None:
        """Select a task and update the Home inspector without opening a route."""
        self._selected_task_id = task_id
        self._selection_cleared = False
        callback = self._widget_context.on_task_selected
        if callback is not None:
            callback(task_id)
        if self._view is None:
            return
        row = next(
            (candidate for candidate in self._view.rows if candidate.task_id == task_id), None
        )
        if row is None:
            return
        self._view = replace(
            self._view,
            selected_task=row,
            selected_task_missing=False,
            selected_task_matches=True,
        )
        self._render_view(self._view)

    def _browser_list_views(self) -> tuple[ListView, ...]:
        list_views = tuple(
            list_view
            for list_id in (TASK_LIST_ID, TASK_RECENT_LIST_ID)
            if (list_view := self.query(f"#{list_id}"))
        )
        return tuple(cast("ListView", list_view.first()) for list_view in list_views)

    def _browser_row_items(self, task_id: str) -> tuple[_BrowserListItem, ...]:
        return tuple(
            item
            for list_view in self._browser_list_views()
            for item in list_view.children
            if isinstance(item, _BrowserListItem) and item.task_id == task_id
        )

    def clear_selection(self) -> None:
        """Clear the Home inspector until an operator selects another task."""
        self._selected_task_id = None
        self._selection_cleared = True
        if self._view is not None:
            self._view = replace(
                self._view,
                selected_task=None,
                selected_task_missing=False,
                selected_task_matches=False,
            )
        for list_view in self._browser_list_views():
            list_view.index = None
        callback = self._widget_context.on_task_selected
        if callback is not None:
            callback(None)

    def sync_context_selection(self, task_id: str | None) -> None:
        """Keep list identity aligned with another Home widget's task selection."""
        if task_id == self._selected_task_id:
            return
        self._selected_task_id = task_id
        self._selection_cleared = task_id is None
        if not widget_is_current(self):
            return
        for list_view in self._browser_list_views():
            item = next(
                (
                    candidate
                    for candidate in list_view.children
                    if isinstance(candidate, _BrowserListItem) and candidate.task_id == task_id
                ),
                None,
            )
            list_view.index = list_view.children.index(item) if item is not None else None
            if item is not None:
                list_view.scroll_to_widget(item, animate=False, immediate=True)

    async def _reveal_selected_task(self) -> None:
        if self._selected_task_id is None or self._view is None:
            return
        selected = self._view.selected_task
        if selected is None:
            return
        task_filter = _filter_for_task(selected)
        self.query_one(f"#{SEARCH_INPUT_ID}", Input).value = ""
        await self._refresh(task_filter=task_filter, search="", reset_pages=True)
        list_view = self.query_one(f"#{TASK_LIST_ID}", ListView)
        list_view.focus()

    async def _refresh(
        self,
        *,
        append_page: bool = False,
        task_filter: TaskBrowserFilter | None = None,
        search: str | None = None,
        reset_pages: bool = False,
    ) -> None:
        async with self._refresh_lock:
            if not widget_is_current(self):
                return
            self._refreshing = True
            try:
                if task_filter is not None:
                    self._filter = task_filter
                if search is not None:
                    self._search = search
                if reset_pages:
                    self._loaded_page_count = 1
                view = self._load_next_page() if append_page else self._load_pages()
                if self._selected_task_id is None and view.rows and not self._selection_cleared:
                    self._selected_task_id = view.rows[0].task_id
                    view = replace(
                        view,
                        selected_task=view.rows[0],
                        selected_task_matches=True,
                    )
                self._view = view
                callback = self._widget_context.on_task_selected
                if callback is not None:
                    callback(self._selected_task_id)
                if not self._render_targets_are_attached():
                    return
                await self._sync_task_lists(view)
                if not self._render_targets_are_attached():
                    return
                self._render_view(view)
            finally:
                self._refreshing = False

    def _load_pages(self, page_count: int | None = None) -> TaskBrowserView:
        requested_page_count = page_count if page_count is not None else self._loaded_page_count
        pages = self._widget_context.views.task_browser_pages(
            task_filter=self._filter,
            search=self._search,
            limit=BROWSER_PAGE_SIZE,
            page_count=requested_page_count,
            selected_task_id=self._selected_task_id,
        )
        self._loaded_page_count = len(pages)
        rows = tuple(row for page in pages for row in page.rows)
        matched_ids = {row.task_id for row in rows}
        ancestors: dict[str, TaskBrowserContextRow] = {}
        for page in pages:
            for ancestor in page.ancestor_context:
                if ancestor.task_id not in matched_ids:
                    ancestors[ancestor.task_id] = ancestor
        view = pages[-1]
        selected_task = view.selected_task
        is_focus_recent = (
            self._focus_layout and self._filter is TaskBrowserFilter.ALL and not self._search
        )
        if is_focus_recent:
            recent_pages = self._widget_context.views.task_browser_pages(
                task_filter=TaskBrowserFilter.HISTORY,
                search=self._search,
                limit=RECENT_TASK_LIMIT,
                page_count=1,
                selected_task_id=self._selected_task_id,
            )
            recent_page = recent_pages[0]
            recent_rows = tuple(
                row for row in recent_page.rows if row.status in TASK_TERMINAL_STATUSES
            )
            recent_by_id = {row.task_id: row for row in recent_rows}
            matched_ids.update(row.task_id for row in recent_rows)
            active_rows = tuple(
                row
                for row in rows
                if row.status not in TASK_TERMINAL_STATUSES and row.task_id not in recent_by_id
            )
            rows = (*active_rows, *recent_rows)
            if selected_task is not None and selected_task.task_id in recent_by_id:
                selected_task = recent_by_id[selected_task.task_id]
            elif selected_task is None:
                selected_task = recent_page.selected_task
            visible_active_count = len(active_rows)
            matching_active_count = max(
                view.matching_count - recent_page.matching_count,
                0,
            )
            view = replace(
                view,
                has_more=visible_active_count < matching_active_count,
            )
        return replace(
            view,
            rows=rows,
            selected_task=selected_task,
            ancestor_context=() if is_focus_recent else tuple(ancestors.values()),
        )

    def _load_next_page(self) -> TaskBrowserView:
        current = self._view
        if (
            current is not None
            and current.filter is self._filter
            and current.search == self._search
        ):
            return self._load_pages(self._loaded_page_count + 1)
        return self._load_pages()

    async def _sync_task_lists(self, view: TaskBrowserView) -> None:
        recent_rows = (
            tuple(row for row in view.rows if row.status in TASK_TERMINAL_STATUSES)
            if self._focus_layout and view.filter is TaskBrowserFilter.ALL and not view.search
            else ()
        )
        recent_ids = {row.task_id for row in recent_rows}
        active_rows = tuple(row for row in view.rows if row.task_id not in recent_ids)
        active_list = self.query_one(f"#{TASK_LIST_ID}", ListView)
        recent_list = self.query_one(f"#{TASK_RECENT_LIST_ID}", ListView)
        recent_heading = self.query_one(f"#{TASK_RECENT_HEADING_ID}", Static)
        recent_heading.display = bool(recent_rows)
        recent_list.display = bool(recent_rows)
        await self._sync_list_rows(active_list, active_rows)
        await self._sync_list_rows(recent_list, recent_rows)
        if self._list_view_is_current(active_list) and self._list_view_is_current(recent_list):
            self._sync_selection(active_list, recent_list)

    async def _sync_list_rows(
        self,
        list_view: ListView,
        rows: tuple[TaskBrowserRow, ...],
    ) -> None:
        if not self._list_view_is_current(list_view):
            return
        items = {
            item.task_id: item for item in list_view.children if isinstance(item, _BrowserListItem)
        }
        desired_ids = tuple(row.task_id for row in rows)
        wanted_ids = set(desired_ids)
        if tuple(items) == desired_ids and len(list_view.children) == len(rows):
            anchor, anchor_offset = self._visible_anchor(list_view, tuple(items.values()))
            changed = self._update_browser_items(items, rows)
            if changed:
                self._restore_list_position_after_refresh(
                    list_view,
                    anchor,
                    anchor_offset,
                )
            return
        anchor, anchor_offset = self._visible_anchor(
            list_view,
            tuple(item for task_id, item in items.items() if task_id in wanted_ids),
        )
        await self._remove_stale_list_items(items, wanted_ids)
        if not self._list_view_is_current(list_view):
            return
        self._update_browser_items(items, rows)
        desired_children = [items[row.task_id] for row in rows]
        await self._place_list_items(list_view, desired_children)
        if not self._list_view_is_current(list_view):
            return
        self._restore_list_position_after_refresh(
            list_view,
            anchor,
            anchor_offset,
        )

    async def _remove_stale_list_items(
        self,
        items: dict[str, _BrowserListItem],
        wanted_ids: set[str],
    ) -> None:
        removed = [item for task_id, item in items.items() if task_id not in wanted_ids]
        if removed:
            await asyncio.gather(*(item.remove() for item in removed))
            retained = {task_id: item for task_id, item in items.items() if task_id in wanted_ids}
            items.clear()
            items.update(retained)

    def _update_browser_items(
        self,
        items: dict[str, _BrowserListItem],
        rows: tuple[TaskBrowserRow, ...],
    ) -> bool:
        changed = False
        for row in rows:
            if row.task_id not in items:
                items[row.task_id] = _BrowserListItem(row, focus_layout=self._focus_layout)
                changed = True
            changed = items[row.task_id].update_row(row) or changed
        return changed

    async def _place_list_items(
        self,
        list_view: ListView,
        desired_children: Sequence[ListItem],
    ) -> None:
        additions = [child for child in desired_children if child.parent is None]
        if additions:
            await list_view.extend(additions)
            if not self._list_view_is_current(list_view):
                return
        for index, child in enumerate(desired_children):
            if list_view.children[index] is not child:
                list_view.move_child(child, before=index)

    def _visible_anchor(
        self,
        list_view: ListView,
        items: tuple[_BrowserListItem, ...],
    ) -> tuple[_BrowserListItem | None, int]:
        content_region = list_view.scrollable_content_region
        visible_items = [item for item in items if item.region.overlaps(content_region)]
        if not visible_items:
            return None, 0
        anchor = min(visible_items, key=lambda item: item.region.y)
        return anchor, anchor.region.y - content_region.y

    def _restore_list_position_after_refresh(
        self,
        list_view: ListView,
        anchor: _BrowserListItem | None,
        anchor_offset: int,
    ) -> None:
        list_key = list_view.id or TASK_LIST_ID
        render_version = self._list_render_versions.get(list_key, 0) + 1
        self._list_render_versions[list_key] = render_version
        callback_args = (
            list_view,
            anchor,
            anchor_offset,
            render_version,
            False,
        )
        if self._focus_layout:
            cast("App[object]", self.app).call_after_refresh(
                self._restore_list_position,
                *callback_args,
            )
        else:
            list_view.call_after_refresh(self._restore_list_position, *callback_args)

    def _restore_list_position(
        self,
        list_view: ListView,
        anchor: _BrowserListItem | None,
        anchor_offset: int,
        render_version: int,
        settled: bool,
    ) -> None:
        list_key = list_view.id or TASK_LIST_ID
        if render_version != self._list_render_versions.get(
            list_key
        ) or not self._list_view_is_current(list_view):
            return
        if anchor is not None and anchor.parent is list_view:
            content_region = list_view.scrollable_content_region
            current_offset = anchor.region.y - content_region.y
            scroll_delta = current_offset - anchor_offset
            if scroll_delta:
                list_view.scroll_to(
                    y=list_view.scroll_y + scroll_delta,
                    animate=False,
                    immediate=True,
                )
        selected_item = next(
            (
                item
                for item in list_view.children
                if isinstance(item, _BrowserListItem) and item.task_id == self._selected_task_id
            ),
            None,
        )
        if (
            anchor is None
            and selected_item is not None
            and selected_item.parent is list_view
            and not selected_item.region.overlaps(list_view.scrollable_content_region)
        ):
            list_view.scroll_to_widget(selected_item, animate=False, immediate=True)
        if not settled:
            callback = (
                cast("App[object]", self.app).call_after_refresh
                if self._focus_layout
                else list_view.call_after_refresh
            )
            callback(
                self._restore_list_position,
                list_view,
                anchor,
                anchor_offset,
                render_version,
                True,
            )
            list_view.refresh(layout=True)

    def _sync_selection(
        self,
        active_list: ListView,
        recent_list: ListView,
    ) -> None:
        selected_task_id = self._selected_task_id
        for list_view in (active_list, recent_list):
            selected_item = next(
                (
                    item
                    for item in list_view.children
                    if isinstance(item, _BrowserListItem) and item.task_id == selected_task_id
                ),
                None,
            )
            selected_index = (
                list_view.children.index(selected_item) if selected_item is not None else None
            )
            if list_view.index != selected_index:
                list_view.index = selected_index

    def _render_view(self, view: TaskBrowserView) -> None:
        if not self._render_targets_are_attached():
            return
        self._render_count(view)
        if not self._focus_layout:
            self.query_one(f"#{CLEAR_BUTTON_ID}", Button).disabled = not bool(view.search)
            for task_filter, button_id, label in FILTER_BUTTONS:
                button = self.query_one(f"#{button_id}", Button)
                button.label = f"> {label}" if task_filter is view.filter else label
        more = self.query_one(f"#{MORE_BUTTON_ID}", Button)
        more.disabled = not view.has_more
        more.display = view.has_more if self._focus_layout else True
        empty = self.query_one(f"#{EMPTY_ID}", Static)
        empty.display = not view.rows
        if view.rows:
            empty.update("")
        elif view.project_count == 0:
            empty.update("No tasks yet.")
        elif view.search:
            empty.update(f"No tasks match {view.search!r} in {FILTER_LABELS[view.filter]}.")
        else:
            empty.update(f"No {FILTER_LABELS[view.filter].lower()} tasks.")
        context = self.query_one(f"#{FAMILY_CONTEXT_ID}", Static)
        context.display = bool(view.ancestor_context)
        if view.ancestor_context:
            context.update(
                "Family context · not counted as matches\n"
                + "\n".join(
                    f"{row.task_id} · {row.title} · {row.status}"
                    + (f" · {row.mode}" if row.mode else "")
                    for row in view.ancestor_context
                )
            )
        else:
            context.update("")
        if not self._focus_layout:
            self._render_preview(view)

    def _render_targets_are_attached(self) -> bool:
        target_ids = (
            COUNT_ID,
            EMPTY_ID,
            FAMILY_CONTEXT_ID,
            MORE_BUTTON_ID,
            TASK_LIST_ID,
        )
        if self._focus_layout:
            target_ids = (
                *target_ids,
                TASK_PREVIEW_ID,
                TASK_RECENT_HEADING_ID,
                TASK_RECENT_LIST_ID,
            )
        else:
            target_ids = (
                *target_ids,
                TASK_PREVIEW_ID,
                REVEAL_BUTTON_ID,
                CLEAR_BUTTON_ID,
                *(button_id for _, button_id, _ in FILTER_BUTTONS),
            )
        return widget_is_current(self) and all(
            self.query(f"#{target_id}") for target_id in target_ids
        )

    def _list_view_is_current(self, list_view: ListView) -> bool:
        return widget_is_current(self) and list_view.is_attached and list_view.parent is self

    def _render_count(self, view: TaskBrowserView) -> None:
        is_default_focus = (
            self._focus_layout and view.filter is TaskBrowserFilter.ALL and not view.search
        )
        if is_default_focus and not view.selected_task_missing:
            count = _FocusTaskCount()
        else:
            count = Text(
                "TASKS · Decision first"
                if is_default_focus
                else (
                    f"TASKS · {FILTER_LABELS[view.filter]} · "
                    f"{len(view.rows)} of {view.matching_count}"
                )
            )
            if view.search:
                count += f" · Search: {view.search}"
            if view.selected_task_missing and view.selected_task is not None:
                count += f" · selected {view.selected_task.task_id} remains outside this view"
        self.query_one(f"#{COUNT_ID}", Static).update(count)

    def _render_preview(self, view: TaskBrowserView) -> None:
        preview = self.query_one(f"#{TASK_PREVIEW_ID}", Static)
        reveal = self.query_one(f"#{REVEAL_BUTTON_ID}", Button)
        selected = view.selected_task
        if view.selected_task_missing:
            preview.update(f"{self._selected_task_id} is no longer available.")
            reveal.disabled = True
            return
        if selected is None:
            preview.update("Select a task to keep its context here.")
            reveal.disabled = True
            return
        state = selected.status.replace("_", " ")
        step = f" · {selected.step}" if selected.step else ""
        role = f" · {selected.acting_role}" if selected.acting_role else ""
        blocker = [*selected.holds]
        if selected.wait_reason:
            blocker.append(selected.wait_reason)
        if selected.open_decision:
            blocker.append("open decision")
        if selected.diagnosed_stall:
            blocker.append("diagnosed stall")
        blocker_text = f"\nWaiting: {' · '.join(blocker)}" if blocker else ""
        dependencies = (
            f"\nDependencies: {', '.join(selected.dependencies)}" if selected.dependencies else ""
        )
        priority = (
            f"\nDispatch priority: {selected.priority}" if state == TASK_STATUS_QUEUED else ""
        )
        note = ""
        reveal.disabled = view.selected_task_matches
        if view.selected_task_matches and all(row.task_id != selected.task_id for row in view.rows):
            reveal.disabled = True
            note = "\nSelected task is farther down the matching list. Load more to reveal it."
        elif not view.selected_task_matches:
            note = f"\nSelected task is outside {FILTER_LABELS[view.filter]} or the current search."
        preview.update(
            f"Selected · {selected.task_id} · {selected.title}\n"
            f"{state}{step} · iteration {selected.iteration}{role} · submitted {selected.age}"
            + (f" · requeued {selected.requeued_age}" if selected.requeued_age is not None else "")
            + f"{blocker_text}{priority}{dependencies}{note}"
        )


def _filter_for_task(row: TaskBrowserRow) -> TaskBrowserFilter:
    if row.status in TASK_TERMINAL_STATUSES:
        return TaskBrowserFilter.HISTORY
    if row.status == TASK_STATUS_QUEUED or row.holds or row.wait_reason:
        return TaskBrowserFilter.WAITING
    return TaskBrowserFilter.ACTIVE


class QueueWidget:
    """The `queue` dashboard widget plugin (06 §5.4)."""

    name = "queue"
    placement = WidgetPlacement.TASK_LIST

    def create(self, context: WidgetContext) -> Widget:
        """Build the task browser for `context` while retaining the `queue` entry point."""
        return QueuePanel(context)
