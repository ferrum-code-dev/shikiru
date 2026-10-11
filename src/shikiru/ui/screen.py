from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from textual.dom import NoScreen
from textual.screen import Screen

if TYPE_CHECKING:
    from textual.widget import Widget


@runtime_checkable
class ConditionalTabStop(Protocol):
    """A widget that Tab and Shift+Tab visit only while `is_tab_stop()` holds."""

    def is_tab_stop(self) -> bool: ...


class TabStopScreen(Screen[object]):
    """Keep widgets that are not stops now out of the Tab order, except the focused widget."""

    @property
    def focus_chain(self) -> list[Widget]:
        focused = self.focused
        return [
            widget
            for widget in super().focus_chain
            if widget is focused
            or not isinstance(widget, ConditionalTabStop)
            or widget.is_tab_stop()
        ]


def widget_is_attached(widget: Widget) -> bool:
    return _screen_for(widget) is not None


def widget_is_on_screen(widget: Widget, screen: object) -> bool:
    return _screen_for(widget) is screen


def widget_is_current(widget: Widget) -> bool:
    screen = _screen_for(widget)
    return screen is not None and screen.is_current


def widget_is_active(widget: Widget) -> bool:
    screen = _screen_for(widget)
    return screen is not None and screen.is_active


def _screen_for(widget: Widget) -> Screen[object] | None:
    if not widget.is_attached:
        return None
    try:
        return widget.screen
    except NoScreen:
        return None
