"""Stall and operator-prompt detection (docs/design/03-workflow.md §4.2, §9)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from shikiru.config.settings import TimeoutsConfig


def is_stalled(
    last_activity: datetime, tool_running: bool, now: datetime, timeouts: TimeoutsConfig
) -> bool:
    """Decide whether `now` is past the timeout for the kind of activity last seen (03 §4.2).

    A running tool gets the longer `timeouts.tool` budget; anything else uses `timeouts.stall`.
    """
    threshold = timeouts.tool if tool_running else timeouts.stall
    return (now - last_activity).total_seconds() >= threshold
