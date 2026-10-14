"""One task timeline: a category's rows, cursor stack, filters and follow state."""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import TYPE_CHECKING, ClassVar, cast

from rich.text import Text
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Input, Select, Static

from shikiru.dashboard.details_list_view import EVENTS_HINT
from shikiru.dashboard.row_list import RowList
from shikiru.dashboard.section_rows import timeline_cells, timeline_meta
from shikiru.domain.tasks import Role
from shikiru.errors import ShikiruError
from shikiru.ui.keys import key_hint
from shikiru.ui.screen import widget_is_current
from shikiru.ui.section import BOLD_STEEL, grey
from shikiru.ui.tokens import SECONDARY_TEXT_COLOR

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Callable

    from rich.console import Console, ConsoleOptions, RenderResult
    from textual.app import App, ComposeResult
    from textual.binding import BindingType
    from textual.widget import Widget

    from shikiru.dashboard.contract import TaskTimelineCategory, TaskTimelinePageView, WidgetContext

NO_VALUE = "\N{EN DASH}"
HEADING_SEPARATOR = " · "
NO_TIMELINE_ROWS = "No recorded entries match."
FOLLOWING_LABEL = "following"
PAUSED_LABEL = "paused · End to follow"
REFRESH_FAILED = "Evidence timeline refresh failed"
FILTER_KEY = "f"
TOGGLE_FILTERS_ACTION = "toggle_filters"
FILTER_LABEL = " filter"
SELECTED_MAX_LINES = 4
ELLIPSIS = "\N{HORIZONTAL ELLIPSIS}"
FILTER_NOTE_SEPARATOR = ", "
ROLE_NOTE = "role {}"
TURN_NOTE = "turn {}"


@dataclass(frozen=True)
class TimelineIds:
    """The widget ids of one timeline section, derived from a shared prefix."""

    root: str
    title: str
    hint: str
    filters: str
    role: str
    turn: str
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
    meta: Text

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        lines = self.text.wrap(console, options.max_width)
        shown = lines[:SELECTED_MAX_LINES]
        if len(lines) > SELECTED_MAX_LINES:
            last = shown[-1]
            last.rstrip()
            last.truncate(max(options.max_width - 1, 1))
            last.append(ELLIPSIS)
        yield from shown
        yield self.meta


