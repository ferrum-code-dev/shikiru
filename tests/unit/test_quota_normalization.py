"""Provider quota windows reach the contract with canonical names and percent units."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from shikiru.agents.contracts import QuotaReported
from shikiru.agents.sdk.reset_time import ResetTimeParser
from shikiru.plugins.agents.claude.turn import ClaudeTurnParser
from shikiru.plugins.agents.codex.quota import CodexQuotaProbe
from tests.support.clock import FakeClock

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _claude_windows(five_hour: float, seven_day: float) -> list[tuple[str, float]]:
    clock = FakeClock(NOW)
    parser = ClaudeTurnParser(
        session_id="s",
        model="haiku",
        initial_usage=None,
        clock=clock,
        reset_time=ResetTimeParser(clock),
    )
    line = json.dumps(
        {
            "type": "rate_limit_event",
            "rate_limit_info": {
                "unifiedWindows": {
                    "five_hour": {"utilization": five_hour},
                    "seven_day": {"utilization": seven_day},
                }
            },
        }
    )
    [signal] = parser.feed(line)
    assert isinstance(signal, QuotaReported)
    return [(window.name, window.used_pct) for window in signal.windows]


@pytest.mark.parametrize(
    ("utilization", "percent"),
    [(0, 0.0), (0.0036, 0.36), (0.36, 36.0), (1, 100.0)],
)
def test_claude_fractional_utilization_becomes_percent_exactly_once(
    utilization: float, percent: float
) -> None:
    windows = _claude_windows(utilization, utilization)

    assert windows == [("5h", pytest.approx(percent)), ("7d", pytest.approx(percent))]


def _codex_windows(
    primary: dict[str, float | int], secondary: dict[str, float | int]
) -> list[tuple[str, float]]:
    rollout = json.dumps(
        {
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "rate_limits": {"primary": primary, "secondary": secondary},
            },
        }
    )
    clock = FakeClock(NOW)
    probe = CodexQuotaProbe(Path(), clock, ResetTimeParser(clock))
    return [(w.name, w.used_pct) for w in probe.parse(rollout, NOW)]


@pytest.mark.parametrize(
    ("primary_minutes", "secondary_minutes", "names"),
    [
        (300, 10080, ["5h", "7d"]),
        (None, None, ["5h", "7d"]),
        (120, 1440, ["2h", "1d"]),
        (90, 20160, ["90m", "14d"]),
    ],
)
def test_codex_window_names_follow_the_reported_duration(
    primary_minutes: int | None, secondary_minutes: int | None, names: list[str]
) -> None:
    def window(minutes: int | None) -> dict[str, float | int]:
        return {"used_percent": 0.5} | ({"window_minutes": minutes} if minutes else {})

    windows = _codex_windows(window(primary_minutes), window(secondary_minutes))

    assert windows == [(names[0], 0.5), (names[1], 0.5)]
