"""Folds of the task details: a header with a live headline above a body that shows when open."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Static

from shikiru.dashboard.details_list_view import DetailsEntry, entry_renderable
from shikiru.dashboard.row_list import RowList

if TYPE_CHECKING:
    from collections.abc import Sequence

    from textual import events
    from textual.app import ComposeResult
    from textual.binding import BindingType
    from textual.widget import Widget

OPEN_MARK = "\N{BLACK DOWN-POINTING SMALL TRIANGLE}"
CLOSED_MARK = "\N{BLACK RIGHT-POINTING SMALL TRIANGLE}"
_STEP_UP = -1
_STEP_DOWN = 1


class FoldHeader(Static):
    """One focusable header row; ↑↓ move between headers, Enter/Space/→ open, ← close."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("up", f"move({_STEP_UP})", "Previous section", show=False),
        Binding("down", f"move({_STEP_DOWN})", "Next section", show=False),
        Binding("enter", "open", "Open", show=False),
        Binding("space", "open", "Open", show=False),
        Binding("right", "open", "Open", show=False),
        Binding("left", "close", "Close", show=False),
    ]
    DEFAULT_CSS = """
    FoldHeader { height: 1; width: 100%; }
    FoldHeader:focus { background: $selected-surface; background-tint: transparent; }
    """

    def __init__(self, fold: DetailFold) -> None:
        super().__init__("", markup=False)
        self._fold = fold

    def allow_focus(self) -> bool:
        """Only the current header is a Tab stop, so the whole list is one stop."""
        return self._fold.is_current

    def on_focus(self) -> None:
        self._fold.set_focused(focused=True)

    def on_blur(self) -> None:
        self._fold.set_focused(focused=False)

    def on_click(self, event: events.Click) -> None:
        event.stop()
        self._fold.folds.focus_header(self._fold)
        self._fold.activate()

    def action_move(self, step: int) -> None:
        """Focus the neighbouring header."""
        self._fold.folds.move(self._fold, step)

    def action_open(self) -> None:
        """Open the fold, or enter its list when it is already open."""
        self._fold.activate()

    def action_close(self) -> None:
        """Fold the body away."""
        self._fold.set_open(False)


class DetailFold(Vertical):
    """A section header with a live headline and a body whose display follows the fold state."""

    DEFAULT_CSS = """
    DetailFold { height: auto; }
    DetailFold .fold-body { height: auto; padding: 0 0 0 2; }
    """

    class Toggled(Message):
        """The fold opened or closed."""

        def __init__(self, fold: DetailFold) -> None:
            super().__init__()
            self.fold = fold

    class Chosen(Message):
        """A header without a body was activated."""

        def __init__(self, fold: DetailFold) -> None:
            super().__init__()
            self.fold = fold

    def __init__(
        self,
        view_id: str,
        title: str,
        body: Sequence[Widget] | None = None,
        *,
        is_open: bool = False,
    ) -> None:
        """Hold the section's view id and name; a fold without a body is a plain choice."""
        super().__init__()
        self.view_id = view_id
        self.title = title
        self.headline: str | None = None
        self.header = FoldHeader(self)
        self._body = Vertical(*body, classes="fold-body") if body is not None else None
        self._open = is_open and body is not None
        self._focused = False
        if self._body is not None:
            self._body.display = self._open

    def compose(self) -> ComposeResult:
        """Yield the header, then the body when the fold has one."""
        yield self.header
        if self._body is not None:
            yield self._body

    def on_mount(self) -> None:
        """Paint the header once it exists."""
        self.paint()

    @property
    def folds(self) -> DetailFolds:
        """The list this fold belongs to."""
        return cast("DetailFolds", self.parent)

    @property
    def is_open(self) -> bool:
        """Whether the body is shown."""
        return self._open

    @property
    def is_current(self) -> bool:
        """Whether the header is the list's Tab stop."""
        return self.folds.is_current(self)

    @property
    def focus_target(self) -> Widget:
        """The row list inside an open body, otherwise the header."""
        lists = self._body.query(RowList) if self._body is not None and self._open else None
        return lists.first(RowList) if lists else self.header

    def set_open(self, is_open: bool) -> None:
        """Show or hide the body and tell the owner when the state changed."""
        if self._body is None or is_open == self._open:
            return
        self._open = is_open
        self._body.display = is_open
        self.paint()
        self.post_message(self.Toggled(self))

    def set_focused(self, *, focused: bool) -> None:
        """Highlight the header while it has focus."""
        self._focused = focused
        self.paint()

    def set_headline(self, headline: str | None) -> None:
        """Replace the grey right-hand headline."""
        if headline != self.headline:
            self.headline = headline
            self.paint()

    def activate(self) -> None:
        """Open a closed fold, or move into the list of an open one; a choice posts `Chosen`."""
        if self._body is None:
            self.post_message(self.Chosen(self))
            return
        self.set_open(True)
        self.call_after_refresh(self.folds.focus_fold, self)

    def paint(self) -> None:
        """Draw the header: mark, name and headline, highlighted while it has focus."""
        if not self.header.is_mounted:
            return
        mark = OPEN_MARK if self._open else CLOSED_MARK
        entry = DetailsEntry(self.view_id, f"{mark} {self.title}", self.headline)
        self.header.update(entry_renderable(entry, selected=self._focused))


class DetailFolds(Vertical):
    """The list of folds: one Tab stop, ↑↓ between headers."""

    DEFAULT_CSS = "DetailFolds { height: auto; border-top: solid $neutral-border; }"

    def __init__(self, *, widget_id: str) -> None:
        super().__init__(id=widget_id)
        self._current: DetailFold | None = None

    @property
    def all_folds(self) -> list[DetailFold]:
        """Every fold in display order."""
        return list(self.query(DetailFold))

    @property
    def current_fold(self) -> DetailFold:
        """The fold holding the roving Tab stop: the last one focused, the first until then."""
        return self._current or self.all_folds[0]

    def is_current(self, fold: DetailFold) -> bool:
        """Whether `fold` holds the roving Tab stop."""
        return self.current_fold is fold

    def make_current(self, fold: DetailFold) -> None:
        """Move the Tab stop to `fold`."""
        self._current = fold

    def move(self, fold: DetailFold, step: int) -> None:
        """Focus the header `step` places from `fold`, stopping at both ends."""
        folds = self.all_folds
        target = folds[min(max(folds.index(fold) + step, 0), len(folds) - 1)]
        self.focus_header(target)

    def focus_header(self, fold: DetailFold) -> None:
        """Make `fold` the Tab stop and focus its header."""
        self.make_current(fold)
        self.screen.set_focus(fold.header)

    def focus_fold(self, fold: DetailFold) -> None:
        """Focus the row list of an open fold, or its header."""
        self.make_current(fold)
        self.screen.set_focus(fold.focus_target)
