"""Shared task-status visuals for operator surfaces."""

from __future__ import annotations

from dataclasses import dataclass

from shikiru.domain.tasks import Status
from shikiru.ui.glyphs import (
    GLYPH_BLOCKED,
    GLYPH_DONE,
    GLYPH_ENGINE,
    GLYPH_ESCALATED,
    GLYPH_PENDING,
)
from shikiru.ui.tokens import (
    ALERT_COLOR,
    ENGINE_COLOR,
    IDLE_COLOR,
    REWORK_COLOR,
    STYLE_BOLD,
    STYLE_DIM,
    STYLE_REVERSE,
    SUCCESS_COLOR,
)


@dataclass(frozen=True, slots=True)
class StatusVisual:
    """The glyph and Rich style representing one task status."""

    glyph: str
    style: str


_STATUS_VISUALS: dict[Status, StatusVisual] = {
    Status.QUEUED: StatusVisual(GLYPH_PENDING, f"{STYLE_DIM} {IDLE_COLOR}"),
    Status.RUNNING: StatusVisual(GLYPH_ENGINE, f"{STYLE_BOLD} {ENGINE_COLOR}"),
    Status.INTEGRATING: StatusVisual(GLYPH_ENGINE, f"{STYLE_BOLD} {ENGINE_COLOR}"),
    Status.ESCALATED: StatusVisual(GLYPH_ESCALATED, f"{STYLE_REVERSE} {ALERT_COLOR}"),
    Status.DONE: StatusVisual(GLYPH_DONE, f"{STYLE_BOLD} {SUCCESS_COLOR}"),
    Status.CANCELLED: StatusVisual(GLYPH_BLOCKED, f"{STYLE_BOLD} {REWORK_COLOR}"),
}


def status_visual(status: Status) -> StatusVisual:
    """Return the shared glyph and style for `status`."""
    return _STATUS_VISUALS[status]
