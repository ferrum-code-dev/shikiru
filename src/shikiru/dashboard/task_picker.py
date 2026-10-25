"""The Task picker popup: choose one project task by ID or title."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from rich.cells import cell_len
from rich.text import Text
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, ListItem, ListView, Rule, Static

from shikiru.plugins.widgets.queue import status_marker
from shikiru.plugins.widgets.queue_hint import MORE_KEY, load_more_hint
from shikiru.ui.keys import BOLD_DISABLED_KEY, key_hint
from shikiru.ui.popup import (
    POPUP_BODY_CLASS,
    POPUP_CLOSE_CLASS,
    POPUP_CSS,
    POPUP_FRAME_CLASS,
    POPUP_HEADER_CLASS,
    POPUP_RULE_CLASS,
    POPUP_TITLE_CLASS,
    PopupFrame,
    popup_close_label,
    prepare_popup,
)
from shikiru.ui.section import BOLD_PRIMARY, WrappedGroups, grey, key_label

if TYPE_CHECKING:
    from rich.console import Console, ConsoleOptions, RenderResult
    from textual import events
    from textual.app import ComposeResult
    from textual.binding import BindingType

    from shikiru.operator.queries import OperatorQueries, TaskSummary

PICKER_TITLE = "Switch task"
PICKER_FRAME = "task-picker-frame"
PICKER_CLOSE_BUTTON = "task-picker-close"
PICKER_SEARCH = "task-picker-search"
PICKER_LIST = "task-picker-list"
PICKER_EMPTY = "task-picker-empty"
PICKER_HINT = "task-picker-hint"
PICKER_PAGE_LIMIT = 20
PICKER_VISIBLE_ROWS = 10
SEARCH_KEY = "/"
SEARCH_LABEL = "search"
HINT_GAP = "   "
TINY_HINT_GAP = " "
COMPACT_CLASS = "compact"
TINY_CLASS = "tiny"
COMPACT_MAX_COLUMNS = 48
COMPACT_MAX_ROWS = 20
TINY_MAX_COLUMNS = 32
TINY_MAX_ROWS = 10
MIN_TITLE_CELLS = 8
NAME_GAP = " "
PAGE_OVERLAP_ROWS = 1
CLOSE_ACTION = "close"
MOVE_ACTION = "move"
PAGE_ACTION = "page"
JUMP_ACTION = "jump"
LOAD_MORE_ACTION = "load_more"
OPEN_SEARCH_ACTION = "open_search"


def picker_row_text(task: TaskSummary, width: int) -> Text:
    """Return `glyph T-n title status` on one line of `width` cells, dropping the status last."""
    marker, marker_style = status_marker(task.status.value)
    head = Text.assemble((marker, marker_style), NAME_GAP, (task.id, BOLD_PRIMARY), NAME_GAP)
    tail = Text.assemble(NAME_GAP, grey(task.status.value.replace("_", NAME_GAP)))
    room = width - cell_len(head.plain) - cell_len(tail.plain)
    if room < MIN_TITLE_CELLS:
        tail = Text("")
        room = width - cell_len(head.plain)
    title = Text(task.title.replace("\n", NAME_GAP))
    title.truncate(max(room, 0), overflow="ellipsis", pad=True)
    return Text.assemble(head, title, tail, no_wrap=True, overflow="ellipsis")


class _RowText:
    def __init__(self, task: TaskSummary) -> None:
        self._task = task

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        del console
        yield picker_row_text(self._task, options.max_width)


class PickerRow(ListItem):
    def __init__(self, task: TaskSummary) -> None:
        super().__init__(Static(_RowText(task), markup=False))
        self.task_id = task.id


class _PickerList(ListView):
    def on_resize(self) -> None:
        self.call_after_refresh(self._reveal_selection)

    def _reveal_selection(self) -> None:
        if self.highlighted_child is not None:
            self.highlighted_child.scroll_visible(animate=False)


class TaskPickerScreen(ModalScreen[str | None]):
    """List project tasks one per line; Enter dismisses with the chosen task ID."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", CLOSE_ACTION, "Close"),
        Binding("up", f"{MOVE_ACTION}(-1)", "Up", show=False, priority=True),
        Binding("down", f"{MOVE_ACTION}(1)", "Down", show=False, priority=True),
        Binding("pageup", f"{PAGE_ACTION}(-1)", "Page up", show=False, priority=True),
        Binding("pagedown", f"{PAGE_ACTION}(1)", "Page down", show=False, priority=True),
        Binding("home", f"{JUMP_ACTION}(-1)", "First", show=False, priority=True),
        Binding("end", f"{JUMP_ACTION}(1)", "Last", show=False, priority=True),
        Binding(SEARCH_KEY, OPEN_SEARCH_ACTION, "Search", show=False),
        Binding(MORE_KEY, LOAD_MORE_ACTION, "Load more", show=False),
    ]
    DEFAULT_CSS = (
        POPUP_CSS
        + f"""
    #{PICKER_SEARCH} {{ height: 1; border: none; padding: 0; margin-bottom: 1; }}
    #{PICKER_LIST} {{
        border: none; padding: 0; background: $surface;
        scrollbar-size-vertical: $scrollbar-width;
    }}
    #{PICKER_LIST} > ListItem {{
        height: 1; padding: 0; border: none; background: $surface; color: $primary-text;
    }}
    #{PICKER_LIST} > ListItem > Static {{
        width: 100%; height: 1; padding: 0; color: $primary-text;
        text-wrap: nowrap; text-overflow: ellipsis;
    }}
    #{PICKER_LIST} > ListItem.-highlight,
    #{PICKER_LIST} > ListItem.-highlight > Static {{
        outline: none; background: $selected-surface; color: $primary-text; text-style: none;
    }}
    #{PICKER_LIST}:focus {{ background-tint: transparent; }}
    #{PICKER_EMPTY} {{ height: auto; color: $muted-text; }}
    #{PICKER_HINT} {{ height: auto; margin-top: 1; }}
    #{PICKER_FRAME}.{COMPACT_CLASS} {{ margin: 0 1; padding: 0 1; }}
    #{PICKER_FRAME}.{TINY_CLASS} #{PICKER_SEARCH} {{ margin-bottom: 0; }}
    #{PICKER_FRAME}.{TINY_CLASS} #{PICKER_HINT} {{ margin-top: 0; }}
    """
    )

    def __init__(self, queries: OperatorQueries, current_task_id: str | None = None) -> None:
        super().__init__()
        prepare_popup(self)
        self._queries = queries
        self._current_task_id = current_task_id
        self._search = ""
        self._tasks: tuple[TaskSummary, ...] = ()
        self._has_more = False

    def compose(self) -> ComposeResult:
        """Render the header, the hidden search field, the task rows and the hint line."""
        with PopupFrame(widget_id=PICKER_FRAME, classes=POPUP_FRAME_CLASS):
            with Horizontal(classes=POPUP_HEADER_CLASS):
                yield Label(PICKER_TITLE, classes=POPUP_TITLE_CLASS)
                yield Button(popup_close_label(), id=PICKER_CLOSE_BUTTON, classes=POPUP_CLOSE_CLASS)
            yield Rule(classes=POPUP_RULE_CLASS)
            yield Static(id=PICKER_EMPTY, markup=False)
            yield _PickerList(id=PICKER_LIST, classes=POPUP_BODY_CLASS)
            yield Static(id=PICKER_HINT, markup=False)

    async def on_mount(self) -> None:
        """Load the first page, select the current task and size the popup."""
        await self._reload()
        self._list.focus()

    def on_resize(self, event: events.Resize) -> None:
        self._apply_compact_layout(event.size.width, event.size.height)

    def _apply_compact_layout(self, width: int, height: int) -> None:
        frame = self.query_one(f"#{PICKER_FRAME}", PopupFrame)
        compact = width <= COMPACT_MAX_COLUMNS or height <= COMPACT_MAX_ROWS
        tiny = height <= TINY_MAX_ROWS
        frame.set_class(compact, COMPACT_CLASS)
        frame.set_class(tiny, TINY_CLASS)
        frame.styles.max_height = max(1, height - frame.styles.margin.height)
        self._list.styles.height = min(PICKER_VISIBLE_ROWS, len(self._tasks))
        self._paint_hint()
        frame.reset_preferred_size()
        self.call_after_refresh(self._fit_content_to_viewport)

    def _fit_content_to_viewport(self) -> None:
        frame = self.query_one(f"#{PICKER_FRAME}", PopupFrame)
        height = self.size.height
        fixed_height = sum(
            child.region.height + child.styles.margin.height
            for child in frame.children
            if child is not self._list and child.display
        )
        available_rows = max(
            0,
            height - frame.styles.margin.height - frame.styles.gutter.height - fixed_height,
        )
        list_height = min(PICKER_VISIBLE_ROWS, len(self._tasks), available_rows)
        self._list.styles.height = list_height
        content_height = fixed_height + list_height + frame.styles.gutter.height
        frame.styles.max_height = max(
            1,
            min(height - frame.styles.margin.height, content_height),
        )
        frame.content_ready()

    def _keys_only(self, width: int, height: int) -> bool:
        return width <= TINY_MAX_COLUMNS or height <= TINY_MAX_ROWS

    @property
    def _list(self) -> ListView:
        return self.query_one(f"#{PICKER_LIST}", ListView)

    @property
    def _search_open(self) -> bool:
        return bool(self.query(f"#{PICKER_SEARCH}"))

    async def _reload(self) -> None:
        self._tasks = ()
        await self._list.clear()
        await self._append_page(preselect=self._current_task_id)

    async def _append_page(self, *, preselect: str | None = None) -> None:
        page = self._queries.search_task_targets(
            self._search, limit=PICKER_PAGE_LIMIT, offset=len(self._tasks)
        )
        known = {task.id for task in self._tasks}
        added = tuple(task for task in page.tasks if task.id not in known)
        self._tasks += added
        self._has_more = page.has_more
        await self._list.extend(PickerRow(task) for task in added)
        if self._list.index is None and self._tasks:
            self._list.index = self._index_of(preselect)
        self._paint_empty(page.project_count)
        self._apply_compact_layout(self.size.width, self.size.height)

    def _index_of(self, task_id: str | None) -> int:
        return next((index for index, task in enumerate(self._tasks) if task.id == task_id), 0)

    def _paint_empty(self, project_count: int) -> None:
        empty = self.query_one(f"#{PICKER_EMPTY}", Static)
        empty.display = not self._tasks
        if self._tasks:
            return
        message = "No tasks" if project_count == 0 else "No matches"
        empty.update(message)

    def _paint_hint(self) -> None:
        if not self.is_attached:
            return
        has_rows = bool(self._tasks)
        keys_only = self._keys_only(self.size.width, self.size.height)
        choose = (
            key_hint("Enter keep filter")
            if self._search_open
            else key_hint("Enter choose", enabled=has_rows)
        )
        groups = self._hint_groups(has_rows, choose, keys_only)
        gap = TINY_HINT_GAP if keys_only else HINT_GAP
        self.query_one(f"#{PICKER_HINT}", Static).update(WrappedGroups(groups, gap))

    def _hint_groups(self, has_rows: bool, choose: Text, keys_only: bool) -> tuple[Text, ...]:
        if keys_only:
            return (
                key_hint("↑↓", enabled=has_rows),
                Text.assemble(
                    key_hint("Enter", enabled=has_rows or self._search_open),
                    key_label(SEARCH_KEY, ""),
                    self._load_more_hint(keys_only=True),
                ),
            )
        return (
            key_hint("↑↓ select", enabled=has_rows),
            choose,
            key_label(SEARCH_KEY, SEARCH_LABEL),
            self._load_more_hint(keys_only=False),
        )

    def _load_more_hint(self, *, keys_only: bool) -> Text:
        if self._has_more:
            return load_more_hint(keys_only=keys_only)
        inert = Text(MORE_KEY, style=BOLD_DISABLED_KEY)
        return inert if keys_only else Text.assemble(inert, grey(" load more"))

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Leave Home and End to the search field's cursor while it has the keyboard."""
        del parameters
        return not (action == JUMP_ACTION and isinstance(self.focused, Input))

    def action_move(self, step: int) -> None:
        """Move the selection by `step` rows, stopping at the first and last loaded task."""
        if self._tasks:
            self._list.index = min(max((self._list.index or 0) + step, 0), len(self._tasks) - 1)

    def action_page(self, direction: int) -> None:
        """Move the selection by one visible page up (`-1`) or down (`1`)."""
        self.action_move(direction * max(1, self._list.size.height - PAGE_OVERLAP_ROWS))

    def action_jump(self, direction: int) -> None:
        """Select the first (`-1`) or last (`1`) loaded task."""
        if self._tasks:
            self._list.index = 0 if direction < 0 else len(self._tasks) - 1

    async def action_open_search(self) -> None:
        """Show the search field under the title and focus it."""
        if not self._search_open:
            search = Input(
                value=self._search,
                placeholder="Search task ID or title",
                id=PICKER_SEARCH,
            )
            await self.mount(search, before=f"#{PICKER_EMPTY}")
        self.query_one(f"#{PICKER_SEARCH}", Input).focus()
        self._apply_compact_layout(self.size.width, self.size.height)

    async def action_load_more(self) -> None:
        """Append the next bounded page, keeping the selection."""
        if self._has_more:
            await self._append_page()

    async def action_close(self) -> None:
        """Clear and close the search first; a second press closes the popup."""
        if not self._search_open:
            self.dismiss(None)
            return
        self._search = ""
        await self._close_search()
        await self._reload()

    async def _close_search(self) -> None:
        await self.query_one(f"#{PICKER_SEARCH}", Input).remove()
        self._apply_compact_layout(self.size.width, self.size.height)
        self._list.focus()

    async def on_input_changed(self, event: Input.Changed) -> None:
        """Narrow the list to tasks whose ID or title match the typed text."""
        search = event.value.strip()
        if search == self._search:
            return
        self._search = search
        await self._reload()

    async def on_input_submitted(self) -> None:
        """Close the search field and keep its filter."""
        await self._close_search()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Dismiss with the ID of the task chosen with Enter or a click."""
        if isinstance(event.item, PickerRow):
            self.dismiss(event.item.task_id)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Close the popup from its close control."""
        if event.button.id == PICKER_CLOSE_BUTTON:
            self.dismiss(None)
