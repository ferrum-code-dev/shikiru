"""The focusable one-line fold heading shared by the popups that expand a section in place."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from textual.binding import Binding
from textual.message import Message
from textual.widgets import Static

from shikiru.ui.section import key_label

if TYPE_CHECKING:
    from rich.text import Text
    from textual import events
    from textual.binding import BindingType

COLLAPSED_ARROW = "\N{BLACK RIGHT-POINTING SMALL TRIANGLE}"
EXPANDED_ARROW = "\N{BLACK DOWN-POINTING SMALL TRIANGLE}"
FOLD_TOGGLE_KEY = "t"
FOLD_TOGGLE_LABEL = "show/hide"


def fold_arrow(*, expanded: bool) -> str:
    """Return the arrow that shows whether a fold is open."""
    return EXPANDED_ARROW if expanded else COLLAPSED_ARROW


def fold_toggle_hint() -> Text:
    """Return the `t show/hide` hint shown at the right of a fold heading."""
    return key_label(FOLD_TOGGLE_KEY, FOLD_TOGGLE_LABEL)


class FoldLine(Static, can_focus=True):
    """A fold heading that asks to toggle on Enter, Space or a click."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("enter,space", "toggle_fold", "Toggle", show=False),
    ]

    class Toggled(Message):
        """The heading asked to expand or collapse its section."""

    def action_toggle_fold(self) -> None:
        """Ask to expand or collapse the section."""
        self.post_message(self.Toggled())

    def on_click(self, event: events.Click) -> None:
        event.stop()
        self.post_message(self.Toggled())
