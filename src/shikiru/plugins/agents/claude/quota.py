"""Claude Code subscription quota probe and cache."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from shikiru.agents.contracts import QuotaWindow
from shikiru.plugins.agents.claude.options import CLAUDE_COMMAND

if TYPE_CHECKING:
    from shikiru.agents.sdk.reset_time import ResetTimeParser
    from shikiru.clock import Clock
    from shikiru.process import SubprocessRunner

PROBE_ARGV = (
    CLAUDE_COMMAND,
    "-p",
    ".",
    "--output-format",
    "stream-json",
    "--verbose",
    "--model",
    "haiku",
    "--safe-mode",
    "--tools",
    "",
    "--no-session-persistence",
)
PROBE_TIMEOUT_SECONDS = 60.0
QUOTA_CACHE_SECONDS = 900
RATE_LIMIT_EVENT = "rate_limit_event"
RATE_LIMIT_INFO_FIELD = "rate_limit_info"
UNIFIED_WINDOWS_FIELD = "unifiedWindows"
UTILIZATION_FIELD = "utilization"
RESETS_AT_FIELD = "resetsAt"
WINDOW_NAMES = ("five_hour", "seven_day")


class ClaudeQuotaProbe:
    """Runs and caches Claude's subscription quota probe."""

    def __init__(
        self,
        runner: SubprocessRunner,
        clock: Clock,
        reset_time: ResetTimeParser,
        environment: Callable[[], Mapping[str, str]],
    ) -> None:
        """Use `runner`, `clock`, `reset_time` and auth `environment` for probes."""
        self._runner = runner
        self._clock = clock
        self._reset_time = reset_time
        self._environment = environment
        self._expires_at = None
        self._cached: list[QuotaWindow] | None = None

    async def get(self) -> list[QuotaWindow]:
        """Return a cached quota snapshot or run the subscription probe."""
        now = self._clock.now()
        if self._expires_at is not None and self._cached is not None and now < self._expires_at:
            return list(self._cached)
        result = await self._runner.run(
            PROBE_ARGV,
            timeout=PROBE_TIMEOUT_SECONDS,
            env=self._environment(),
            check=False,
        )
        windows = self._parse(result.stdout, now)
        self._cached = windows
        self._expires_at = now + timedelta(seconds=QUOTA_CACHE_SECONDS)
        return list(windows)

    def invalidate(self) -> None:
        """Expire the current snapshot after a quota failure."""
        self._expires_at = None

    def _parse(self, stdout: str, observed_at: datetime) -> list[QuotaWindow]:
        for raw_line in stdout.splitlines():
            payload = json.loads(raw_line)
            if not isinstance(payload, Mapping):
                continue
            typed_payload = cast("Mapping[str, Any]", payload)
            if typed_payload.get("type") != RATE_LIMIT_EVENT:
                continue
            info = typed_payload.get(RATE_LIMIT_INFO_FIELD)
            if not isinstance(info, Mapping):
                continue
            typed_info = cast("Mapping[str, Any]", info)
            windows = typed_info.get(UNIFIED_WINDOWS_FIELD)
            if not isinstance(windows, Mapping):
                continue
            typed_windows = cast("Mapping[str, Any]", windows)
            return [
                self._window(name, cast("Mapping[str, Any]", typed_windows[name]), observed_at)
                for name in WINDOW_NAMES
                if isinstance(typed_windows.get(name), Mapping)
            ]
        return []

    def _window(self, name: str, raw: Mapping[str, Any], observed_at: datetime) -> QuotaWindow:
        utilization = raw.get(UTILIZATION_FIELD)
        if not isinstance(utilization, int | float):
            message = f"Claude quota window {name!r} has no numeric utilization"
            raise TypeError(message)
        reset_value = raw.get(RESETS_AT_FIELD)
        resets_at = self._reset_time.parse(str(reset_value)) if reset_value is not None else None
        return QuotaWindow(
            name=name,
            used_pct=float(utilization),
            resets_at=resets_at,
            observed_at=observed_at,
        )
