"""Colour tokens for the shared visual language (docs/design/06-operator.md §6.1)."""

from __future__ import annotations

from shikiru import palette
from shikiru.domain.tasks import Role

BACKGROUND_COLOR = palette.BACKGROUND_COLOR
SURFACE_COLOR = palette.SURFACE_COLOR
RAISED_SURFACE_COLOR = palette.RAISED_SURFACE_COLOR
SELECTED_SURFACE_COLOR = palette.SELECTED_SURFACE_COLOR
SELECTION_OUTLINE_COLOR = palette.SELECTION_OUTLINE_COLOR
NEUTRAL_BORDER_COLOR = palette.NEUTRAL_BORDER_COLOR
PRIMARY_TEXT_COLOR = palette.PRIMARY_TEXT_COLOR
SECONDARY_TEXT_COLOR = palette.SECONDARY_TEXT_COLOR
STEEL_BLUE = palette.STEEL_BLUE
SAGE = palette.SAGE
AMBER = palette.AMBER
DUSTY_ROSE = palette.DUSTY_ROSE
SCROLLBAR_THUMB_COLOR = palette.SCROLLBAR_THUMB_COLOR
SCROLLBAR_THUMB_HOVER_COLOR = palette.SCROLLBAR_THUMB_HOVER_COLOR
SCROLLBAR_THUMB_ACTIVE_COLOR = palette.SCROLLBAR_THUMB_ACTIVE_COLOR
SCROLLBAR_TRACK_COLOR = palette.SCROLLBAR_TRACK_COLOR

ROLE_COLORS: dict[Role, str] = {
    Role.LEADER: STEEL_BLUE,
    Role.IMPLEMENTER: STEEL_BLUE,
    Role.HELPER: STEEL_BLUE,
}

OPERATOR_COLOR = palette.STEEL_BLUE
ENGINE_COLOR = palette.SECONDARY_TEXT_COLOR
SUCCESS_COLOR = palette.SAGE
REWORK_COLOR = palette.AMBER
HOLD_COLOR = palette.AMBER
ALERT_COLOR = palette.DUSTY_ROSE
IDLE_COLOR = palette.SECONDARY_TEXT_COLOR

STYLE_DIM = "dim"
STYLE_BOLD = "bold"
STYLE_REVERSE = "reverse"
"""Attributes paired with every panel colour (06 §6.1): under `NO_COLOR`, Rich keeps the
attribute and drops the colour, giving the required monochrome bold/dim/reverse."""

GAUGE_WARN_THRESHOLD_PCT = 70
GAUGE_ALERT_THRESHOLD_PCT = 90
GAUGE_OK_COLOR = SUCCESS_COLOR
GAUGE_WARN_COLOR = HOLD_COLOR
GAUGE_ALERT_COLOR = ALERT_COLOR


def gauge_color(used_pct: float) -> str:
    """Return the gauge colour for `used_pct`, green below 70%, yellow below 90%, else red."""
    if used_pct >= GAUGE_ALERT_THRESHOLD_PCT:
        return GAUGE_ALERT_COLOR
    if used_pct >= GAUGE_WARN_THRESHOLD_PCT:
        return GAUGE_WARN_COLOR
    return GAUGE_OK_COLOR
