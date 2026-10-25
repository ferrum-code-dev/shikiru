"""Colour tokens for the shared visual language."""

from __future__ import annotations

from shikiru import palette
from shikiru.domain.levels import UsageLevel
from shikiru.domain.tasks import Role

BACKGROUND_COLOR = palette.BACKGROUND_COLOR
SURFACE_COLOR = palette.SURFACE_COLOR
RAISED_SURFACE_COLOR = palette.RAISED_SURFACE_COLOR
SELECTED_SURFACE_COLOR = palette.SELECTED_SURFACE_COLOR
DETAIL_SURFACE_COLOR = palette.DETAIL_SURFACE_COLOR
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
STOPPED_DURING = "stopped during"
IDLE_COLOR = palette.SECONDARY_TEXT_COLOR

STYLE_DIM = "dim"
STYLE_BOLD = "bold"
STYLE_REVERSE = "reverse"
"""Attributes paired with every panel colour: under `NO_COLOR`, Rich keeps the
attribute and drops the colour, giving the required monochrome bold/dim/reverse."""

USAGE_LEVEL_COLORS: dict[UsageLevel, str] = {
    UsageLevel.OK: SAGE,
    UsageLevel.WARN: AMBER,
    UsageLevel.ALERT: DUSTY_ROSE,
}
