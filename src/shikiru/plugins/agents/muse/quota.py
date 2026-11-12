"""Muse account quota read from `muse usage --json`."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from math import isfinite
from typing import Any, cast

from shikiru.agents.contracts import (
    PERCENT,
    QUOTA_WINDOW_FIVE_HOUR,
    QUOTA_WINDOW_SEVEN_DAY,
    QuotaReading,
    QuotaWindow,
)
from shikiru.errors import ShikiruError
from shikiru.plugins.agents.muse.options import (
    USAGE_ALLOWANCE_KEY,
    USAGE_AS_OF_KEY,
    USAGE_RESETS_AT_KEY,
    USAGE_USED_PERCENT_KEY,
    USAGE_WEEKLY_KEY,
    USAGE_WINDOW_KEY,
)

_MALFORMED_USAGE_MESSAGE = "muse usage --json printed an unreadable allowance"
_MILLISECONDS_PER_SECOND = 1000
_WINDOW_NAMES = (
    (USAGE_WINDOW_KEY, QUOTA_WINDOW_FIVE_HOUR),
    (USAGE_WEEKLY_KEY, QUOTA_WINDOW_SEVEN_DAY),
)


def parse_usage(text: str, since: datetime) -> QuotaReading | None:
    """Return the allowance in `muse usage --json` output if it was observed after `since`.

    A null allowance, or one that reports no window, has no reading. A field that is missing
    rather than null makes the output unreadable.
    """
    allowance = _allowance(text)
    if allowance is None:
        return None
    observed_at = _instant(allowance, USAGE_AS_OF_KEY)
    if observed_at <= since:
        return None
    windows: list[QuotaWindow] = []
    for key, name in _WINDOW_NAMES:
        window = _field(allowance, key)
        if window is not None:
            windows.append(_window(_mapping(window), name, observed_at))
    return QuotaReading(tuple(windows), observed_at) if windows else None


def _allowance(text: str) -> Mapping[str, Any] | None:
    try:
        document = json.loads(text)
    except json.JSONDecodeError as error:
        raise ShikiruError(_MALFORMED_USAGE_MESSAGE) from error
    allowance = _field(_mapping(document), USAGE_ALLOWANCE_KEY)
    return None if allowance is None else _mapping(allowance)


def _window(window: Mapping[str, Any], name: str, observed_at: datetime) -> QuotaWindow:
    resets_at = _field(window, USAGE_RESETS_AT_KEY)
    return QuotaWindow(
        name=name,
        used_pct=_used_percent(window),
        resets_at=None if resets_at is None else _instant(window, USAGE_RESETS_AT_KEY),
        observed_at=observed_at,
    )


def _used_percent(window: Mapping[str, Any]) -> float:
    value = _field(window, USAGE_USED_PERCENT_KEY)
    if isinstance(value, bool) or not isinstance(value, int | float) or not isfinite(value):
        raise ShikiruError(_MALFORMED_USAGE_MESSAGE)
    return min(float(value), float(PERCENT))


def _instant(record: Mapping[str, Any], key: str) -> datetime:
    value = _field(record, key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ShikiruError(_MALFORMED_USAGE_MESSAGE)
    try:
        return datetime.fromtimestamp(value / _MILLISECONDS_PER_SECOND, tz=UTC)
    except (OverflowError, OSError, ValueError) as error:
        raise ShikiruError(_MALFORMED_USAGE_MESSAGE) from error


def _field(record: Mapping[str, Any], key: str) -> object:
    if key not in record:
        raise ShikiruError(_MALFORMED_USAGE_MESSAGE)
    return record[key]


def _mapping(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ShikiruError(_MALFORMED_USAGE_MESSAGE)
    return cast("Mapping[str, Any]", value)
