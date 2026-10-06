from __future__ import annotations

from typing import TYPE_CHECKING

from textual.dom import NoScreen

if TYPE_CHECKING:
    from textual.screen import Screen
    from textual.widget import Widget


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
