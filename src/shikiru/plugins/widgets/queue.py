"""The `queue` widget: searchable task browser (docs/design/06-operator.md §5.4)."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import TYPE_CHECKING, cast

from rich.text import Text
from textual.containers import Horizontal, Vertical
from textual.widgets import Input, ListItem, ListView, Static

from shikiru.dashboard.contract import (
    TASK_STATUS_QUEUED,
    TASK_TERMINAL_STATUSES,
    TaskBrowserFilter,
    TaskBrowserRow,
    TaskBrowserView,
    WidgetPlacement,
)
from shikiru.plugins.widgets.queue_hint import load_more_hint, task_list_header
from shikiru.ui.gated_static import GatedStatic
from shikiru.ui.keys import key_hint
from shikiru.ui.recent_window import RecentWindow, step_selection
from shikiru.ui.screen import widget_is_current
from shikiru.ui.tokens import (
    ALERT_COLOR,
    AMBER,
    PRIMARY_TEXT_COLOR,
    SAGE,
    SECONDARY_TEXT_COLOR,
    STEEL_BLUE,
    STOPPED_DURING,
    STYLE_BOLD,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from textual import events
    from textual.app import App, ComposeResult
    from textual.widget import Widget

    from shikiru.dashboard.contract import WidgetContext

BROWSER_PAGE_SIZE = 24
FOCUS_MAIN_CONTAINER_ID = "focus-main"
TASK_ROW_LINES = 2
TASK_ROW_GAP_LINES = 1
RECENT_LATER_LABEL = "↑ later"
RECENT_EARLIER_LABEL = "↓ earlier"
RECENT_LATER_COMPACT_LABEL = "↑"
RECENT_EARLIER_COMPACT_LABEL = "↓"
RECENT_CONTROL_GAP = 1
TASK_STATE_DECISION_NEEDED = "decision needed"
WAIT_REASON_DEPENDENCY = "dependency"
FOCUS_LIST_CLASS = "focus-list"
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
RECENT_HEADING_FULL_COLUMNS = (
    len(TASK_MARKER_RECENT)
    + RECENT_CONTROL_GAP * 2
    + len(RECENT_LATER_LABEL)
    + len(RECENT_EARLIER_LABEL)
)
TASK_ACTIVE_BLOCK_ID = "task-browser-active"
TASK_RECENT_BLOCK_ID = "task-browser-recent"
RECENT_LATER_ID = "task-browser-recent-later"
RECENT_EARLIER_ID = "task-browser-recent-earlier"
RECENT_INACTIVE_CLASS = "-inactive"
TASK_RECENT_HEADING_ID = "task-browser-recent-heading"
TASK_RECENT_TITLE_ID = "task-browser-recent-title"
RECENT_PAGER_CLASS = "recent-pager"
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
COUNT_ID = "task-browser-count"
EMPTY_ID = "task-browser-empty"
MORE_HINT_ID = "task-browser-more"
ESCAPE_KEY = "escape"
MOVE_STEPS = {"up": -1, "down": 1}
STATUS_FILTER_CYCLE = (
    TaskBrowserFilter.ALL,
    TaskBrowserFilter.RUNNING,
    TaskBrowserFilter.INTEGRATING,
    TaskBrowserFilter.ESCALATED,
    TaskBrowserFilter.QUEUED,
    TaskBrowserFilter.DONE,
    TaskBrowserFilter.CANCELLED,
)
FILTER_LABELS = {
    TaskBrowserFilter.ACTIVE: "Active",
    TaskBrowserFilter.WAITING: "Waiting",
    TaskBrowserFilter.HISTORY: "History",
    TaskBrowserFilter.ALL: "All",
    **{task_filter: task_filter.value.capitalize() for task_filter in STATUS_FILTER_CYCLE[1:]},
}
STATUS_FILTER_WORKER = "task-browser-status-filter"
MORE_WORKER = "task-browser-more"
SEARCH_FIELD_CLASS = "focus-search"
SEARCH_OPEN_CLASS = "search-open"
HEADER_TINY_MAX_ROWS = 8
ROW_STATUS_MARKERS = {
    TASK_STATUS_DONE: (TASK_MARKER_DONE, SAGE),
    TASK_STATUS_CANCELLED: (TASK_MARKER_CANCELLED, SECONDARY_TEXT_COLOR),
    TASK_STATUS_QUEUED: (TASK_MARKER_QUEUED, SECONDARY_TEXT_COLOR),
}


class _BrowserListItem(ListItem):
    def __init__(self, row: TaskBrowserRow) -> None:
        self._status_bright = False
        self._body = GatedStatic(_focus_row_text(row), markup=False)
        super().__init__(self._body, id=f"task-row-{row.task_id}")
        self.task_id = row.task_id
        self.row = row

    def update_row(self, row: TaskBrowserRow) -> bool:
        if row == self.row:
            return False
        self.row = row
        self._status_bright = False
        self._render_row()
        return True

    def update_status_motion(self, status_bright: bool) -> None:
        if not self.row.agent_processing:
            return
        if self._status_bright == status_bright:
            return
        self._status_bright = status_bright
        self._render_row()

    def _render_row(self) -> None:
        self._body.update_if_changed(_focus_row_text(self.row, status_bright=self._status_bright))


class _TaskBrowserListView(ListView):
    def __init__(
        self,
        *,
        widget_id: str,
        move_selection: Callable[[int], Awaitable[None]],
        select_task: Callable[[str], None],
        activate_attention_decision: Callable[[], Awaitable[bool]] | None,
    ) -> None:
        super().__init__(id=widget_id)
        self._move_selection = move_selection
        self._select_task = select_task
        self._activate_attention_decision = activate_attention_decision

    def is_tab_stop(self) -> bool:
        """Keep a task list a Tab stop only while it holds the selection."""
        return self.highlighted_child is not None

    def action_cursor_up(self) -> None:
        self.run_worker(self._move_selection(MOVE_STEPS["up"]))

    def action_cursor_down(self) -> None:
        self.run_worker(self._move_selection(MOVE_STEPS["down"]))

    def action_select_cursor(self) -> None:
        item = self.highlighted_child
        self.run_worker(
            self._activate_decision_or_select(item if isinstance(item, _BrowserListItem) else None)
        )

    async def _activate_decision_or_select(self, item: _BrowserListItem | None) -> None:
        if not widget_is_current(self):
            return
        if (
            self._activate_attention_decision is not None
            and await self._activate_attention_decision()
        ):
            return
        if not widget_is_current(self):
            return
        if item is not None and item.parent is self:
            self._select_task(item.task_id)


def _focus_row_text(row: TaskBrowserRow, *, status_bright: bool = False) -> Text:
    marker, marker_style = _focus_row_marker(row)
    stage = STEP_LABELS.get(row.step or "")
    state = _focus_row_state(row, stage)
    details = _focus_row_details(row, stage, state)
    if row.agent_processing and status_bright:
        details[0] = (details[0][0], f"{details[0][1]} bold")
    content = Text.assemble(
        (f"{marker} ", marker_style),
        (row.task_id, f"{PRIMARY_TEXT_COLOR} {STYLE_BOLD}"),
        (f" · {row.title}", PRIMARY_TEXT_COLOR),
        "\n  ",
    )
    for index, (detail, style) in enumerate(details):
        if index:
            content.append(" · ", style=SECONDARY_TEXT_COLOR)
        content.append(detail, style=style)
    return content


def _focus_row_marker(row: TaskBrowserRow) -> tuple[str, str]:
    if row.status in TASK_TERMINAL_STATUSES:
        return ROW_STATUS_MARKERS[row.status]
    if row.open_decision:
        return TASK_MARKER_DECISION, AMBER
    if row.diagnosed_stall:
        return TASK_MARKER_STALL, ALERT_COLOR
    if TASK_HOLD_INTERVENTION in row.holds:
        return TASK_MARKER_OPERATOR, STEEL_BLUE
    if TASK_HOLD_PAUSED in row.holds:
        return TASK_MARKER_PAUSED, AMBER
    return _status_marker(row.status)


def _status_marker(status: str) -> tuple[str, str]:
    return ROW_STATUS_MARKERS.get(status, (TASK_MARKER_CURRENT, STEEL_BLUE))


def _terminal_state(row: TaskBrowserRow) -> str:
    if row.status == TASK_STATUS_DONE:
        return "Done"
    stopped = f" · {STOPPED_DURING} {row.step}" if row.step else ""
    return f"Cancelled{stopped}"


def _focus_row_state(row: TaskBrowserRow, stage: str | None) -> str:
    if row.status in TASK_TERMINAL_STATUSES:
        return _terminal_state(row)
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
        _focus_row_marker(row)[1]
        if row.status in TASK_TERMINAL_STATUSES
        else SECONDARY_TEXT_COLOR
        if row.status == TASK_STATUS_QUEUED
        else AMBER
        if row.wait_reason or row.holds or row.open_decision
        else STEEL_BLUE
    )
    details: list[tuple[str, str]] = [(state, state_style)]
    if row.status in TASK_TERMINAL_STATUSES:
        return details
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


def _all_attached(widgets: Sequence[Widget]) -> bool:
    return all(widget.is_attached for widget in widgets)


class _RecentPager(Static):
    def __init__(
        self,
        *,
        widget_id: str,
        full_label: str,
        compact_label: str,
        page: Callable[[], Awaitable[None]],
    ) -> None:
        super().__init__(key_hint(full_label), id=widget_id, markup=False)
        self._full_label = full_label
        self._compact_label = compact_label
        self._page = page

    def show(self, *, enabled: bool, compact: bool) -> None:
        self.update(key_hint(self._compact_label if compact else self._full_label))
        self.set_class(not enabled, RECENT_INACTIVE_CLASS)

    async def on_click(self, event: events.Click) -> None:
        event.stop()
        if not self.has_class(RECENT_INACTIVE_CLASS):
            await self._page()


class _LoadMoreHint(Static):
    def __init__(self, *, load: Callable[[], Awaitable[None]]) -> None:
        super().__init__(id=MORE_HINT_ID, markup=False)
        self._load = load

    async def on_click(self, event: events.Click) -> None:
        event.stop()
        await self._load()


class QueuePanel(Vertical):
    """Render the Home task list with status filter, search, selection and Recent."""

    DEFAULT_CSS = """
    #queue { height: auto; }
    #task-browser-count {
        height: 1; margin-bottom: 1; color: $steel-blue;
        text-wrap: nowrap; text-overflow: ellipsis;
    }
    #task-browser-count.search-open { margin-bottom: 0; }
    #task-browser-search.focus-search { height: 1; border: none; padding: 0; margin-bottom: 1; }
    #task-browser-list {
        height: 12; min-height: 2; border: none; padding: 0; background: $background;
        scrollbar-size-vertical: $scrollbar-width;
    }
    #task-browser-list > ListItem {
        height: auto; min-height: 2; margin-bottom: 1; padding: 0;
        border: none; background: $background; color: $primary-text;
    }
    #task-browser-list.focus-list > ListItem { padding: 0 0 0 1; }
    #task-browser-list > ListItem:last-child { margin-bottom: 0; }
    #task-browser-list > ListItem > Static {
        height: auto; padding: 0; color: $primary-text;
        text-wrap: wrap; text-overflow: clip;
    }
    #task-browser-list > ListItem.-highlight {
        outline: none; background: $selected-surface; color: $primary-text; text-style: none;
    }
    #task-browser-list > ListItem.-highlight > Static {
        background: $selected-surface; color: $primary-text; text-style: none;
    }
    #task-browser-active { height: auto; }
    #task-browser-recent { display: none; height: auto; }
    #task-browser-recent-heading {
        height: 3; min-height: 3; margin: 0; border-top: solid $neutral-border; padding: 0;
    }
    #task-browser-recent-title { width: 1fr; height: 1; color: $steel-blue; }
    .recent-pager { width: auto; height: 1; margin-left: 1; color: $secondary-text; }
    .recent-pager:hover { text-style: bold; }
    .recent-pager.-inactive { color: $muted-text; text-style: dim; }
    .recent-pager.-inactive:hover { text-style: dim; }
    #task-browser-recent-list {
        height: auto; min-height: 2; border: none; padding: 0; background: $background;
        scrollbar-size-vertical: 0;
    }
    #task-browser-recent-list > ListItem {
        height: auto; min-height: 2; margin: 0 0 1 0; padding: 0 0 0 1;
        border: none; background: $background; color: $primary-text;
    }
    #task-browser-recent-list > ListItem:last-child { margin-bottom: 0; }
    #task-browser-recent-list > ListItem > Static {
        height: auto; padding: 0; color: $primary-text;
        text-wrap: wrap; text-overflow: clip;
    }
    #task-browser-recent-list > ListItem.-highlight {
        outline: none; background: $selected-surface; color: $primary-text; text-style: none;
    }
    #task-browser-recent-list > ListItem.-highlight > Static {
        background: $selected-surface; color: $primary-text; text-style: none;
    }
    #task-browser-list:focus { background-tint: transparent; }
    #task-browser-empty { height: auto; padding: 1; color: $muted-text; }
    #task-browser-more {
        width: auto; height: 1; margin-top: 1; text-wrap: nowrap; text-overflow: ellipsis;
    }
    """

    def __init__(self, context: WidgetContext) -> None:
        super().__init__(id="queue")
        self._widget_context = context
        self._filter = TaskBrowserFilter.ALL
        self._search = ""
        self._selected_task_id = context.focus_task
        self._selection_cleared = False
        self._loaded_page_count = 1
        self._recent_offset = 0
        self._terminal_count: int | None = None
        self._recent_window = RecentWindow(0, context.views.recent_task_window, 0)
        self._view: TaskBrowserView | None = None
        self._rendered: tuple[TaskBrowserView, str | None] | None = None
        self._refreshing = False
        self._refresh_lock = asyncio.Lock()
        self._list_render_versions: dict[str, int] = {}
        self._status_bright = False
        self._remove_motion_callback: Callable[[], None] | None = None
        self._count = Static("", id=COUNT_ID, markup=False)
        self._empty = Static("", id=EMPTY_ID, markup=False)
        self._task_list = _TaskBrowserListView(
            widget_id=TASK_LIST_ID,
            move_selection=self.move_selection,
            select_task=self.select_task,
            activate_attention_decision=context.activate_attention_decision,
        )
        self._recent_block = Vertical(id=TASK_RECENT_BLOCK_ID)
        self._active_block = Vertical(id=TASK_ACTIVE_BLOCK_ID)
        self._later = _RecentPager(
            widget_id=RECENT_LATER_ID,
            full_label=RECENT_LATER_LABEL,
            compact_label=RECENT_LATER_COMPACT_LABEL,
            page=lambda: self.page_recent(-1),
        )
        self._earlier = _RecentPager(
            widget_id=RECENT_EARLIER_ID,
            full_label=RECENT_EARLIER_LABEL,
            compact_label=RECENT_EARLIER_COMPACT_LABEL,
            page=lambda: self.page_recent(1),
        )
        self._recent_heading = Horizontal(
            Static(TASK_MARKER_RECENT, id=TASK_RECENT_TITLE_ID, markup=False),
            self._later,
            self._earlier,
            id=TASK_RECENT_HEADING_ID,
        )
        self._later.add_class(RECENT_PAGER_CLASS)
        self._earlier.add_class(RECENT_PAGER_CLASS)
        self._recent_list = _TaskBrowserListView(
            widget_id=TASK_RECENT_LIST_ID,
            move_selection=self.move_selection,
            select_task=self.select_task,
            activate_attention_decision=context.activate_attention_decision,
        )
        self._more_hint = _LoadMoreHint(load=self.load_more)

    def compose(self) -> ComposeResult:
        search = Input(placeholder="Search task ID or title", id=SEARCH_INPUT_ID)
        search.display = False
        search.add_class(SEARCH_FIELD_CLASS)
        self._task_list.add_class(FOCUS_LIST_CLASS)
        self._recent_list.add_class(FOCUS_LIST_CLASS)
        self._more_hint.display = False
        with self._active_block:
            yield self._count
            yield search
            yield self._empty
            yield self._task_list
            yield self._more_hint
        with self._recent_block:
            yield self._recent_heading
            yield self._recent_list

    async def on_mount(self) -> None:
        await self._refresh()
        self.run_worker(
            self._widget_context.on_change(self._refresh_if_current),
            exclusive=True,
        )
        self._remove_motion_callback = self._widget_context.on_motion(self._refresh_while_active)

    def on_resize(self, event: events.Resize) -> None:
        del event
        self._render_recent_pager()
        if self._view is not None and self._count.is_attached:
            self._render_count(self._view)
        if self._more_hint.is_attached:
            self._more_hint.update(load_more_hint(keys_only=self._keys_only()))

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
        await self._refresh(motion=True)
        if not self._render_targets_are_attached():
            return
        animated = self._widget_context.animations and self._widget_context.views.engine_up()
        status_bright = not self._status_bright if animated else False
        if animated or self._status_bright:
            self._apply_status_motion(status_bright)

    def _apply_status_motion(self, status_bright: bool) -> None:
        self._status_bright = status_bright
        for list_view in self._browser_list_views():
            for item in list_view.children:
                if isinstance(item, _BrowserListItem):
                    item.update_status_motion(status_bright)

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

    async def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != SEARCH_INPUT_ID:
            return
        await self._refresh(search=event.value, reset_pages=True)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == SEARCH_INPUT_ID:
            self._close_search()

    def focus_task_results(self) -> None:
        """Focus the visible task list, keeping empty result sets usable."""
        list_view = next((item for item in self._browser_list_views() if item.display), None)
        if list_view is not None:
            list_view.focus()
            return
        search = self.query_one(f"#{SEARCH_INPUT_ID}", Input)
        if search.display:
            search.focus()
            return
        app = cast("App[object]", self.app)
        footer_buttons = app.query("#focus-footer Button")
        app.set_focus(footer_buttons.first() if footer_buttons else None)

    def open_search(self) -> None:
        """Focus the task search from contextual keyboard navigation."""
        search = self.query_one(f"#{SEARCH_INPUT_ID}", Input)
        self._show_search_field(search, visible=True)
        search.focus()

    def cycle_status_filter(self) -> None:
        """Show the next task status filter, keeping the search text and the selection."""
        cycle = STATUS_FILTER_CYCLE
        position = cycle.index(self._filter) if self._filter in cycle else 0
        self._filter = cycle[(position + 1) % len(cycle)]
        self.run_worker(self._refresh(reset_pages=True), group=STATUS_FILTER_WORKER)

    def _close_search(self) -> None:
        search = self.query_one(f"#{SEARCH_INPUT_ID}", Input)
        self._show_search_field(search, visible=False)
        self.focus_task_results()

    def _show_search_field(self, search: Input, *, visible: bool) -> None:
        search.display = visible
        self._count.set_class(visible, SEARCH_OPEN_CLASS)

    async def load_more(self) -> None:
        """Append the next bounded browser page and return focus to the task list."""
        await self._refresh(append_page=True)
        self.focus_task_results()

    def request_more(self) -> None:
        """Load the next page from the `m` key while the hint is shown."""
        if self._more_hint.display:
            self.run_worker(self.load_more(), group=MORE_WORKER)

    async def activate_filter(self, task_filter: TaskBrowserFilter) -> None:
        """Navigate to a task category while retaining search and selected identity."""
        await self._refresh(task_filter=task_filter, reset_pages=True)
        self.focus_task_results()

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.list_view.id not in {TASK_LIST_ID, TASK_RECENT_LIST_ID} or self._refreshing:
            return
        if event.item is event.list_view.highlighted_child and isinstance(
            event.item, _BrowserListItem
        ):
            self.select_task(event.item.task_id)

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id in {TASK_LIST_ID, TASK_RECENT_LIST_ID} and isinstance(
            event.item, _BrowserListItem
        ):
            self.select_task(event.item.task_id)

    def on_key(self, event: events.Key) -> None:
        focused = cast("App[object]", self.app).focused
        if event.key == ESCAPE_KEY and isinstance(focused, Input) and focused.id == SEARCH_INPUT_ID:
            event.stop()
            focused.value = ""
            self._close_search()

    async def move_selection(self, step: int) -> None:
        """Move the task selection by `step` rows across the visible task lists."""
        rows = self._visible_rows()
        if not rows:
            return
        active_count = len(self._list_items(self._task_list))
        current = next(
            (index for index, item in enumerate(rows) if item.task_id == self._selected_task_id),
            None,
        )
        target = step_selection(active_count, self._recent_window, current, step)
        if target.recent_position is not None:
            self._recent_offset = self._recent_window.at_page_of(target.recent_position).offset
            await self._refresh()
            rows = self._visible_rows()
            active_count = len(self._list_items(self._task_list))
            visible_index = active_count + target.recent_position - self._recent_window.offset
        else:
            visible_index = cast("int", target.visible_index)
        if visible_index >= len(rows):
            return
        item = rows[visible_index]
        self.select_task(item.task_id)
        list_view = cast("ListView", item.parent)
        list_view.index = list_view.children.index(item)
        list_view.scroll_to_widget(item, animate=False, immediate=True)
        item.scroll_visible(animate=False, immediate=True)

    async def page_recent(self, direction: int) -> None:
        """Show the older (`1`) or newer (`-1`) Recent window without changing the selection."""
        window = self._recent_window
        offset = window.offset + direction * window.size
        if not 0 <= offset < window.total:
            return
        self._recent_offset = offset
        await self._refresh()
        self.focus_task_results()

    def _visible_rows(self) -> tuple[_BrowserListItem, ...]:
        return tuple(
            item for list_view in self._browser_list_views() for item in self._list_items(list_view)
        )

    @staticmethod
    def _list_items(list_view: ListView) -> tuple[_BrowserListItem, ...]:
        return tuple(item for item in list_view.children if isinstance(item, _BrowserListItem))

    def select_task(self, task_id: str) -> None:
        """Select a task and update the Home inspector without opening a route."""
        self._selected_task_id = task_id
        self._selection_cleared = False
        self._sync_selection(self._task_list, self._recent_list)
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
        self._view = replace(self._view, selected_task=row)

    def _browser_list_views(self) -> tuple[ListView, ...]:
        return tuple(
            list_view for list_view in (self._task_list, self._recent_list) if list_view.is_attached
        )

    def clear_selection(self) -> None:
        """Clear the Home inspector until an operator selects another task."""
        self._selected_task_id = None
        self._selection_cleared = True
        if self._view is not None:
            self._view = replace(self._view, selected_task=None)
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

    async def _refresh(
        self,
        *,
        append_page: bool = False,
        task_filter: TaskBrowserFilter | None = None,
        search: str | None = None,
        reset_pages: bool = False,
        motion: bool = False,
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
                    self._recent_offset = 0
                view = self._load_next_page() if append_page else self._load_pages()
                if self._selected_task_id is None and view.rows and not self._selection_cleared:
                    self._selected_task_id = view.rows[0].task_id
                    view = replace(view, selected_task=view.rows[0])
                self._view = view
                callback = self._widget_context.on_task_selected
                if callback is not None:
                    callback(self._selected_task_id)
                if not self._render_targets_are_attached():
                    return
                rendering = (view, self._selected_task_id)
                if motion and rendering == self._rendered:
                    return
                self._rendered = None
                await self._sync_task_lists(view)
                if not self._render_targets_are_attached():
                    return
                self._render_view(view)
                self._rendered = rendering
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
        view = pages[-1]
        selected_task = view.selected_task
        is_focus_recent = self._filter is TaskBrowserFilter.ALL and not self._search
        self._recent_window = RecentWindow(0, self._recent_window.size, 0)
        if is_focus_recent:
            recent_page, self._recent_window = self._load_recent_window()
            recent_rows = tuple(
                row for row in recent_page.rows if row.status in TASK_TERMINAL_STATUSES
            )[: self._recent_window.shown]
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
        return replace(view, rows=rows, selected_task=selected_task)

    def _load_recent_window(self) -> tuple[TaskBrowserView, RecentWindow]:
        views = self._widget_context.views
        size = views.recent_task_window
        page = self._recent_page(self._recent_offset)
        completed = self._terminal_count is not None and page.matching_count > self._terminal_count
        self._terminal_count = page.matching_count
        window = RecentWindow(
            0 if completed else self._recent_offset,
            size,
            min(page.matching_count, views.recent_task_limit),
        ).clamped()
        if window.offset != self._recent_offset:
            page = self._recent_page(window.offset)
        self._recent_offset = window.offset
        return page, window

    def _recent_page(self, offset: int) -> TaskBrowserView:
        views = self._widget_context.views
        size = views.recent_task_window
        return views.task_browser_pages(
            task_filter=TaskBrowserFilter.HISTORY,
            search=self._search,
            limit=size,
            page_count=offset // size + 1,
            selected_task_id=self._selected_task_id,
        )[-1]

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
            if view.filter is TaskBrowserFilter.ALL and not view.search
            else ()
        )
        recent_ids = {row.task_id for row in recent_rows}
        active_rows = tuple(row for row in view.rows if row.task_id not in recent_ids)
        active_list = self._task_list
        recent_list = self._recent_list
        active_list.display = bool(active_rows)
        self._recent_block.display = bool(recent_rows)
        await self._sync_list_rows(active_list, active_rows)
        await self._sync_list_rows(recent_list, recent_rows)
        self._render_recent_pager()
        if self._list_view_is_current(active_list) and self._list_view_is_current(recent_list):
            self._sync_selection(active_list, recent_list)
            focused = cast("App[object]", self.app).focused
            if (focused is active_list and not active_list.display) or (
                focused is recent_list and not self._recent_block.display
            ):
                self.focus_task_results()

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
                items[row.task_id] = _BrowserListItem(row)
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
        cast("App[object]", self.app).call_after_refresh(
            self._restore_list_position,
            list_view,
            anchor,
            anchor_offset,
            render_version,
            False,
        )

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
            cast("App[object]", self.app).call_after_refresh(
                self._restore_list_position,
                list_view,
                anchor,
                anchor_offset,
                render_version,
                True,
            )
            list_view.refresh(layout=True)

    def _render_recent_pager(self) -> None:
        window = self._recent_window
        controls = (self._later, self._earlier)
        if not _all_attached((*controls, self._recent_list)):
            return
        compact = 0 < self.size.width < RECENT_HEADING_FULL_COLUMNS
        self._later.show(enabled=window.has_later, compact=compact)
        self._earlier.show(enabled=window.has_earlier, compact=compact)
        for control in controls:
            control.display = window.paged
        self._recent_list.styles.min_height = (
            TASK_ROW_LINES * window.size + TASK_ROW_GAP_LINES * (window.size - 1)
            if window.paged and self._is_wide()
            else None
        )

    def _is_wide(self) -> bool:
        return any(
            ancestor.id == FOCUS_MAIN_CONTAINER_ID and ancestor.has_class("wide")
            for ancestor in self.ancestors
        )

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
        more = self._more_hint
        more.display = view.has_more
        more.update(load_more_hint(keys_only=self._keys_only()))
        empty = self._empty
        empty.display = not view.rows
        if view.rows:
            empty.update("")
        elif view.project_count == 0:
            empty.update("No tasks yet.")
        elif view.search:
            empty.update(f"No tasks match {view.search!r} in {FILTER_LABELS[view.filter]}.")
        else:
            empty.update(f"No {FILTER_LABELS[view.filter].lower()} tasks.")

    def _render_targets_are_attached(self) -> bool:
        return widget_is_current(self) and _all_attached(self._render_targets())

    def _render_targets(self) -> tuple[Widget, ...]:
        return (
            self._count,
            self._empty,
            self._more_hint,
            self._task_list,
            self._recent_heading,
            self._recent_list,
        )

    def _list_view_is_current(self, list_view: ListView) -> bool:
        return widget_is_current(self) and list_view.is_attached and self in list_view.ancestors

    def _render_count(self, view: TaskBrowserView) -> None:
        self._count.update(self._task_list_header(view))

    def _task_list_header(self, view: TaskBrowserView) -> Text:
        is_all = view.filter is TaskBrowserFilter.ALL
        return task_list_header(
            name=view.filter.value,
            marker=None if is_all else _status_marker(view.filter.value),
            search=view.search,
            width=self.size.width,
            keys_only=self._keys_only(),
        )

    def _keys_only(self) -> bool:
        return cast("App[object]", self.app).size.height <= HEADER_TINY_MAX_ROWS


class QueueWidget:
    """The `queue` dashboard widget plugin (06 §5.4)."""

    name = "queue"
    placement = WidgetPlacement.TASK_LIST

    def create(self, context: WidgetContext) -> Widget:
        """Build the task browser for `context` while retaining the `queue` entry point."""
        return QueuePanel(context)
