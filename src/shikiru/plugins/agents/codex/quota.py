"""Codex rollout quota parsing."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Any, cast

from shikiru.agents.contracts import (
    QUOTA_WINDOW_FIVE_HOUR,
    QUOTA_WINDOW_SEVEN_DAY,
    QuotaObservationTimeSource,
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
_MINUTES_PER_HOUR = 60
_MINUTES_PER_DAY = 24 * _MINUTES_PER_HOUR
_USED_PERCENT_FIELD = "used_percent"
_RESETS_AT_FIELD = "resets_at"
_ROLLOUT_GLOB = "rollout-*.jsonl"
_TIMESTAMP_FIELD = "timestamp"


class CodexQuotaProbe:
    """Reads quota windows from the newest Codex rollout."""

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
        """Parse the latest quota windows from Codex rollout JSONL text."""
        samples = _rate_limit_samples(text)
        if not samples:
            return []
        source_time, limits = samples[-1]
        if limits is None or not limits:
            return []
        time_source = (
            QuotaObservationTimeSource.PROVIDER
            if source_time is not None
            else QuotaObservationTimeSource.UNKNOWN
        )
        return self._windows(limits, source_time or observed_at, time_source)

    def live_sample(self, text: str, observed_at: datetime) -> list[QuotaWindow]:
        """Parse one quota event arriving through this turn's stdout stream."""
        samples = _rate_limit_samples(text)
        if not samples:
            return []
        source_time, limits = samples[-1]
        if limits is None or not limits:
            return []
        time_source = (
            QuotaObservationTimeSource.PROVIDER
            if source_time is not None
            else QuotaObservationTimeSource.COLLECTED
        )
        return self._windows(limits, source_time or observed_at, time_source)

    def samples(self, text: str, since: datetime) -> list[list[QuotaWindow]]:
        """Return each rollout sample dated at or after `since`, oldest first.

        Timestamped samples keep their rollout time. Older rollouts without timestamps retain
        their latest snapshot with unknown provenance; it cannot establish a task interval.
        """
        samples = _rate_limit_samples(text)
        if any(observed_at is not None for observed_at, _ in samples):
            windows = [
                self._windows(limits, observed_at, QuotaObservationTimeSource.PROVIDER)
                for observed_at, limits in samples
                if observed_at is not None and observed_at >= since and limits
            ]
            return [sample for sample in windows if sample]
        limits = [limits for _, limits in samples if limits]
        if not limits:
            return []
        latest = self._windows(limits[-1], self._clock.now(), QuotaObservationTimeSource.UNKNOWN)
        return [latest] if latest else []

    def _windows(
        self,
        limits: Mapping[str, Any],
        observed_at: datetime,
        time_source: QuotaObservationTimeSource,
    ) -> list[QuotaWindow]:
        windows: list[QuotaWindow] = []
        for key in _DEFAULT_WINDOW_NAMES:
            raw_window = limits.get(key)
            if isinstance(raw_window, Mapping):
                windows.append(
                    self._window(
                        key,
                        cast("Mapping[str, Any]", raw_window),
                        observed_at,
                        time_source,
                    )
                )
        return windows

    def _window(
        self,
        key: str,
        raw: Mapping[str, Any],
        observed_at: datetime,
        time_source: QuotaObservationTimeSource,
    ) -> QuotaWindow:
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
            time_source=time_source,
        )

    def _newest_rollout(self) -> Path | None:
        sessions = self._home / "sessions"
        paths = list(sessions.rglob(_ROLLOUT_GLOB)) if sessions.is_dir() else []
        return max(paths, key=lambda path: path.stat().st_mtime_ns) if paths else None


def _rate_limit_samples(text: str) -> list[tuple[datetime | None, Mapping[str, Any] | None]]:
    samples: list[tuple[datetime | None, Mapping[str, Any] | None]] = []
    for line in text.splitlines():
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, Mapping):
            continue
        entry = cast("Mapping[str, Any]", raw)
        payload = _payload(entry)
        if payload.get("type") != USAGE_EVENT_TYPE:
            continue
        limits = payload.get(_RATE_LIMITS_FIELD)
        samples.append(
            (
                _timestamp(entry.get(_TIMESTAMP_FIELD)),
                cast("Mapping[str, Any]", limits) if isinstance(limits, Mapping) else None,
            )
        )
    return samples


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _payload(entry: Mapping[str, Any]) -> Mapping[str, Any]:
    if entry.get("type") == _EVENT_MESSAGE_TYPE:
        payload = entry.get("payload")
        if isinstance(payload, Mapping):
            return cast("Mapping[str, Any]", payload)
    return entry


def _window_name(key: str, minutes: object) -> str:
    if not isinstance(minutes, int) or isinstance(minutes, bool) or minutes <= 0:
        return _DEFAULT_WINDOW_NAMES[key]
    if minutes % _MINUTES_PER_DAY == 0:
        return f"{minutes // _MINUTES_PER_DAY}d"
    if minutes % _MINUTES_PER_HOUR == 0:
        return f"{minutes // _MINUTES_PER_HOUR}h"
    return f"{minutes}m"
