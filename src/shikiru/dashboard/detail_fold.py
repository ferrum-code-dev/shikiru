"""Folds of the task details: a header with a live headline above a body that shows when open."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Static

from shikiru.dashboard.details_list_view import DetailsEntry, entry_renderable
from shikiru.dashboard.row_list import RowList
from shikiru.ui.tokens import STEEL_BLUE

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
_DISPLAY_NONE = "none"


class FoldHeader(Static):
    """One focusable header row; ↑↓ move, Enter opens and enters or folds, Space toggles."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("up", f"move({_STEP_UP})", "Previous section", show=False),
        Binding("down", f"move({_STEP_DOWN})", "Next section", show=False),
        Binding("enter", "enter", "Open or close", show=False),
        Binding("space", "toggle_open", "Open or close", show=False),
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
        self._fold.toggle()

    def action_move(self, step: int) -> None:
        """Focus the neighbouring header."""
        self._fold.folds.move(self._fold, step)

    def action_enter(self) -> None:
        """Fold an open fold; open a closed one and enter its list."""
        self._fold.toggle()

    def action_toggle_open(self) -> None:
        """Open or fold the body without leaving the header."""
        self._fold.set_open(not self._fold.is_open)


class DetailFold(Vertical):
    """A section header with a live headline and a body whose display follows the fold state."""

    DEFAULT_CSS = """
    DetailFold { height: auto; }
    DetailFold .fold-body { height: auto; padding: 0 0 1 2; }
    """

    class Toggled(Message):
        """The fold opened or closed."""

        def __init__(self, fold: DetailFold) -> None:
            super().__init__()
            self.fold = fold

    def __init__(
        self,
        view_id: str,
        title: str,
        body: Sequence[Widget],
        *,
        is_open: bool = False,
        name_color: str = STEEL_BLUE,
    ) -> None:
        """Hold the section's view id, name in its colour, and body."""
        super().__init__()
        self.view_id = view_id
        self.title = title
        self.headline: str | None = None
        self.name_color = name_color
        self.header = FoldHeader(self)
        self._body = Vertical(*body, classes="fold-body")
        self._open = is_open
        self._focused = False
        self._body.display = self._open

    def compose(self) -> ComposeResult:
        """Yield the header, then the body."""
        yield self.header
        yield self._body

    def on_mount(self) -> None:
        """Paint the header once it exists."""
        self.paint()

    @property
    def folds(self) -> DetailFolds:
        """The list this fold belongs to: the nearest one above it, so a nested fold has its own."""
        return next(node for node in self.ancestors if isinstance(node, DetailFolds))

    @property
    def is_shown(self) -> bool:
        """Whether the header is on screen: shown itself, inside no hidden or folded fold."""
        if self.styles.display == _DISPLAY_NONE:
            return False
        enclosing = next((node for node in self.ancestors if isinstance(node, DetailFold)), None)
        return enclosing is None or (enclosing.is_open and enclosing.is_shown)

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
        lists = self._body.query(RowList) if self._open else None
        return lists.first(RowList) if lists else self.header

    def set_open(self, is_open: bool) -> None:
        """Show or hide the body and tell the owner when the state changed."""
        if is_open == self._open:
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
        """Open the fold and move into its list when it has one."""
        self.set_open(True)
        self.call_after_refresh(self.folds.focus_fold, self)

    def toggle(self) -> None:
        """Fold an open fold back to its header; open a closed one and enter its list."""
        if self._open:
            self.set_open(False)
            self.folds.focus_header(self)
        else:
            self.activate()

    def paint(self) -> None:
        """Draw the header: mark, name and headline, highlighted while it has focus."""
        if not self.header.is_mounted:
            return
        mark = OPEN_MARK if self._open else CLOSED_MARK
        entry = DetailsEntry(
            self.view_id, f"{mark} {self.title}", self.headline, name_color=self.name_color
        )
        self.header.update(entry_renderable(entry, selected=self._focused))


class DetailFolds(Vertical):
    """The list of folds: one Tab stop, ↑↓ between headers."""

    DEFAULT_CSS = "DetailFolds { height: auto; border-top: solid $neutral-border; }"

    def __init__(self, *, widget_id: str) -> None:
        super().__init__(id=widget_id)
        self._current: DetailFold | None = None

    @property
    def all_folds(self) -> list[DetailFold]:
        """Every fold in display order, shown or not."""
        return list(self.query(DetailFold))

    @property
    def visible_folds(self) -> list[DetailFold]:
        """The folds shown, in display order; a fold inside a closed or hidden one has no header."""
        return [fold for fold in self.all_folds if fold.is_shown]

    @property
    def current_fold(self) -> DetailFold:
        """The fold holding the roving Tab stop: the last one focused, else the first shown."""
        visible = self.visible_folds
        if self._current is not None and self._current in visible:
            return self._current
        return visible[0]

    def is_current(self, fold: DetailFold) -> bool:
        """Whether `fold` holds the roving Tab stop; a hidden fold never does."""
        return fold.is_shown and self.current_fold is fold

    def make_current(self, fold: DetailFold) -> None:
        """Move the Tab stop to `fold`."""
        self._current = fold

    def move(self, fold: DetailFold, step: int) -> None:
        """Focus the header `step` places from `fold`, stopping at both ends."""
        folds = self.visible_folds
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
