from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from textual.widget import Widget

POPUP_FRAME_CLASS = "popup-frame"
POPUP_HEADER_CLASS = "popup-header"
POPUP_TITLE_CLASS = "popup-title"
POPUP_CLOSE_CLASS = "popup-close-control"
POPUP_RULE_CLASS = "popup-rule"
POPUP_BODY_CLASS = "popup-body"
POPUP_SECONDARY_CLASS = "popup-secondary"

POPUP_BUTTON_CSS = """
.popup-frame Button.-style-default {
    color: $primary-text; background: $surface; border: solid $neutral-border;
    border-top: solid $neutral-border; border-bottom: solid $neutral-border;
    text-style: none;
}
.popup-frame Button.-style-default.-primary {
    border: solid $neutral-border;
    border-top: solid $neutral-border; border-bottom: solid $neutral-border;
}
.popup-frame Button.-style-default:focus,
.popup-frame Button.-style-default.-primary:focus {
    color: $primary-text; background: $selected-surface;
    border: solid $focus; border-top: solid $focus; border-bottom: solid $focus;
    text-style: reverse;
}
.popup-frame Button.-style-default:disabled,
.popup-frame Button.-style-default.-primary:disabled {
    color: $secondary-text; background: $surface;
    border: solid $neutral-border;
    border-top: solid $neutral-border; border-bottom: solid $neutral-border;
    text-opacity: 76%;
}
"""

POPUP_CONTROL_CSS = """
.popup-frame Button:focus {
    color: $primary-text; background: $selected-surface; text-style: reverse;
}
.popup-frame Button:disabled { color: $secondary-text; }
.popup-frame SelectCurrent { border: solid $neutral-border; padding: 0 1; }
.popup-frame Select:focus > SelectCurrent,
.popup-frame Select.-expanded > SelectCurrent {
    border: solid $focus; background: $selected-surface;
}
.popup-frame SelectOverlay { border: solid $neutral-border; }
.popup-frame SelectOverlay:focus { border: solid $focus; }
.popup-frame Select:disabled > SelectCurrent { color: $secondary-text; }
.popup-frame Button.popup-close-control {
    width: auto; min-width: 5; height: 1; margin: 0; padding: 0 1;
    border: none; border-top: none; border-bottom: none;
    background: $surface; color: $primary-text; text-style: none;
}
.popup-frame Button.popup-close-control.-style-default:focus {
    border: none; border-top: none; border-right: none; border-bottom: none; border-left: none;
    background: $selected-surface; color: $primary-text; text-style: reverse;
}
"""

POPUP_CSS = (
    """
.popup-frame {
    width: 100%; max-width: 64; height: auto; max-height: 100%; margin: 1 2;
    padding: 1 2; border: solid $steel-blue; background: $surface; color: $primary-text;
}
.popup-header { width: 100%; height: 1; }
.popup-title { width: 1fr; height: 1; color: $primary-text; text-style: bold; }
.popup-frame Rule.popup-rule { width: 100%; height: 1; margin: 0; color: $neutral-border; }
.popup-body {
    height: auto; max-height: 1fr; min-height: 0; padding: 0;
}
.popup-body, .popup-frame #task-detail-body {
    overflow-x: hidden; overflow-y: auto;
    scrollbar-size-horizontal: 0; scrollbar-size-vertical: 1;
    scrollbar-color: $secondary-text; scrollbar-color-active: $focus;
    scrollbar-color-hover: $primary-text; scrollbar-background: $surface;
    scrollbar-background-active: $surface; scrollbar-background-hover: $surface;
    scrollbar-corner-color: $surface;
}
.popup-secondary { color: $secondary-text; }
"""
    + POPUP_BUTTON_CSS
    + POPUP_CONTROL_CSS
)


def prepare_popup(screen: Widget) -> None:
    screen.styles.align_horizontal = "center"
    screen.styles.align_vertical = "middle"
    screen.styles.background = "transparent"
    screen.styles.padding = 0
