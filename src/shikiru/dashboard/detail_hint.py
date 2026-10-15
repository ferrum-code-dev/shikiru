"""The one hint line at the bottom of the task details, following the focused part."""

from __future__ import annotations

from enum import StrEnum

from rich.text import Text
from textual.widgets import Static

from shikiru.ui.section import BOLD_STEEL, grey

_ARROWS = "\N{UPWARDS ARROW}\N{DOWNWARDS ARROW}"
_SEPARATOR = " · "
_BACK = ("Esc", "back")
_SELECT = (_ARROWS, "select")
_FOLLOW = ("End", "follow")
_TASK_WINDOW = ("t", "task window")


class HintTarget(StrEnum):
    """What holds focus inside the task details."""

    HEADER = "header"
    EVENTS = "events"
    AGENT = "agent"
    AGENT_FILTER = "agent_filter"
    DEPENDENCIES = "dependencies"


_HINTS: dict[HintTarget, tuple[tuple[str, str], ...]] = {
    HintTarget.HEADER: ((_ARROWS, "move"), ("Enter", "open/close")),
    HintTarget.EVENTS: (_SELECT, _FOLLOW, _BACK),
    HintTarget.AGENT: (_SELECT, _FOLLOW, ("f", "filter"), _BACK),
    HintTarget.AGENT_FILTER: (("r", "role"), ("t", "turn"), ("x", "clear"), _BACK),
    HintTarget.DEPENDENCIES: (_SELECT, ("Enter", "open"), _BACK),
}


def hint_for(target: HintTarget, *, task_window: bool, keys_only: bool) -> Text:
    """Return the hint for `target`: cyan bold keys, grey labels, only keys in tiny panes.

    Esc is named only inside a list, where it returns to the header.
    """
    entries = _HINTS[target] + ((_TASK_WINDOW,) if task_window else ())
    hint = Text()
    for index, (key, label) in enumerate(entries):
        if index:
            hint.append_text(grey(_SEPARATOR))
        hint.append(key, style=BOLD_STEEL)
        if not keys_only:
            hint.append_text(grey(f" {label}"))
    return hint


class DetailHintBar(Static):
    """One-row line below the scrolling details; hidden while nothing in them has focus."""

    def __init__(self, *, widget_id: str) -> None:
        super().__init__("", id=widget_id, markup=False)
        self.display = False
        self._hint: Text | None = None

    def show(self, hint: Text | None) -> None:
        """Show `hint`, or hide the line when there is none."""
        self.display = hint is not None
        if hint is not None and hint != self._hint:
            self.update(hint)
        self._hint = hint
