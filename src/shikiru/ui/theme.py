"""Shared semantic colors for Textual screens and evidence views."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.theme import Theme

from shikiru.ui.tokens import (
    ALERT_COLOR,
    AMBER,
    BACKGROUND_COLOR,
    HOLD_COLOR,
    NEUTRAL_BORDER_COLOR,
    PRIMARY_TEXT_COLOR,
    RAISED_SURFACE_COLOR,
    SCROLLBAR_THUMB_ACTIVE_COLOR,
    SCROLLBAR_THUMB_COLOR,
    SCROLLBAR_THUMB_HOVER_COLOR,
    SCROLLBAR_TRACK_COLOR,
    SECONDARY_TEXT_COLOR,
    SELECTED_SURFACE_COLOR,
    SELECTION_OUTLINE_COLOR,
    STEEL_BLUE,
    SUCCESS_COLOR,
    SURFACE_COLOR,
)

if TYPE_CHECKING:
    from textual.app import App

BACKGROUND = BACKGROUND_COLOR
SURFACE = SURFACE_COLOR
RAISED_SURFACE = RAISED_SURFACE_COLOR
NEUTRAL_BORDER = NEUTRAL_BORDER_COLOR
PRIMARY_TEXT = PRIMARY_TEXT_COLOR
SECONDARY_TEXT = SECONDARY_TEXT_COLOR
FOCUS = STEEL_BLUE
THEME_NAME = "shikiru"

SHIKIRU_THEME = Theme(
    name=THEME_NAME,
    primary=FOCUS,
    secondary=SECONDARY_TEXT,
    warning=HOLD_COLOR,
    error=ALERT_COLOR,
    success=SUCCESS_COLOR,
    accent=FOCUS,
    foreground=PRIMARY_TEXT,
    background=BACKGROUND,
    surface=SURFACE,
    panel=RAISED_SURFACE,
    variables={
        "border": NEUTRAL_BORDER,
        "accent": FOCUS,
        "amber": AMBER,
        "dusty-rose": ALERT_COLOR,
        "neutral-border": NEUTRAL_BORDER,
        "focus": FOCUS,
        "muted-text": SECONDARY_TEXT,
        "primary-text": PRIMARY_TEXT,
        "sage": SUCCESS_COLOR,
        "scrollbar": SCROLLBAR_THUMB_COLOR,
        "scrollbar-active": SCROLLBAR_THUMB_ACTIVE_COLOR,
        "scrollbar-background": SCROLLBAR_TRACK_COLOR,
        "scrollbar-background-active": SCROLLBAR_TRACK_COLOR,
        "scrollbar-background-hover": SCROLLBAR_TRACK_COLOR,
        "scrollbar-corner-color": SCROLLBAR_TRACK_COLOR,
        "scrollbar-hover": SCROLLBAR_THUMB_HOVER_COLOR,
        "secondary-text": SECONDARY_TEXT,
        "selected-surface": SELECTED_SURFACE_COLOR,
        "selection-outline": SELECTION_OUTLINE_COLOR,
        "steel-blue": STEEL_BLUE,
    },
)


def install_theme(app: App[None]) -> None:
    """Register the shared Shikiru palette on a Textual application."""
    app.register_theme(SHIKIRU_THEME)
    app.theme = THEME_NAME
