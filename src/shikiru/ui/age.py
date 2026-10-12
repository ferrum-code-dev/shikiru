"""Elapsed-time text shared by the CLI and the dashboard."""

from __future__ import annotations

from datetime import timedelta

AGE_NOW = "now"
SECONDS_PER_MINUTE = 60
MINUTES_PER_HOUR = 60
HOURS_PER_DAY = 24
SECONDS_PER_HOUR = SECONDS_PER_MINUTE * MINUTES_PER_HOUR


def format_age(seconds: float) -> str:
    """Return `seconds` as `H:MM:SS` (with a day count past 24 hours)."""
    return str(timedelta(seconds=int(seconds)))


def format_short_age(duration: timedelta) -> str:
    """Return `duration` as `now`, `5m`, `3h` or `2d`, the form of the Usage panel."""
    seconds = max(int(duration.total_seconds()), 0)
    if seconds < SECONDS_PER_MINUTE:
        return AGE_NOW
    minutes = seconds // SECONDS_PER_MINUTE
    if minutes < MINUTES_PER_HOUR:
        return f"{minutes}m"
    hours = minutes // MINUTES_PER_HOUR
    if hours < HOURS_PER_DAY:
        return f"{hours}h"
    return f"{hours // HOURS_PER_DAY}d"


def format_duration(seconds: float) -> str:
    """Return `seconds` as `3h 12m`, `5m` or `12s`."""
    hours, remainder = divmod(int(seconds), SECONDS_PER_HOUR)
    minutes, remainder = divmod(remainder, SECONDS_PER_MINUTE)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m"
    return f"{remainder}s"
