"""Codex rollout quota parsing."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, cast

from shikiru.agents.contracts import QuotaWindow
from shikiru.plugins.agents.codex.options import USAGE_EVENT_TYPE

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

    from shikiru.agents.sdk.reset_time import ResetTimeParser
    from shikiru.clock import Clock

_EVENT_MESSAGE_TYPE = "event_msg"
_RATE_LIMITS_FIELD = "rate_limits"
_PRIMARY_WINDOW = "primary"
_SECONDARY_WINDOW = "secondary"
_WINDOW_NAMES = (_PRIMARY_WINDOW, _SECONDARY_WINDOW)
_USED_PERCENT_FIELD = "used_percent"
_RESETS_AT_FIELD = "resets_at"
_ROLLOUT_GLOB = "rollout-*.jsonl"


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
        """Parse quota windows from Codex rollout JSONL text."""
        latest: Mapping[str, Any] | None = None
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
            latest = cast("Mapping[str, Any]", limits) if isinstance(limits, Mapping) else None
        if latest is None:
            return []
        windows: list[QuotaWindow] = []
        for name in _WINDOW_NAMES:
            raw_window = latest.get(name)
            if isinstance(raw_window, Mapping):
                windows.append(
                    self._window(name, cast("Mapping[str, Any]", raw_window), observed_at)
                )
        return windows

    def _window(self, name: str, raw: Mapping[str, Any], observed_at: datetime) -> QuotaWindow:
        value = raw.get(_USED_PERCENT_FIELD)
        if not isinstance(value, int | float):
            message = f"Codex quota window {name!r} has no numeric used_percent"
            raise TypeError(message)
        reset_value = raw.get(_RESETS_AT_FIELD)
        resets_at = self._reset_time.parse(str(reset_value)) if reset_value is not None else None
        return QuotaWindow(
            name=name,
            used_pct=float(value),
            resets_at=resets_at,
            observed_at=observed_at,
        )

    def _newest_rollout(self) -> Path | None:
        sessions = self._home / "sessions"
        paths = list(sessions.rglob(_ROLLOUT_GLOB)) if sessions.is_dir() else []
        return max(paths, key=lambda path: path.stat().st_mtime_ns) if paths else None


def _payload(entry: Mapping[str, Any]) -> Mapping[str, Any]:
    if entry.get("type") == _EVENT_MESSAGE_TYPE:
        payload = entry.get("payload")
        if isinstance(payload, Mapping):
            return cast("Mapping[str, Any]", payload)
    return entry
