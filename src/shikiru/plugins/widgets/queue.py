"""The `queue` widget: searchable task browser (docs/design/06-operator.md §5.4)."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import TYPE_CHECKING, cast

from textual.containers import Horizontal, HorizontalScroll, Vertical
from textual.widgets import Button, Input, ListItem, ListView, Static

from shikiru.dashboard.contract import (
    TASK_STATUS_QUEUED,
    TASK_TERMINAL_STATUSES,
    TaskBrowserFilter,
    TaskBrowserRow,
    TaskBrowserView,
)

if TYPE_CHECKING:
    from textual import events
    from textual.app import App, ComposeResult
    from textual.widget import Widget

    from shikiru.dashboard.contract import WidgetContext

BROWSER_PAGE_SIZE = 24
SEARCH_INPUT_ID = "task-browser-search"
TASK_LIST_ID = "task-browser-list"
TASK_PREVIEW_ID = "task-browser-preview"
COUNT_ID = "task-browser-count"
EMPTY_ID = "task-browser-empty"
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
    def __init__(self, row: TaskBrowserRow) -> None:
        super().__init__(Static(_row_text(row), markup=False), id=f"task-row-{row.task_id}")
        self.task_id = row.task_id
        self.row = row

    def update_row(self, row: TaskBrowserRow) -> bool:
        if row == self.row:
            return False
        self.row = row
        self.query_one(Static).update(_row_text(row))
        return True


def _row_text(row: TaskBrowserRow) -> str:
    group = f"{row.group_label} · " if row.group_label else ""
    state = row.status.replace("_", " ")
    step = f" · {row.step}" if row.step else ""
    role = f" · {row.acting_role}" if row.acting_role else ""
    state_line = f"{group}{state}{step} · iteration {row.iteration}{role} · {row.age}"
    signals: list[str] = []
    if row.open_decision:
        signals.append("decision")
    if row.diagnosed_stall:
        signals.append("stalled")
    if row.holds:
        signals.extend(row.holds)
    if row.wait_reason:
        signals.append(row.wait_reason)
    if row.status == TASK_STATUS_QUEUED:
        signals.append(f"priority {row.priority}")
    if row.dependencies:
        signals.append(f"depends on {', '.join(row.dependencies)}")
    blocker = f" · {' · '.join(signals)}" if signals else ""
    return f"{row.task_id} · {row.title}\n{state_line}{blocker}"


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
    #task-browser-count { height: 1; color: $primary-text; }
    #task-browser-list { height: 12; min-height: 3; border: solid $neutral-border; }
    #task-browser-empty { height: auto; padding: 1; color: $muted-text; }
    #task-browser-more { width: auto; min-width: 1; }
    #task-browser-preview { height: auto; padding: 1; border: round $neutral-border; }
    #task-browser-reveal { width: auto; min-width: 1; }
    """

    def __init__(self, context: WidgetContext) -> None:
        super().__init__(id="queue")
        self._widget_context = context
        self._filter = TaskBrowserFilter.ALL
        self._search = ""
        self._selected_task_id = context.focus_task
        self._loaded_page_count = 1
        self._view: TaskBrowserView | None = None
        self._refreshing = False
        self._refresh_lock = asyncio.Lock()
        self._list_render_version = 0

    def compose(self) -> ComposeResult:
        with Horizontal(id="task-browser-tools"):
            yield Input(placeholder="Search task ID or title", id=SEARCH_INPUT_ID)
            yield Button("Clear", id=CLEAR_BUTTON_ID)
        with HorizontalScroll(id="task-browser-filters"):
            for _, button_id, label in FILTER_BUTTONS:
                yield Button(label, id=button_id)
        yield Static("", id=COUNT_ID, markup=False)
        yield Static("", id=EMPTY_ID, markup=False)
        yield ListView(id=TASK_LIST_ID)
        yield Button("Load more", id=MORE_BUTTON_ID)
        yield Static("Select a task to keep its context here.", id=TASK_PREVIEW_ID, markup=False)
        yield Button("Reveal selected task", id=REVEAL_BUTTON_ID)

    async def on_mount(self) -> None:
        await self._refresh()
        self.run_worker(self._widget_context.on_change(self._refresh), exclusive=True)

    async def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != SEARCH_INPUT_ID:
            return
        await self._refresh(search=event.value, reset_pages=True)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == SEARCH_INPUT_ID:
            self.query_one(f"#{TASK_LIST_ID}", ListView).focus()

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

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.list_view.id != TASK_LIST_ID or self._refreshing:
            return
        if isinstance(event.item, _BrowserListItem):
            self._select_task(event.item.task_id)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id == TASK_LIST_ID and isinstance(event.item, _BrowserListItem):
            self._select_task(event.item.task_id)

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
            self.query_one(f"#{TASK_LIST_ID}", ListView).focus()

    def _select_task(self, task_id: str) -> None:
        self._selected_task_id = task_id
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
                list_view = self.query_one(f"#{TASK_LIST_ID}", ListView)
                await self._sync_list_rows(list_view, view.rows)
                self._sync_selection(list_view, view.rows)
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
        return replace(pages[-1], rows=tuple(row for page in pages for row in page.rows))

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
        current_ids = tuple(items)
        desired_ids = tuple(row.task_id for row in rows)
        if current_ids == desired_ids:
            anchor, anchor_offset = self._visible_anchor(list_view, tuple(items.values()))
            changed = False
            for row in rows:
                changed = items[row.task_id].update_row(row) or changed
            if changed:
                self._restore_list_position_after_refresh(
                    list_view,
                    tuple(items.values()),
                    anchor,
                    anchor_offset,
                )
            return

        wanted_ids = set(desired_ids)
        anchor, anchor_offset = self._visible_anchor(
            list_view,
            tuple(item for task_id, item in items.items() if task_id in wanted_ids),
        )
        removed = [item for task_id, item in items.items() if task_id not in wanted_ids]
        if removed:
            await asyncio.gather(*(item.remove() for item in removed))
            items = {task_id: item for task_id, item in items.items() if task_id in wanted_ids}

        new_items = [_BrowserListItem(row) for row in rows if row.task_id not in items]
        items.update({item.task_id: item for item in new_items})
        for row in rows:
            items[row.task_id].update_row(row)
        if new_items:
            await list_view.extend(new_items)
        for index, row in enumerate(rows):
            item = items[row.task_id]
            if list_view.children[index] is not item:
                list_view.move_child(item, before=index)
        self._restore_list_position_after_refresh(
            list_view,
            tuple(items.values()),
            anchor,
            anchor_offset,
        )

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
        items: tuple[_BrowserListItem, ...],
        anchor: _BrowserListItem | None = None,
        anchor_offset: int = 0,
    ) -> None:
        self._list_render_version += 1
        selected_item = next(
            (item for item in items if item.task_id == self._selected_task_id),
            None,
        )
        list_view.call_after_refresh(
            self._restore_list_position,
            list_view,
            selected_item,
            anchor,
            anchor_offset,
            self._list_render_version,
        )

    def _restore_list_position(
        self,
        list_view: ListView,
        selected_item: _BrowserListItem | None,
        anchor: _BrowserListItem | None,
        anchor_offset: int,
        render_version: int,
    ) -> None:
        if render_version != self._list_render_version:
            return
        content_region = list_view.scrollable_content_region
        if anchor is not None and anchor.parent is list_view:
            anchor_offset_now = anchor.region.y - content_region.y
            list_view.scroll_to(
                y=list_view.scroll_y + anchor_offset_now - anchor_offset,
                animate=False,
                immediate=True,
            )
        if (
            selected_item is not None
            and selected_item.parent is list_view
            and not selected_item.region.overlaps(list_view.scrollable_content_region)
        ):
            list_view.scroll_to_widget(selected_item, animate=False, immediate=True)

    def _sync_selection(self, list_view: ListView, rows: tuple[TaskBrowserRow, ...]) -> None:
        selected_index = next(
            (index for index, row in enumerate(rows) if row.task_id == self._selected_task_id),
            None,
        )
        if list_view.index != selected_index:
            list_view.index = selected_index

    def _render_view(self, view: TaskBrowserView) -> None:
        count = f"{FILTER_LABELS[view.filter]} · {len(view.rows)} of {view.matching_count}"
        if view.search:
            count += f" · Search: {view.search}"
        self.query_one(f"#{COUNT_ID}", Static).update(count)
        self.query_one(f"#{CLEAR_BUTTON_ID}", Button).disabled = not bool(view.search)
        self.query_one(f"#{MORE_BUTTON_ID}", Button).disabled = not view.has_more
        for task_filter, button_id, label in FILTER_BUTTONS:
            button = self.query_one(f"#{button_id}", Button)
            button.label = f"> {label}" if task_filter is view.filter else label
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
            f"{state}{step} · iteration {selected.iteration}{role} · age {selected.age}"
            f"{blocker_text}{priority}{dependencies}{note}"
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

    def create(self, context: WidgetContext) -> Widget:
        """Build the task browser for `context` while retaining the `queue` entry point."""
        return QueuePanel(context)
