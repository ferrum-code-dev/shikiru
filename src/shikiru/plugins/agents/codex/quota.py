"""Codex rollout quota parsing."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from shikiru.agents.contracts import (
    QUOTA_WINDOW_FIVE_HOUR,
    QUOTA_WINDOW_SEVEN_DAY,
    QuotaReading,
    QuotaWindow,
)
from shikiru.plugins.agents.codex.options import USAGE_EVENT_TYPE

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.agents.sdk.reset_time import ResetTimeParser
    from shikiru.clock import Clock

_EVENT_MESSAGE_TYPE = "event_msg"
_RATE_LIMITS_FIELD = "rate_limits"
_PRIMARY_WINDOW = "primary"
_SECONDARY_WINDOW = "secondary"
_DEFAULT_WINDOW_NAMES = {
    _PRIMARY_WINDOW: QUOTA_WINDOW_FIVE_HOUR,
    _SECONDARY_WINDOW: QUOTA_WINDOW_SEVEN_DAY,
}
_WINDOW_MINUTES_FIELD = "window_minutes"
_TIMESTAMP_FIELD = "timestamp"
_MINUTES_PER_HOUR = 60
_MINUTES_PER_DAY = 24 * _MINUTES_PER_HOUR
_USED_PERCENT_FIELD = "used_percent"
_RESETS_AT_FIELD = "resets_at"
_ROLLOUT_GLOB = "rollout-*.jsonl"
_LIMIT_ID_FIELD = "limit_id"
CODEX_LIMIT_ID = "codex"
_ROLLOUT_TAIL_BYTES = 1 << 20
_NEWLINE = b"\n"
_FILE_START = 0
_AFTER_SEPARATOR = -1


@dataclass(frozen=True)
class QuotaObservation:
    """A Codex quota sample and its provider timestamp, when present."""

    windows: tuple[QuotaWindow, ...]
    timestamp: datetime | None


class CodexQuotaProbe:
    """Reads the Codex-bucket quota windows from Codex rollouts."""

    def __init__(self, home: Path, clock: Clock, reset_time: ResetTimeParser) -> None:
        """Use `home`, `clock` and `reset_time` to inspect Codex session files."""
        self._home = home
        self._clock = clock
        self._reset_time = reset_time

    def get(self, rollout_path: Path | None = None) -> list[QuotaWindow]:
        """Return the latest primary and secondary quota windows, or no windows."""
        path = rollout_path or self._newest_rollout()
        if path is None or not path.is_file():
            return []
        return self.parse(path.read_text(encoding="utf-8"), self._clock.now())

    def parse(self, text: str, observed_at: datetime) -> list[QuotaWindow]:
        """Parse quota windows from Codex rollout JSONL text."""
        observation = self.parse_observation(text, observed_at)
        return list(observation.windows) if observation is not None else []

    def parse_observation(self, text: str, observed_at: datetime) -> QuotaObservation | None:
        """Parse the latest quota sample and preserve its provider timestamp."""
        latest: tuple[Mapping[str, Any], datetime | None] | None = None
        for line in text.splitlines():
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(raw, Mapping):
                continue
            payload = _payload(cast("Mapping[str, Any]", raw))
            if payload.get("type") != USAGE_EVENT_TYPE:
                continue
            limits = payload.get(_RATE_LIMITS_FIELD)
            if _is_codex_bucket(limits) and _has_windows(cast("Mapping[str, Any]", limits)):
                latest = (
                    cast("Mapping[str, Any]", limits),
                    _event_time(cast("Mapping[str, Any]", raw)),
                )
        if latest is None:
            return None
        rate_limits, event_time = latest
        window_time = event_time or observed_at
        windows = tuple(
            self._window(key, cast("Mapping[str, Any]", rate_limits[key]), window_time)
            for key in _DEFAULT_WINDOW_NAMES
            if isinstance(rate_limits.get(key), Mapping)
        )
        return QuotaObservation(windows, event_time)

    def latest_reading(self, since: datetime) -> QuotaReading | None:
        """Return the newest Codex-bucket sample stamped after `since` in any recent rollout.

        Only rollouts written after `since` are read, from their tail; a sample without a
        provider timestamp cannot show it is fresh and is ignored.
        """
        sessions = self._home / "sessions"
        if not sessions.is_dir():
            return None
        readings = [
            QuotaReading(observation.windows, observation.timestamp)
            for path in sessions.rglob(_ROLLOUT_GLOB)
            if datetime.fromtimestamp(path.stat().st_mtime, tz=UTC) > since
            for observation in [self.parse_observation(_rollout_tail(path), self._clock.now())]
            if observation is not None
            and observation.timestamp is not None
            and observation.timestamp > since
        ]
        return max(readings, key=lambda reading: reading.observed_at, default=None)

    def _window(self, key: str, raw: Mapping[str, Any], observed_at: datetime) -> QuotaWindow:
        value = raw.get(_USED_PERCENT_FIELD)
        if not isinstance(value, int | float):
            message = f"Codex quota window {key!r} has no numeric used_percent"
            raise TypeError(message)
        reset_value = raw.get(_RESETS_AT_FIELD)
        resets_at = self._reset_time.parse(str(reset_value)) if reset_value is not None else None
        return QuotaWindow(
            name=_window_name(key, raw.get(_WINDOW_MINUTES_FIELD)),
            used_pct=float(value),
            resets_at=resets_at,
            observed_at=observed_at,
        )

    def _newest_rollout(self) -> Path | None:
        sessions = self._home / "sessions"
        paths = list(sessions.rglob(_ROLLOUT_GLOB)) if sessions.is_dir() else []
        return max(paths, key=lambda path: path.stat().st_mtime_ns) if paths else None


def _is_codex_bucket(limits: object) -> bool:
    if not isinstance(limits, Mapping):
        return False
    limit_id = cast("Mapping[str, Any]", limits).get(_LIMIT_ID_FIELD)
    return limit_id is None or limit_id == CODEX_LIMIT_ID


def _has_windows(limits: Mapping[str, Any]) -> bool:
    return any(isinstance(limits.get(key), Mapping) for key in _DEFAULT_WINDOW_NAMES)


def _rollout_tail(path: Path) -> str:
    with path.open("rb") as handle:
        size = handle.seek(_FILE_START, os.SEEK_END)
        handle.seek(max(size - _ROLLOUT_TAIL_BYTES, _FILE_START))
        data = handle.read()
    if size > _ROLLOUT_TAIL_BYTES:
        data = data.partition(_NEWLINE)[_AFTER_SEPARATOR]
    return data.decode("utf-8", errors="ignore")


def _payload(entry: Mapping[str, Any]) -> Mapping[str, Any]:
    if entry.get("type") == _EVENT_MESSAGE_TYPE:
        payload = entry.get("payload")
        if isinstance(payload, Mapping):
            return cast("Mapping[str, Any]", payload)
    return entry


def _event_time(entry: Mapping[str, Any]) -> datetime | None:
    value = entry.get(_TIMESTAMP_FIELD)
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _window_name(key: str, minutes: object) -> str:
    if not isinstance(minutes, int) or isinstance(minutes, bool) or minutes <= 0:
        return _DEFAULT_WINDOW_NAMES[key]
    if minutes % _MINUTES_PER_DAY == 0:
        return f"{minutes // _MINUTES_PER_DAY}d"
    if minutes % _MINUTES_PER_HOUR == 0:
        return f"{minutes // _MINUTES_PER_HOUR}h"
    return f"{minutes}m"
