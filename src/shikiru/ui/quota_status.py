"""The status line of an agent pane whose turn or dispatch waits on a quota block."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.domain.quota_waits import QUOTA_BANNER_SEPARATOR, quota_reset_clock
from shikiru.ui.glyphs import GLYPH_QUOTA

if TYPE_CHECKING:
    from datetime import datetime

    from shikiru.domain.quota_waits import QuotaAction

QUOTA_BLOCKED_STATUS = "Quota blocked"


def quota_status_text(action: QuotaAction | None, at: datetime | None, now: datetime) -> str:
    """Return the pane status line; the engine's next `action` and its time show only when known.

    `at` and `now` are rendered in the zone they carry, so callers pass the viewer's local time.
    """
    text = f"{GLYPH_QUOTA} {QUOTA_BLOCKED_STATUS}"
    if action is None or at is None:
        return text
    return f"{text}{QUOTA_BANNER_SEPARATOR}{action.value} {quota_reset_clock(at, now)}"
