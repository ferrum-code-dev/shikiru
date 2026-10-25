"""Stall detection."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from shikiru.config.settings import TimeoutsConfig


def seconds_until_stall(
    last_activity: datetime, tool_running: bool, now: datetime, timeouts: TimeoutsConfig
) -> float:
    """Return how long past `now` the activity last seen may stay silent.

    A running tool gets the longer `timeouts.tool` budget; anything else uses `timeouts.stall`.
    """
    threshold = timeouts.tool if tool_running else timeouts.stall
    return threshold - (now - last_activity).total_seconds()


def is_stalled(
    last_activity: datetime, tool_running: bool, now: datetime, timeouts: TimeoutsConfig
) -> bool:
    """Decide whether `now` is past the timeout for the kind of activity last seen."""
    return seconds_until_stall(last_activity, tool_running, now, timeouts) <= 0
