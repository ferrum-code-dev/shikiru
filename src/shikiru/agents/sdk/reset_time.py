"""`ResetTimeParser`: turns an agent's reset-time strings into aware `datetime`s (04 §2)."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from shikiru.clock import Clock

_EPOCH_SECONDS_PATTERN = re.compile(r"^\d+(\.\d+)?$")
_CLOCK_TIME_PATTERN = re.compile(
    r"resets\s+(?P<hour>\d{1,2}):(?P<minute>\d{2})\s*(?P<meridiem>am|pm)\s*\((?P<zone>[^)]+)\)",
    re.IGNORECASE,
)
_RELATIVE_DURATION_PATTERN = re.compile(
    r"in\s+(?P<amount>\d+)\s*(?P<unit>second|minute|hour|day)s?", re.IGNORECASE
)
_SECONDS_PER_UNIT = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}
_HOURS_PER_MERIDIEM = 12


class ResetTimeParser:
    """Parses a `resets ...`, ISO, epoch-seconds or relative-duration string into a `datetime`."""

    def __init__(self, clock: Clock) -> None:
        """Anchor relative and clock-time parsing on `clock.now()`, never the wall clock."""
        self._clock = clock

    def parse(self, text: str) -> datetime | None:
        """Return the aware `datetime` `text` names, or `None` if no known format matches."""
        stripped = text.strip()
        return (
            self._parse_epoch(stripped)
            or self._parse_iso(stripped)
            or self._parse_relative(stripped)
            or self._parse_clock_time(stripped)
        )

    def _parse_epoch(self, text: str) -> datetime | None:
        if not _EPOCH_SECONDS_PATTERN.match(text):
            return None
        return datetime.fromtimestamp(float(text), tz=UTC)

    def _parse_iso(self, text: str) -> datetime | None:
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)

    def _parse_relative(self, text: str) -> datetime | None:
        match = _RELATIVE_DURATION_PATTERN.search(text)
        if match is None:
            return None
        amount = int(match.group("amount"))
        seconds = amount * _SECONDS_PER_UNIT[match.group("unit").lower()]
        return self._clock.now() + timedelta(seconds=seconds)

    def _parse_clock_time(self, text: str) -> datetime | None:
        match = _CLOCK_TIME_PATTERN.search(text)
        if match is None:
            return None
        zone = ZoneInfo(match.group("zone"))
        hour = int(match.group("hour")) % _HOURS_PER_MERIDIEM
        if match.group("meridiem").lower() == "pm":
            hour += _HOURS_PER_MERIDIEM
        minute = int(match.group("minute"))
        now_in_zone = self._clock.now().astimezone(zone)
        candidate = now_in_zone.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now_in_zone:
            candidate += timedelta(days=1)
        return candidate
