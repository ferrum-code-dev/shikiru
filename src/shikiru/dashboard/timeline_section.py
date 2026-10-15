"""One task timeline: a category's rows, cursor stack, filters and follow state."""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import TYPE_CHECKING, ClassVar, cast

from rich.text import Text
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Static

from shikiru.dashboard.row_list import RowList
from shikiru.dashboard.section_rows import timeline_cells
from shikiru.dashboard.timeline_filter import TimelineFilterBar
from shikiru.errors import ShikiruError
from shikiru.ui.screen import widget_is_current

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Callable

    from rich.console import Console, ConsoleOptions, RenderResult
    from textual.app import App, ComposeResult
    from textual.binding import BindingType
    from textual.widget import Widget

    from shikiru.dashboard.contract import (
        TaskTimelineCategory,
        TaskTimelineEntryView,
        TaskTimelinePageView,
        WidgetContext,
    )

NO_TIMELINE_ROWS = "No recorded entries match."
FOLLOWING_LABEL = "following"
PAUSED_LABEL = "paused"
REFRESH_FAILED = "Evidence timeline refresh failed"
FILTER_KEY = "f"
OPEN_FILTER_ACTION = "open_filter"
SELECTED_MAX_LINES = 4
ELLIPSIS = "\N{HORIZONTAL ELLIPSIS}"


@dataclass(frozen=True)
class TimelineIds:
    """The widget ids of one timeline section, derived from a shared prefix."""

    root: str
    filters: str
    list: str
    selected: str

    @classmethod
    def with_prefix(cls, prefix: str) -> TimelineIds:
        """Build the ids `<prefix>-<part>` for every part of a timeline section."""
        return cls(*(f"{prefix}-{part.name}" for part in fields(cls)))


@dataclass(frozen=True)
class TimelineRequest:
    """The page a timeline asks the views for."""

    cursor: str | None
    role: str | None
    turn: str | None
    category: str


@dataclass(frozen=True)
class TimelineHost:
    """What a timeline needs from the panel that owns it."""

    task_id: Callable[[], str | None]
    lock: asyncio.Lock
    report: Callable[[str], None]


def screen_is_active(widget: Widget) -> bool:
    """Whether the widget's screen is the current one in a running app."""
    return widget_is_current(widget) and cast("App[None]", widget.app).is_running


@dataclass(frozen=True)
class _SelectedEntry:
    text: Text

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        lines = self.text.wrap(console, options.max_width)
        shown = lines[:SELECTED_MAX_LINES]
        if len(lines) > SELECTED_MAX_LINES:
            last = shown[-1]
            last.rstrip()
            last.truncate(max(options.max_width - 1, 1))
            last.append(ELLIPSIS)
        yield from shown


