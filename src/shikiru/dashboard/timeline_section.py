"""One task timeline: a category's rows, cursor stack, filters and follow state."""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import TYPE_CHECKING, cast

from rich.text import Text
from textual.containers import Horizontal, Vertical
from textual.widgets import Input, Select, Static

from shikiru.dashboard.contract import TaskTimelineCategory
from shikiru.dashboard.details_list_view import EVENTS_HINT, DetailsSection
from shikiru.dashboard.row_list import RowList
from shikiru.dashboard.section_rows import timeline_cells
from shikiru.domain.tasks import Role
from shikiru.errors import ShikiruError
from shikiru.ui.screen import widget_is_current
from shikiru.ui.section import BOLD_PRIMARY, grey

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Callable

    from textual.app import App, ComposeResult
    from textual.widget import Widget

    from shikiru.dashboard.contract import TaskTimelinePageView, WidgetContext

NO_VALUE = "\N{EN DASH}"
HEADING_SEPARATOR = " · "
NO_TIMELINE_ROWS = "No recorded entries match."
FOLLOWING_LABEL = "following"
PAUSED_LABEL = "paused · End to follow"
REFRESH_FAILED = "Evidence timeline refresh failed"
TIMELINE_FILTER_OPTIONS = (
    ("Decisions and transitions", TaskTimelineCategory.ACTIVITY),
    ("All engine events", TaskTimelineCategory.ENGINE),
    ("Decisions", TaskTimelineCategory.DECISION),
    ("Transitions", TaskTimelineCategory.TRANSITION),
    ("Agent signals", TaskTimelineCategory.SIGNAL),
    ("Tools", TaskTimelineCategory.TOOL),
    ("Messages", TaskTimelineCategory.MESSAGE),
    ("Usage", TaskTimelineCategory.USAGE),
    ("All evidence", TaskTimelineCategory.ALL),
)


@dataclass(frozen=True)
class TimelineIds:
    """The widget ids of one timeline section, derived from a shared prefix."""

    root: str
    title: str
    status: str
    filters: str
    category: str
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
    queue_refresh: Callable[[], None]
    report: Callable[[str], None]


def screen_is_active(widget: Widget) -> bool:
    """Whether the widget's screen is the current one in a running app."""
    return widget_is_current(widget) and cast("App[None]", widget.app).is_running


class TimelineSection(Vertical):
    """Own one category's timeline rows, paging, filters and follow state."""

    DEFAULT_CSS = """
    TimelineSection { height: auto; }
    TimelineSection .timeline-heading { height: auto; padding: 0; }
    TimelineSection .timeline-filters { width: 100%; height: auto; }
    TimelineSection .timeline-filters Select, TimelineSection .timeline-filters Input {
        height: 3;
    }
    TimelineSection .timeline-category { width: 40%; min-width: 10; }
    TimelineSection .timeline-role { width: 30%; min-width: 10; }
    TimelineSection .timeline-turn { width: 30%; min-width: 8; }
    """

    def __init__(
        self,
        context: WidgetContext,
        host: TimelineHost,
        *,
        ids: TimelineIds,
        section: DetailsSection,
        category: TaskTimelineCategory,
    ) -> None:
        super().__init__(id=ids.root)
        self.ids = ids
        self._widget_context = context
        self._host = host
        self._section = section
        self._default_category = category.value
        self._page: TaskTimelinePageView | None = None
        self._generation = 0
        self._reset_state()

    def compose(self) -> ComposeResult:
        yield Static("", id=self.ids.title, classes="timeline-heading", markup=False)
        yield Static("", id=self.ids.status, classes="timeline-heading", markup=False)
        with Horizontal(id=self.ids.filters, classes="timeline-filters"):
            yield Select(
                TIMELINE_FILTER_OPTIONS,
                value=self._category,
                id=self.ids.category,
                classes="timeline-category",
            )
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

    @property
    def shown(self) -> bool:
        """Whether this section is on screen; only a shown section refreshes."""
        return self.display

    @shown.setter
    def shown(self, shown: bool) -> None:
        self.display = shown

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
        for static_id in (self.ids.title, self.ids.status, self.ids.selected):
            self.query_one(f"#{static_id}", Static).update("")
        self.query_one(f"#{self.ids.role}", Select).value = Select.BLANK
        self.query_one(f"#{self.ids.turn}", Input).value = ""
        self.query_one(f"#{self.ids.category}", Select).value = self._category
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
        if event.select.id in {self.ids.category, self.ids.role}:
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

    def on_row_list_latest(self, message: RowList.Latest) -> None:
        message.stop()
        self.restart()

    def _reset_state(self) -> None:
        self._cursors: list[str | None] = [None]
        self._following = True
        self._role: str | None = None
        self._turn: str | None = None
        self._category = self._default_category
        self._selected_identity: str | None = None

    def _invalidate(self) -> None:
        self._generation += 1
        self._selected_identity = None
        self._host.queue_refresh()

    def _apply_filters(self) -> None:
        role_value = cast("Select[str]", self.query_one(f"#{self.ids.role}", Select)).value
        category_value = cast("Select[str]", self.query_one(f"#{self.ids.category}", Select)).value
        turn_value = self.query_one(f"#{self.ids.turn}", Input).value.strip()
        role = role_value or None if isinstance(role_value, str) else None
        category = category_value if isinstance(category_value, str) else self._default_category
        turn = turn_value or None
        if (role, category, turn) == (self._role, self._category, self._turn):
            return
        self._role, self._category, self._turn = role, category, turn
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
        heading = Text(self._section.value, style=BOLD_PRIMARY)
        heading.append_text(
            grey(HEADING_SEPARATOR + (FOLLOWING_LABEL if self._following else PAUSED_LABEL))
        )
        self.query_one(f"#{self.ids.title}", Static).update(heading)
        self.query_one(f"#{self.ids.status}", Static).update(grey(EVENTS_HINT))

    def _paint_selection(self) -> None:
        page = self._page
        entries = page.entries if page is not None else ()
        chosen = self.query_one(f"#{self.ids.list}", RowList).selected
        target = self.query_one(f"#{self.ids.selected}", Static)
        selected = entries[chosen] if chosen < len(entries) else None
        target.display = selected is not None
        if selected is None:
            return
        lines = [
            f"{selected.source} · {selected.category}",
            f"Timestamp · {selected.timestamp or NO_VALUE}",
        ]
        if selected.role is not None:
            lines.append(f"Role · {selected.role}")
        if selected.turn is not None:
            lines.append(f"Turn · {selected.turn}")
        lines.extend((selected.summary, selected.detail))
        target.update("\n".join(lines))
