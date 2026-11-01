"""Codex quota evidence: bucket provenance, freshness and the reset the failure names."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from shikiru.agents.contracts import FailureKind, QuotaReported, TurnFailed
from shikiru.agents.sdk.reset_time import ResetTimeParser
from shikiru.plugins.agents.codex.quota import CodexQuotaProbe
from shikiru.plugins.agents.codex.turn import CodexTurnParser
from tests.support.clock import FakeClock

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

_FAILURE_AT = datetime(2026, 10, 31, 23, 31, 9, tzinfo=UTC)
_WEEKLY_RESET = datetime(2026, 11, 4, 6, 52, 8, tzinfo=UTC)
_FIVE_HOUR_RESET = datetime(2026, 11, 1, 4, 11, 8, tzinfo=UTC)
_FIVE_HOUR_MINUTES = 300
_WEEK_MINUTES = 10080
_WARSAW = ZoneInfo("Europe/Warsaw")
_ACTUAL_ERROR = {
    "type": "turn.failed",
    "error": {
        "message": json.dumps(
            {
                "status": 429,
                "code": "usage_limit_reached",
                "message": (
                    "You've hit your usage limit. Upgrade to Pro, visit settings to purchase "
                    "more credits or try again at Nov 4th, 2026 7:52 AM."
                ),
            }
        )
    },
}
_BARE_ERROR = {
    "type": "turn.failed",
    "error": {
        "message": json.dumps(
            {"status": 429, "code": "usage_limit_reached", "message": "Usage limit reached"}
        )
    },
}


def _event(
    at: datetime,
    *,
    limit_id: str,
    five_hour: float | None,
    weekly: float | None,
    five_hour_reset: datetime = _FIVE_HOUR_RESET,
) -> str:
    def window(used: float | None, minutes: int, reset: datetime) -> dict[str, object] | None:
        if used is None:
            return None
        return {
            "used_percent": used,
            "window_minutes": minutes,
            "resets_at": int(reset.timestamp()),
        }

    return json.dumps(
        {
            "timestamp": at.isoformat(),
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "rate_limits": {
                    "limit_id": limit_id,
                    "primary": window(five_hour, _FIVE_HOUR_MINUTES, five_hour_reset),
                    "secondary": window(weekly, _WEEK_MINUTES, _WEEKLY_RESET),
                },
            },
        }
    )


def _parser(tmp_path: Path, rollout_lines: list[str], *, host_zone: bool = True) -> CodexTurnParser:
    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text("\n".join(rollout_lines) + "\n", encoding="utf-8")
    clock = FakeClock(_FAILURE_AT)
    return CodexTurnParser(
        session_id=None,
        initial_usage=None,
        home=tmp_path / "codex-home",
        rollout_path=rollout,
        clock=clock,
        reset_time=ResetTimeParser(
            clock,
            resolve_local=(lambda naive: naive.replace(tzinfo=_WARSAW)) if host_zone else None,
        ),
    )


def _incident_rollout() -> list[str]:
    return [
        _event(_FAILURE_AT - timedelta(seconds=5), limit_id="codex", five_hour=11, weekly=100),
        _event(_FAILURE_AT - timedelta(seconds=1), limit_id="premium", five_hour=None, weekly=None),
    ]


def _finish(parser: CodexTurnParser, error: Mapping[str, object]) -> list[object]:
    parser.feed(json.dumps(error))
    return list(parser.finish(1, ""))


def test_a_trailing_premium_event_without_windows_keeps_the_codex_reading(tmp_path: Path) -> None:
    signals = _finish(_parser(tmp_path, _incident_rollout()), _BARE_ERROR)

    [reported] = [signal for signal in signals if isinstance(signal, QuotaReported)]
    assert {(window.name, window.used_pct) for window in reported.windows} == {
        ("5h", 11),
        ("7d", 100),
    }


def test_another_bucket_with_windows_never_replaces_the_codex_reading(tmp_path: Path) -> None:
    rollout = [
        _event(_FAILURE_AT - timedelta(seconds=5), limit_id="codex", five_hour=11, weekly=100),
        _event(_FAILURE_AT - timedelta(seconds=1), limit_id="premium", five_hour=3, weekly=0),
    ]

    signals = _finish(_parser(tmp_path, rollout), _BARE_ERROR)

    [reported] = [signal for signal in signals if isinstance(signal, QuotaReported)]
    assert {(window.name, window.used_pct) for window in reported.windows} == {
        ("5h", 11),
        ("7d", 100),
    }


def test_the_failure_resets_when_the_provider_text_says_not_when_a_window_does(
    tmp_path: Path,
) -> None:
    signals = _finish(_parser(tmp_path, _incident_rollout()), _ACTUAL_ERROR)

    [failure] = [signal for signal in signals if isinstance(signal, TurnFailed)]
    assert failure.kind is FailureKind.QUOTA
    assert failure.resets_at == datetime(2026, 11, 4, 6, 52, tzinfo=UTC)


def test_without_a_stated_reset_the_exhausted_weekly_window_decides_not_the_available_one(
    tmp_path: Path,
) -> None:
    signals = _finish(_parser(tmp_path, _incident_rollout()), _BARE_ERROR)

    [failure] = [signal for signal in signals if isinstance(signal, TurnFailed)]
    assert failure.resets_at == _WEEKLY_RESET


def test_a_provider_text_without_a_known_zone_is_not_given_an_invented_one(tmp_path: Path) -> None:
    rollout = [
        _event(_FAILURE_AT - timedelta(seconds=1), limit_id="premium", five_hour=None, weekly=None)
    ]

    signals = _finish(_parser(tmp_path, rollout, host_zone=False), _ACTUAL_ERROR)

    [failure] = [signal for signal in signals if isinstance(signal, TurnFailed)]
    assert failure.resets_at is None


def _probe(home: Path) -> CodexQuotaProbe:
    clock = FakeClock(_FAILURE_AT)
    return CodexQuotaProbe(home, clock, ResetTimeParser(clock))


def _rollout(home: Path, name: str, lines: list[str], *, written_at: datetime) -> None:
    path = home / "sessions" / "2026" / "11" / "01" / f"rollout-{name}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.utime(path, (written_at.timestamp(), written_at.timestamp()))


def test_the_latest_reading_is_the_newest_codex_sample_after_the_block(tmp_path: Path) -> None:
    after = _FAILURE_AT + timedelta(minutes=10)
    _rollout(
        tmp_path,
        "old",
        [_event(_FAILURE_AT - timedelta(hours=1), limit_id="codex", five_hour=12, weekly=100)],
        written_at=_FAILURE_AT - timedelta(hours=1),
    )
    _rollout(
        tmp_path,
        "new",
        [
            _event(after, limit_id="codex", five_hour=2, weekly=0),
            _event(after + timedelta(seconds=1), limit_id="premium", five_hour=None, weekly=None),
        ],
        written_at=after,
    )

    reading = _probe(tmp_path).latest_reading(_FAILURE_AT)

    assert reading is not None
    assert reading.observed_at == after
    assert {(window.name, window.used_pct) for window in reading.windows} == {("5h", 2), ("7d", 0)}


def test_samples_that_predate_the_block_are_not_evidence(tmp_path: Path) -> None:
    _rollout(
        tmp_path,
        "stale",
        [_event(_FAILURE_AT - timedelta(minutes=1), limit_id="codex", five_hour=1, weekly=0)],
        written_at=_FAILURE_AT + timedelta(minutes=5),
    )

    assert _probe(tmp_path).latest_reading(_FAILURE_AT) is None


def test_a_newer_unrelated_bucket_is_not_evidence_for_the_codex_block(tmp_path: Path) -> None:
    later = _FAILURE_AT + timedelta(minutes=10)
    _rollout(
        tmp_path,
        "other-bucket",
        [
            _event(later, limit_id="premium", five_hour=1, weekly=0),
            _event(later, limit_id="premium", five_hour=None, weekly=None),
        ],
        written_at=later,
    )

    assert _probe(tmp_path).latest_reading(_FAILURE_AT) is None


def test_no_rollouts_is_no_reading(tmp_path: Path) -> None:
    assert _probe(tmp_path).latest_reading(_FAILURE_AT) is None
