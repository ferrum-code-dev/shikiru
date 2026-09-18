"""Colour tokens for the shared visual language (docs/design/06-operator.md §6.1)."""

from __future__ import annotations

from shikiru.domain.tasks import Role

ROLE_COLORS: dict[Role, str] = {
    Role.LEADER: "#a78bfa",
    Role.IMPLEMENTER: "#22d3ee",
    Role.HELPER: "#e0b872",
}

OPERATOR_COLOR = "#f472b6"
ENGINE_COLOR = "#cbd5e1"
SUCCESS_COLOR = "#4ade80"
REWORK_COLOR = "#fb923c"
HOLD_COLOR = "#facc15"
ALERT_COLOR = "#f87171"
IDLE_COLOR = "#64748b"

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
