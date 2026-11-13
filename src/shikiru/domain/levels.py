"""Usage levels: how full a share of a limit is against its warn and alert thresholds."""

from __future__ import annotations

from enum import StrEnum, auto
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime

    from shikiru.agents.contracts import QuotaWindow

WHOLE_PERCENT = 100


class UsageLevel(StrEnum):
    """Whether a used share is below the warn threshold, from it, or from the alert threshold."""

    OK = auto()
    WARN = auto()
    ALERT = auto()


def displayed_pct(used_pct: float) -> int:
    """Return the whole percentage a surface shows for a used share (`41.6` shows `42`)."""
    return round(used_pct)


def usage_level(used_pct: float, *, warn_pct: int, alert_pct: int) -> UsageLevel:
    """Classify `used_pct` by the whole percentage shown: `alert_pct` and above is ALERT."""
    shown = displayed_pct(used_pct)
    if shown >= alert_pct:
        return UsageLevel.ALERT
    if shown >= warn_pct:
        return UsageLevel.WARN
    return UsageLevel.OK


def free_space_level(
    free_bytes: int, total_bytes: int, *, warn_pct: int, alert_pct: int
) -> UsageLevel:
    """Classify free space below `alert_pct` percent as ALERT and below `warn_pct` as WARN.

    The share is compared as exact byte counts, so it is never rounded into a level.
    """
    if free_bytes * WHOLE_PERCENT < alert_pct * total_bytes:
        return UsageLevel.ALERT
    if free_bytes * WHOLE_PERCENT < warn_pct * total_bytes:
        return UsageLevel.WARN
    return UsageLevel.OK


def effective_used_pct(window: QuotaWindow, now: datetime) -> float | None:
    """Return the used percentage a human sees for `window` at `now`, or None when it is unknown.

    A window whose known reset has passed is unknown: its stored reading is only a lower bound,
    since the account may have been used elsewhere. The stored reading itself is unchanged.
    """
    if window.resets_at is not None and window.resets_at <= now:
        return None
    return window.used_pct


def reset_is_known(window: QuotaWindow, now: datetime) -> bool:
    """Return whether `window` shows a countdown to a reset still ahead of `now`.

    Derived from `effective_used_pct`, so a window never shows a countdown while its percentage
    is unknown, and never shows an unknown percentage beside a countdown.
    """
    return window.resets_at is not None and effective_used_pct(window, now) is not None


def next_quota_reset(windows: Iterable[QuotaWindow], now: datetime) -> datetime | None:
    """Return the earliest reset still ahead of `now` among `windows`, or None when none is.

    Until that instant a displayed window keeps its stored reading; from it the window shows as
    unused, so a surface that shows quotas must be redrawn then.
    """
    upcoming = [
        window.resets_at
        for window in windows
        if window.resets_at is not None and window.resets_at > now
    ]
    return min(upcoming, default=None)