class TimelineSection(Vertical):
    """Own one timeline's rows, paging, filters and follow state; `shown` while open."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding(FILTER_KEY, OPEN_FILTER_ACTION, "Filter", show=False)
    ]
    DEFAULT_CSS = """
    TimelineSection { height: auto; }
    TimelineSection .timeline-selected {
        height: auto; padding: 0 1; margin: 1 0 0 0;
        color: $primary-text; background: $detail-surface; text-style: italic;
    }
    """

    class StatusChanged(Message):
        """The section's `following` or `paused` status changed; `None` clears it."""

        def __init__(self, section: TimelineSection, status: str | None) -> None:
            super().__init__()
            self.section = section
            self.status = status

    def __init__(
        self,
        context: WidgetContext,
        host: TimelineHost,
        *,
        ids: TimelineIds,
        category: TaskTimelineCategory,
        filterable: bool,
    ) -> None:
        super().__init__(id=ids.root)
        self.ids = ids
        self._widget_context = context
        self._host = host
        self._category = category.value
        self._filterable = filterable
        self._page: TaskTimelinePageView | None = None
        self._generation = 0
        self.shown = False
        self._reset_state()

    def compose(self) -> ComposeResult:
        yield RowList(widget_id=self.ids.list, edge_paging=True)
        selected = Static("", id=self.ids.selected, classes="timeline-selected", markup=False)
        selected.display = False
        yield selected
        if self._filterable:
            yield TimelineFilterBar(widget_id=self.ids.filters)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        del parameters
        return self._filterable if action == OPEN_FILTER_ACTION else True

    def action_open_filter(self) -> None:
        """Show the filter bar and move focus into it."""
        bar = self.query_one(f"#{self.ids.filters}", TimelineFilterBar)
        bar.display = True
        bar.focus()

    def close_filter(self) -> bool:
        """Hide the focused filter bar and return focus to the list; False when it has none."""
        if not self._filterable:
            return False
        bar = self.query_one(f"#{self.ids.filters}", TimelineFilterBar)
        if not bar.has_focus:
            return False
        self.query_one(f"#{self.ids.list}", RowList).focus()
        bar.display = bar.active
        return True

    @property
    def request(self) -> TimelineRequest:
        """The page the current cursor, filters and category ask for."""
        return TimelineRequest(
            None if self._following else self._cursors[-1],
            self._role,
            self._turn,
            self._category,
        )

    @property
    def generation(self) -> int:
        """Bumped whenever the request changes, so older replies can be dropped."""
        return self._generation

    @property
    def following(self) -> bool:
        """Whether the section tracks the latest entries."""
        return self._following

    @property
    def page(self) -> TaskTimelinePageView | None:
        """The last page painted."""
        return self._page

    def reset(self) -> None:
        """Forget the cursor, filters and rows, as for a newly selected task."""
        self._reset_state()
        self._page = None
        self._generation += 1
        if not self.is_mounted:
            return
        selected = self.query_one(f"#{self.ids.selected}", Static)
        selected.update("")
        selected.display = False
        if self._filterable:
            bar = self.query_one(f"#{self.ids.filters}", TimelineFilterBar)
            bar.reset()
            bar.display = False
        self.post_message(self.StatusChanged(self, None))
        self.query_one(f"#{self.ids.list}", RowList).set_rows(
            (), selected=0, empty=NO_TIMELINE_ROWS
        )

    def show_page(self, page: TaskTimelinePageView | None) -> None:
        """Paint a page of rows, keeping the selected entry when it is still listed."""
        self._page = page
        entries = page.entries if page is not None else ()
        selected = (
            len(entries) - 1
            if self._select_last
            else next(
                (
                    index
                    for index, item in enumerate(entries)
                    if item.identity == self._selected_identity
                ),
                0,
            )
        )
        if self._select_last and selected > 0:
            self._selected_identity = entries[selected].identity
            self._following = False
        self._select_last = False
        self._remember_turns(entries)
        self.query_one(f"#{self.ids.list}", RowList).set_rows(
            [timeline_cells(item) for item in entries], selected=selected, empty=NO_TIMELINE_ROWS
        )
        self._announce_status()
        self._paint_selection()

    def pause_follow(self) -> None:
        """Stop following the latest entries until the operator asks for them again."""
        if not self._following:
            return
        self._following = False
        self._generation += 1
        if screen_is_active(self):
            self._announce_status()

    def restart(self) -> None:
        """Return to the latest page and follow it."""
        self._cursors = [None]
        self._following = True
        self._invalidate()

    async def refresh_when_due(self) -> None:
        """Fetch the latest page for the 1 Hz tick; only a shown, following section does."""
        if self.shown and self._following:
            await self.refresh_page()

    async def refresh_page(self) -> None:
        """Fetch this section's own page and paint it unless the request changed meanwhile."""
        async with self._host.lock:
            task_id = self._host.task_id()
            if task_id is None or not screen_is_active(self):
                return
            generation = self._generation
            request = self.request
            try:
                page = await self._widget_context.views.task_timeline(
                    task_id,
                    cursor=request.cursor,
                    role=request.role,
                    turn=request.turn,
                    category=request.category,
                )
            except (ShikiruError, OSError, ValueError, TypeError) as error:
                if screen_is_active(self) and generation == self._generation:
                    self._host.report(f"{REFRESH_FAILED}: {error}")
                return
            if (
                screen_is_active(self)
                and task_id == self._host.task_id()
                and generation == self._generation
            ):
                self.show_page(page)

    def on_timeline_filter_bar_changed(self, message: TimelineFilterBar.Changed) -> None:
        message.stop()
        self._role, self._turn = message.role, message.turn
        self.restart()

    def on_timeline_filter_bar_closed(self, message: TimelineFilterBar.Closed) -> None:
        message.stop()
        self.close_filter()

    def on_row_list_moved(self, message: RowList.Moved) -> None:
        message.stop()
        page = self._page
        if page is None:
            return
        self._selected_identity = (
            page.entries[message.index].identity if message.index > 0 else None
        )
        if message.index > 0:
            self.pause_follow()
        self._paint_selection()

    def on_row_list_paged(self, message: RowList.Paged) -> None:
        message.stop()
        if message.older:
            self._older_page()
        else:
            self._newer_page(select_last=message.by_arrow)

    def on_row_list_scrolled(self, message: RowList.Scrolled) -> None:
        message.stop()
        self.pause_follow()

    def on_row_list_latest(self, message: RowList.Latest) -> None:
        message.stop()
        self.restart()

    def _reset_state(self) -> None:
        self._cursors: list[str | None] = [None]
        self._following = True
        self._role: str | None = None
        self._turn: str | None = None
        self._selected_identity: str | None = None
        self._select_last = False
        self._known_turns: set[str] = set()

    def _invalidate(self) -> None:
        self._generation += 1
        self._selected_identity = None
        self.run_worker(self.refresh_page(), exclusive=False)

    def _older_page(self) -> None:
        page = self._page
        if (
            page is None
            or not page.has_older
            or page.older_cursor is None
            or page.older_cursor == self._cursors[-1]
        ):
            return
        self._cursors.append(page.older_cursor)
        self._following = False
        self._invalidate()

    def _newer_page(self, *, select_last: bool) -> None:
        if len(self._cursors) <= 1:
            return
        self._cursors.pop()
        self._following = len(self._cursors) == 1
        self._select_last = select_last
        self._invalidate()

    def _announce_status(self) -> None:
        self.post_message(
            self.StatusChanged(self, FOLLOWING_LABEL if self._following else PAUSED_LABEL)
        )

    def _remember_turns(self, entries: tuple[TaskTimelineEntryView, ...]) -> None:
        if not self._filterable:
            return
        self._known_turns.update(item.turn for item in entries if item.turn)
        self.query_one(f"#{self.ids.filters}", TimelineFilterBar).set_turns(
            sorted(self._known_turns)
        )

    def _paint_selection(self) -> None:
        page = self._page
        entries = page.entries if page is not None else ()
        chosen = self.query_one(f"#{self.ids.list}", RowList).selected
        target = self.query_one(f"#{self.ids.selected}", Static)
        selected = entries[chosen] if chosen < len(entries) else None
        target.display = selected is not None
        if selected is not None:
            target.update(_SelectedEntry(Text(selected.phrase)))
