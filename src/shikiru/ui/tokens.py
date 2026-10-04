"""Colour tokens for the shared visual language (docs/design/06-operator.md §6.1)."""

from __future__ import annotations

from shikiru.domain.tasks import Role

BACKGROUND_COLOR = "#11151b"
SURFACE_COLOR = "#171d25"
RAISED_SURFACE_COLOR = "#171d25"
SELECTED_SURFACE_COLOR = "#233340"
NEUTRAL_BORDER_COLOR = "#3d4654"
PRIMARY_TEXT_COLOR = "#dce2e9"
SECONDARY_TEXT_COLOR = "#9aa5b5"
STEEL_BLUE = "#83bddd"
SAGE = "#90cba6"
AMBER = "#dfb16c"
DUSTY_ROSE = "#e3a0a0"

ROLE_COLORS: dict[Role, str] = {
    Role.LEADER: STEEL_BLUE,
    Role.IMPLEMENTER: STEEL_BLUE,
    Role.HELPER: STEEL_BLUE,
}

OPERATOR_COLOR = STEEL_BLUE
ENGINE_COLOR = SECONDARY_TEXT_COLOR
SUCCESS_COLOR = SAGE
REWORK_COLOR = AMBER
HOLD_COLOR = AMBER
ALERT_COLOR = DUSTY_ROSE
IDLE_COLOR = SECONDARY_TEXT_COLOR

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
