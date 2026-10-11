"""Usage levels: how full a share of a limit is against its warn and alert thresholds (06 §5.3)."""

from __future__ import annotations

from enum import StrEnum, auto


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
