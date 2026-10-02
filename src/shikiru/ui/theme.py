"""Shared semantic colors for Textual screens and evidence views."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.theme import Theme

from shikiru.ui.tokens import ALERT_COLOR, HOLD_COLOR, SUCCESS_COLOR

if TYPE_CHECKING:
    from textual.app import App

BACKGROUND = "#0b111b"
SURFACE = "#121c2b"
RAISED_SURFACE = "#1b293a"
NEUTRAL_BORDER = "#39495d"
PRIMARY_TEXT = "#e5ebf2"
SECONDARY_TEXT = "#a2afbf"
FOCUS = "#f8fafc"
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
        "neutral-border": NEUTRAL_BORDER,
        "focus": FOCUS,
        "muted-text": SECONDARY_TEXT,
        "primary-text": PRIMARY_TEXT,
        "secondary-text": SECONDARY_TEXT,
    },
)


def install_theme(app: App[None]) -> None:
    """Register the shared Shikiru palette on a Textual application."""
    app.register_theme(SHIKIRU_THEME)
    app.theme = THEME_NAME