class TimelineSection(Vertical):
    """Own one timeline's rows, paging, filters and follow state; `shown` while open."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding(FILTER_KEY, TOGGLE_FILTERS_ACTION, "Filter", show=False)
    ]
    DEFAULT_CSS = """
    TimelineSection { height: auto; }
    TimelineSection .timeline-heading { height: auto; padding: 0; }
    TimelineSection .timeline-filters { width: 100%; height: auto; }
    TimelineSection .timeline-filters Select, TimelineSection .timeline-filters Input {
        height: 3;
    }
    TimelineSection .timeline-role { width: 50%; min-width: 10; }
    TimelineSection .timeline-turn { width: 50%; min-width: 8; }
    """

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
        yield Static("", id=self.ids.title, classes="timeline-heading", markup=False)
        yield Static("", id=self.ids.hint, classes="timeline-heading", markup=False)
        if self._filterable:
            with Horizontal(id=self.ids.filters, classes="timeline-filters") as filters:
                filters.display = False
                yield Select(
                    (
                        ("Any role", ""),
                        ("Leader", Role.LEADER.value),
                        ("Implementer", Role.IMPLEMENTER.value),
                        ("Helper", Role.HELPER.value),
                    ),
                    prompt="Any role",
                    allow_blank=True,
                    value=Select.BLANK,
                    id=self.ids.role,
                    classes="timeline-role",
                )
                yield Input(placeholder="Any turn", id=self.ids.turn, classes="timeline-turn")
        yield RowList(widget_id=self.ids.list)
        yield Static("", id=self.ids.selected, classes="task-detail-section", markup=False)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        del parameters
        return self._filterable if action == TOGGLE_FILTERS_ACTION else True

    def action_toggle_filters(self) -> None:
        filters = self.query_one(f"#{self.ids.filters}", Horizontal)
        filters.display = not filters.display
        if filters.display:
            self.query_one(f"#{self.ids.role}", Select).focus()
        elif filters.has_focus_within:
            self.query_one(f"#{self.ids.list}", RowList).focus()

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
        for static_id in (self.ids.title, self.ids.hint, self.ids.selected):
            self.query_one(f"#{static_id}", Static).update("")
        if self._filterable:
            self.query_one(f"#{self.ids.filters}", Horizontal).display = False
            self.query_one(f"#{self.ids.role}", Select).value = Select.BLANK
            self.query_one(f"#{self.ids.turn}", Input).value = ""
        self.query_one(f"#{self.ids.list}", RowList).set_rows(
            (), selected=0, empty=NO_TIMELINE_ROWS
        )

    def show_page(self, page: TaskTimelinePageView | None) -> None:
        """Paint a page of rows, keeping the selected entry when it is still listed."""
        self._page = page
        entries = page.entries if page is not None else ()
        selected = next(
            (
                index
                for index, item in enumerate(entries)
                if item.identity == self._selected_identity
            ),
            0,
        )
        self.query_one(f"#{self.ids.list}", RowList).set_rows(
            [timeline_cells(item) for item in entries], selected=selected, empty=NO_TIMELINE_ROWS
        )
        self._paint_heading()
        self._paint_selection()

    def pause_follow(self) -> None:
        """Stop following the latest entries until the operator asks for them again."""
        if not self._following:
            return
        self._following = False
        self._generation += 1
        if screen_is_active(self):
            self._paint_heading()

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

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == self.ids.role:
            self._apply_filters()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == self.ids.turn:
            self._apply_filters()

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
            self._newer_page()

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

    def _invalidate(self) -> None:
        self._generation += 1
        self._selected_identity = None
        self.run_worker(self.refresh_page(), exclusive=False)

    def _apply_filters(self) -> None:
        role_value = cast("Select[str]", self.query_one(f"#{self.ids.role}", Select)).value
        turn_value = self.query_one(f"#{self.ids.turn}", Input).value.strip()
        role = role_value or None if isinstance(role_value, str) else None
        turn = turn_value or None
        if (role, turn) == (self._role, self._turn):
            return
        self._role, self._turn = role, turn
        self.restart()

    def _older_page(self) -> None:
        page = self._page
        if page is None or not page.has_older or page.older_cursor is None:
            return
        self._cursors.append(page.older_cursor)
        self._following = False
        self._invalidate()

    def _newer_page(self) -> None:
        if len(self._cursors) <= 1:
            return
        self._cursors.pop()
        self._following = len(self._cursors) == 1
        self._invalidate()

    def _paint_heading(self) -> None:
        heading = key_hint(
            FOLLOWING_LABEL if self._following else PAUSED_LABEL, SECONDARY_TEXT_COLOR
        )
        notes = [
            note.format(value)
            for note, value in ((ROLE_NOTE, self._role), (TURN_NOTE, self._turn))
            if value
        ]
        if notes:
            heading.append_text(grey(HEADING_SEPARATOR + FILTER_NOTE_SEPARATOR.join(notes)))
        self.query_one(f"#{self.ids.title}", Static).update(heading)
        hint = key_hint(EVENTS_HINT, SECONDARY_TEXT_COLOR)
        if self._filterable:
            hint.append(HEADING_SEPARATOR, style=SECONDARY_TEXT_COLOR)
            hint.append(FILTER_KEY, style=BOLD_STEEL)
            hint.append(FILTER_LABEL, style=SECONDARY_TEXT_COLOR)
        self.query_one(f"#{self.ids.hint}", Static).update(hint)

    def _paint_selection(self) -> None:
        page = self._page
        entries = page.entries if page is not None else ()
        chosen = self.query_one(f"#{self.ids.list}", RowList).selected
        target = self.query_one(f"#{self.ids.selected}", Static)
        selected = entries[chosen] if chosen < len(entries) else None
        target.display = selected is not None
        if selected is not None:
            target.update(_SelectedEntry(Text(selected.phrase), grey(timeline_meta(selected))))
