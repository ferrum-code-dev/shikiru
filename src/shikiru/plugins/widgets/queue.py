"""The `queue` widget: searchable task browser (docs/design/06-operator.md §5.4)."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from math import floor
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
from shikiru.ui.tokens import (
    ALERT_COLOR,
    AMBER,
    PRIMARY_TEXT_COLOR,
    SAGE,
    SECONDARY_TEXT_COLOR,
    STEEL_BLUE,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from textual import events
    from textual.app import App, ComposeResult
    from textual.widget import Widget

    from shikiru.dashboard.contract import WidgetContext

BROWSER_PAGE_SIZE = 24
RECENT_TASK_LIMIT = 1
REFERENCE_FONT_SIZE_PIXELS = 12
REFERENCE_LINE_HEIGHT_RATIO = 1.65
REFERENCE_LINE_HEIGHT_PIXELS = REFERENCE_FONT_SIZE_PIXELS * REFERENCE_LINE_HEIGHT_RATIO
TASK_ROW_VERTICAL_PADDING_PIXELS = 12
RECENT_RULE_MARGIN_PIXELS = 12
RECENT_RULE_BORDER_PIXELS = 1
RECENT_RULE_VERTICAL_MARGINS = 2
HALF_CELL_ROUNDING = 0.5
TASK_STATE_DECISION_NEEDED = "decision needed"
WAIT_REASON_DEPENDENCY = "dependency"
REFERENCE_ROW_GAP_CLASS = "reference-row-gap"
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
TASK_RECENT_HEADING_ID = "task-browser-recent-heading"
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
FILTER_BUTTONS = (
    (TaskBrowserFilter.ACTIVE, "task-filter-active", "Active"),
    (TaskBrowserFilter.WAITING, "task-filter-waiting", "Waiting"),
    (TaskBrowserFilter.HISTORY, "task-filter-history", "History"),
    (TaskBrowserFilter.ALL, "task-filter-all", "All"),
)
FILTER_BY_BUTTON_ID = {button_id: task_filter for task_filter, button_id, _ in FILTER_BUTTONS}
FILTER_LABELS = {task_filter: label for task_filter, _, label in FILTER_BUTTONS}


class _BrowserListItem(ListItem):
    def __init__(
        self,
        row: TaskBrowserRow,
        *,
        focus_layout: bool,
    ) -> None:
        render = _focus_row_text(row) if focus_layout else _row_text(row)
        super().__init__(Static(render, markup=False), id=f"task-row-{row.task_id}")
        self.task_id = row.task_id
        self.row = row
        self.focus_layout = focus_layout

    def update_row(self, row: TaskBrowserRow) -> bool:
        if row == self.row:
            return False
        self.row = row
        render = _focus_row_text(row) if self.focus_layout else _row_text(row)
        self.query_one(Static).update(render)
        return True


class _RecentHeading(ListItem):
    def __init__(self) -> None:
        super().__init__(
            Static(TASK_MARKER_RECENT, markup=False),
            id=TASK_RECENT_HEADING_ID,
            classes="recent-heading",
            disabled=True,
        )


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


def _focus_row_text(row: TaskBrowserRow) -> Text:
    marker, marker_style = _focus_row_marker(row)
    stage = STEP_LABELS.get(row.step or "")
    state = _focus_row_state(row, stage)
    details = _focus_row_details(row, stage, state)
    content = Text.assemble(
        (f"{marker} ", marker_style),
        (f"{row.task_id} · {row.title}", STEEL_BLUE if row.open_decision else PRIMARY_TEXT_COLOR),
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
        state = "done"
    elif row.status == TASK_STATUS_CANCELLED:
        state = "cancelled"
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
    details: list[tuple[str, str]] = [
        (state, AMBER if row.wait_reason or row.holds else STEEL_BLUE),
    ]
    if row.acting_role and not stage:
        details.append((row.acting_role.title(), SECONDARY_TEXT_COLOR))
    if row.dependencies and row.wait_reason != WAIT_REASON_DEPENDENCY:
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
    #task-browser-count { height: 1; color: $steel-blue; }
    #task-browser-list {
        height: 12; min-height: 3; border: none; padding: 0; background: $background;
        scrollbar-size-vertical: 0;
    }
    #task-browser-list > ListItem {
        height: auto; min-height: 2; margin-bottom: 1; padding: 0;
        border: none; background: $background; color: $primary-text;
    }
    #task-browser-list.focus-list > ListItem { margin-bottom: 0; }
    #task-browser-list > ListItem > Static {
        height: auto; padding: 0; color: $primary-text;
        text-wrap: wrap; text-overflow: clip;
    }
    #task-browser-list > ListItem.-highlight {
        outline: solid $steel-blue; background: $selected-surface; color: $primary-text;
        text-style: none;
    }
    #task-browser-list > ListItem.-highlight > Static {
        background: $selected-surface; color: $primary-text; text-style: none;
    }
    #task-browser-list > ListItem.recent-heading {
        height: 2; min-height: 2; margin: 0; border-top: solid $neutral-border;
        padding: 0; color: $muted-text;
    }
    #task-browser-list > ListItem.recent-heading > Static {
        height: 1; padding: 0; color: $muted-text; text-style: none;
    }
    #task-browser-list > ListItem:last-child { margin-bottom: 0; }
    #task-browser-list.focus-list > ListItem.reference-row-gap { margin-bottom: 1; }
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
        self._loaded_page_count = 1
        self._view: TaskBrowserView | None = None
        self._refreshing = False
        self._refresh_lock = asyncio.Lock()
        self._list_render_version = 0
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
        task_list = ListView(id=TASK_LIST_ID)
        if self._focus_layout:
            task_list.add_class(FOCUS_LIST_CLASS)
        yield task_list
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
        await self._refresh()
        self.run_worker(
            self._widget_context.on_change(self._refresh_while_active),
            exclusive=True,
        )
        self._remove_motion_callback = self._widget_context.on_motion(self._refresh_while_active)

    def on_unmount(self) -> None:
        if self._remove_motion_callback is not None:
            self._remove_motion_callback()
            self._remove_motion_callback = None

    async def _refresh_while_active(self) -> None:
        if not self.is_mounted:
            return
        app = cast("App[object]", self.app)
        if app.is_running and self.screen.is_current:
            await self._refresh()

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
        if event.list_view.id != TASK_LIST_ID or self._refreshing:
            return
        if isinstance(event.item, _BrowserListItem):
            self._select_task(event.item.task_id)

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id == TASK_LIST_ID and isinstance(event.item, _BrowserListItem):
            self._select_task(event.item.task_id)
            open_detail = self._widget_context.open_task_detail
            if open_detail is not None:
                await open_detail(event.item.task_id)

    def on_key(self, event: events.Key) -> None:
        focused = cast("App[object]", self.app).focused
        if event.key != ESCAPE_KEY or not isinstance(focused, Input):
            return
        search = focused
        if search.id != SEARCH_INPUT_ID:
            return
        event.stop()
        if search.value:
            search.value = ""
        else:
            if self._focus_layout:
                search.display = False
            self.query_one(f"#{TASK_LIST_ID}", ListView).focus()

    def _select_task(self, task_id: str) -> None:
        self._selected_task_id = task_id
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
            self._refreshing = True
            try:
                if task_filter is not None:
                    self._filter = task_filter
                if search is not None:
                    self._search = search
                if reset_pages:
                    self._loaded_page_count = 1
                view = self._load_next_page() if append_page else self._load_pages()
                if self._selected_task_id is None and view.rows:
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
                list_view = self.query_one(f"#{TASK_LIST_ID}", ListView)
                await self._sync_list_rows(list_view, view.rows)
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
        if self._focus_layout and self._filter is TaskBrowserFilter.ALL and not self._search:
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
            for ancestor in recent_page.ancestor_context:
                if ancestor.task_id not in matched_ids:
                    ancestors[ancestor.task_id] = ancestor
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
        return replace(
            view,
            rows=rows,
            selected_task=selected_task,
            ancestor_context=tuple(ancestors.values()),
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

    async def _sync_list_rows(self, list_view: ListView, rows: tuple[TaskBrowserRow, ...]) -> None:
        items = {
            item.task_id: item for item in list_view.children if isinstance(item, _BrowserListItem)
        }
        heading = next(
            (item for item in list_view.children if isinstance(item, _RecentHeading)),
            None,
        )
        recent_task_id = self._recent_task_id(rows)
        desired_ids = tuple(row.task_id for row in rows)
        wanted_ids = set(desired_ids)
        if self._has_current_list_order(list_view, rows, items, heading, recent_task_id):
            anchor, anchor_offset = self._visible_anchor(list_view, tuple(items.values()))
            changed = self._update_browser_items(items, rows)
            self._sync_selection(list_view, rows)
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
        heading = await self._remove_stale_list_items(
            items,
            heading,
            wanted_ids,
            recent_task_id,
        )
        self._update_browser_items(items, rows)
        desired_children, heading = self._ordered_list_items(
            rows,
            items,
            heading,
            recent_task_id,
        )
        await self._place_list_items(list_view, desired_children)
        self._sync_selection(list_view, rows)
        self._restore_list_position_after_refresh(
            list_view,
            anchor,
            anchor_offset,
        )

    def _has_current_list_order(
        self,
        list_view: ListView,
        rows: tuple[TaskBrowserRow, ...],
        items: dict[str, _BrowserListItem],
        heading: _RecentHeading | None,
        recent_task_id: str | None,
    ) -> bool:
        if tuple(items) != tuple(row.task_id for row in rows):
            return False
        if recent_task_id is None:
            return heading is None and len(list_view.children) == len(rows)
        if heading is None:
            return False
        recent_index = next(
            index for index, row in enumerate(rows) if row.task_id == recent_task_id
        )
        return list_view.children[recent_index] is heading

    def _recent_task_id(self, rows: tuple[TaskBrowserRow, ...]) -> str | None:
        if (
            not self._focus_layout
            or self._view is None
            or self._view.filter is not TaskBrowserFilter.ALL
            or self._search
        ):
            return None
        return next((row.task_id for row in rows if row.status in TASK_TERMINAL_STATUSES), None)

    async def _remove_stale_list_items(
        self,
        items: dict[str, _BrowserListItem],
        heading: _RecentHeading | None,
        wanted_ids: set[str],
        recent_task_id: str | None,
    ) -> _RecentHeading | None:
        removed: list[ListItem] = [
            item for task_id, item in items.items() if task_id not in wanted_ids
        ]
        if heading is not None and recent_task_id is None:
            removed.append(heading)
        if removed:
            await asyncio.gather(*(item.remove() for item in removed))
            retained = {task_id: item for task_id, item in items.items() if task_id in wanted_ids}
            items.clear()
            items.update(retained)
            if recent_task_id is None:
                heading = None
        return heading

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

    def _ordered_list_items(
        self,
        rows: tuple[TaskBrowserRow, ...],
        items: dict[str, _BrowserListItem],
        heading: _RecentHeading | None,
        recent_task_id: str | None,
    ) -> tuple[list[ListItem], _RecentHeading | None]:
        if recent_task_id is not None and heading is None:
            heading = _RecentHeading()
        desired_children: list[ListItem] = []
        for row in rows:
            if row.task_id == recent_task_id and heading is not None:
                desired_children.append(heading)
            desired_children.append(items[row.task_id])
        return desired_children, heading

    async def _place_list_items(
        self,
        list_view: ListView,
        desired_children: list[ListItem],
    ) -> None:
        additions = [child for child in desired_children if child.parent is None]
        if additions:
            await list_view.extend(additions)
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
        self._list_render_version += 1
        callback_args = (
            list_view,
            anchor,
            anchor_offset,
            self._list_render_version,
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
        changed = self._focus_layout and self._apply_reference_row_spacing(list_view)
        if changed:
            cast("App[object]", self.app).call_after_refresh(
                self._restore_list_position,
                list_view,
                anchor,
                anchor_offset,
                render_version,
                False,
            )
            return
        if render_version != self._list_render_version:
            return
        if anchor is not None and anchor.parent is list_view:
            anchor_index = list_view.children.index(anchor)
            anchor_position = sum(
                item.region.height
                + (
                    int(item.has_class(REFERENCE_ROW_GAP_CLASS))
                    if list_view.has_class(FOCUS_LIST_CLASS)
                    else 1
                )
                for item in list_view.children[:anchor_index]
            )
            list_view.scroll_to(
                y=anchor_position - anchor_offset,
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
        if self._focus_layout and not settled:
            cast("App[object]", self.app).call_after_refresh(
                self._restore_list_position,
                list_view,
                anchor,
                anchor_offset,
                render_version,
                True,
            )
            list_view.refresh(layout=True)

    def _apply_reference_row_spacing(self, list_view: ListView) -> bool:
        if self._view is None or self._view.filter is not TaskBrowserFilter.ALL or self._search:
            changed = False
            for item in list_view.children:
                changed = item.has_class(REFERENCE_ROW_GAP_CLASS) or changed
                item.remove_class(REFERENCE_ROW_GAP_CLASS)
            return changed
        source_height_pixels = 0.0
        native_height_cells = 0
        changed = False
        for index, item in enumerate(list_view.children):
            if isinstance(item, _BrowserListItem):
                line_count = item.query_one(Static).region.height
                source_height_pixels += (
                    line_count * REFERENCE_LINE_HEIGHT_PIXELS + TASK_ROW_VERTICAL_PADDING_PIXELS
                )
            elif isinstance(item, _RecentHeading):
                source_height_pixels += (
                    RECENT_RULE_MARGIN_PIXELS * RECENT_RULE_VERTICAL_MARGINS
                    + RECENT_RULE_BORDER_PIXELS
                    + REFERENCE_LINE_HEIGHT_PIXELS
                )
                line_count = item.region.height
            else:
                raise TypeError(type(item).__name__)
            target_height_cells = floor(
                source_height_pixels / REFERENCE_LINE_HEIGHT_PIXELS + HALF_CELL_ROUNDING
            )
            gap_cells = target_height_cells - native_height_cells - line_count
            has_gap = gap_cells > 0 and index < len(list_view.children) - 1
            changed = item.has_class(REFERENCE_ROW_GAP_CLASS) != has_gap or changed
            item.set_class(has_gap, REFERENCE_ROW_GAP_CLASS)
            native_height_cells = target_height_cells
        return changed

    def _sync_selection(self, list_view: ListView, rows: tuple[TaskBrowserRow, ...]) -> None:
        selected_index = next(
            (index for index, row in enumerate(rows) if row.task_id == self._selected_task_id),
            None,
        )
        recent_index = next(
            (index for index, row in enumerate(rows) if row.status in TASK_TERMINAL_STATUSES),
            None,
        )
        if (
            self._focus_layout
            and self._view is not None
            and self._view.filter is TaskBrowserFilter.ALL
            and not self._search
            and selected_index is not None
            and recent_index is not None
            and selected_index >= recent_index
        ):
            selected_index += 1
        if list_view.index != selected_index:
            list_view.index = selected_index

    def _render_view(self, view: TaskBrowserView) -> None:
        count = (
            "TASKS · Decision first"
            if self._focus_layout and view.filter is TaskBrowserFilter.ALL and not view.search
            else f"TASKS · {FILTER_LABELS[view.filter]} · {len(view.rows)} of {view.matching_count}"
        )
        if view.search:
            count += f" · Search: {view.search}"
        if view.selected_task_missing and view.selected_task is not None:
            count += f" · selected {view.selected_task.task_id} remains outside this view"
        self.query_one(f"#{COUNT_ID}", Static).update(count)
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
