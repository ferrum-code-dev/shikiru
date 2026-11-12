"""Muse failure classification and quota reset selection."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from shikiru.agents.contracts import (
    QUOTA_WINDOW_FIVE_HOUR,
    FailureKind,
    QuotaWindow,
    TurnFailed,
)
from shikiru.agents.sdk.reset_time import ResetTimeParser
from shikiru.plugins.agents.muse.options import (
    RECORD_PAYLOAD_KEY,
    RECORD_PAYLOAD_TYPE_KEY,
    RECORD_STREAM_KEY,
    RUN_TERMINAL_FAILED_TYPE,
    RUN_USAGE_TYPE,
    STREAM_ID_KEY,
    STREAM_KIND_KEY,
    STREAM_KIND_SESSION,
)
from shikiru.plugins.agents.muse.session_log import NO_SESSION_LOG_MARK
from shikiru.plugins.agents.muse.turn import MuseTurnParser, SessionWatch, classify_failure
from tests.support.clock import FakeClock

NOW = datetime(2026, 11, 12, 12, tzinfo=UTC)
_SESSION_ID = "muse-session"
_MILLISECONDS = 1000


def _window(used_pct: float, resets_at: datetime | None = None) -> QuotaWindow:
    return QuotaWindow(
        name=QUOTA_WINDOW_FIVE_HOUR, used_pct=used_pct, resets_at=resets_at, observed_at=NOW
    )


def _classify(reason: str, windows: tuple[QuotaWindow, ...] = ()) -> TurnFailed:
    return classify_failure(reason, windows, NOW, ResetTimeParser(FakeClock(NOW)))


_EXHAUSTED = (_window(100.0),)
_NOT_EXHAUSTED = (_window(99.9),)


@pytest.mark.parametrize(
    ("reason", "windows", "kind"),
    [
        (
            "your API key from META_API_KEY was rejected — update or unset it",
            (),
            FailureKind.AUTH,
        ),
        (
            "missing meta credentials: run `muse login` or set META_API_KEY",
            (),
            FailureKind.AUTH,
        ),
        ("muse usage: not logged in: run `muse login` first", (), FailureKind.AUTH),
        ("429 Too Many Requests: usage limit reached", (), FailureKind.QUOTA),
        ("HTTP 429: quota exceeded", (), FailureKind.QUOTA),
        ("usage limit reached", (), FailureKind.UNKNOWN),
        ("HTTP 429 with no detail", (), FailureKind.UNKNOWN),
        ("something went wrong", _EXHAUSTED, FailureKind.QUOTA),
        ("something went wrong", _NOT_EXHAUSTED, FailureKind.UNKNOWN),
        ("the model is overloaded, try again", (), FailureKind.UNAVAILABLE),
        ("service unavailable", (), FailureKind.UNAVAILABLE),
        ("upstream returned HTTP 503", (), FailureKind.UNAVAILABLE),
        ("status code: 500", (), FailureKind.UNAVAILABLE),
        ("upstream failed with 500 Internal Server Error", (), FailureKind.UNAVAILABLE),
        ("request_id=503 could not be stored", (), FailureKind.UNKNOWN),
        ("retry in 503 ms", (), FailureKind.UNKNOWN),
        ("session 5012 ended unexpectedly", (), FailureKind.UNKNOWN),
        (
            "model `x` does not exist or you lack access [request_id=req-1]",
            (),
            FailureKind.OTHER,
        ),
        (
            "model did not reach a terminal state within 503 step(s)",
            (),
            FailureKind.OTHER,
        ),
        ("", (), FailureKind.UNKNOWN),
        ("stream reset by peer", (), FailureKind.UNKNOWN),
    ],
)
def test_failure_text_is_classified(
    reason: str, windows: tuple[QuotaWindow, ...], kind: FailureKind
) -> None:
    assert _classify(reason, windows).kind is kind


_RESET = NOW + timedelta(hours=2)
_LATER_RESET = NOW + timedelta(hours=3)
_QUOTA_REASON = "429 usage limit reached"


@pytest.mark.parametrize(
    ("reason", "windows", "expected"),
    [
        (_QUOTA_REASON, (_window(100.0, _RESET),), _RESET),
        (_QUOTA_REASON, (_window(100.0, _RESET), _window(120.0, _LATER_RESET)), _LATER_RESET),
        (_QUOTA_REASON, (_window(100.0, _RESET), _window(50.0, _LATER_RESET)), _RESET),
        (_QUOTA_REASON, (_window(100.0, NOW - timedelta(hours=1)),), None),
        (_QUOTA_REASON, (_window(100.0, NOW + timedelta(days=8)),), None),
        (_QUOTA_REASON, (_window(100.0, None),), None),
        (_QUOTA_REASON, (), None),
        (
            "429 usage limit reached, resets at 2026-11-12T14:30:00Z",
            (),
            datetime(2026, 11, 12, 14, 30, tzinfo=UTC),
        ),
        (
            "429 usage limit reached, resets at 2026-11-12T14:30:00Z",
            (_window(100.0, _RESET),),
            datetime(2026, 11, 12, 14, 30, tzinfo=UTC),
        ),
        (
            "429 usage limit reached, resets at 2026-11-12T10:00:00Z",
            (),
            None,
        ),
        (
            "429 usage limit reached, resets at 2026-12-01T00:00:00Z",
            (),
            None,
        ),
    ],
    ids=(
        "exhausted-window-reset",
        "latest-exhausted-window-wins",
        "unexhausted-window-ignored",
        "past-window-reset-ignored",
        "absurd-window-reset-ignored",
        "window-without-reset",
        "no-evidence",
        "iso-text-reset",
        "iso-text-beats-earlier-window-reset",
        "past-iso-text-ignored",
        "absurd-iso-text-ignored",
    ),
)
def test_quota_reset_is_latest_plausible_evidence(
    reason: str, windows: tuple[QuotaWindow, ...], expected: datetime | None
) -> None:
    failure = _classify(reason, windows)

    assert failure.kind is FailureKind.QUOTA
    assert failure.resets_at == expected


def test_non_quota_failure_carries_no_reset() -> None:
    assert _classify("service unavailable", _EXHAUSTED).resets_at is None


def _record(payload_type: str, payload: object) -> str:
    return json.dumps(
        {
            RECORD_STREAM_KEY: {STREAM_KIND_KEY: STREAM_KIND_SESSION, STREAM_ID_KEY: _SESSION_ID},
            RECORD_PAYLOAD_TYPE_KEY: payload_type,
            RECORD_PAYLOAD_KEY: payload,
        }
    )


def _allowance_record(used_pct: float) -> str:
    now_ms = int(NOW.timestamp()) * _MILLISECONDS
    return _record(
        RUN_USAGE_TYPE,
        {
            "models": [],
            "allowance": {"as_of_ms": now_ms, "window": {"used_percent": used_pct}},
        },
    )


def _parser() -> MuseTurnParser:
    clock = FakeClock(NOW)
    watch = SessionWatch(
        home=Path("muse-home-without-sessions"),
        session_id=None,
        context_window_tokens=1000,
        mark=NO_SESSION_LOG_MARK,
    )
    return MuseTurnParser(clock, ResetTimeParser(clock), watch)


def test_failed_terminal_is_classified_with_the_turn_allowance() -> None:
    parser = _parser()
    parser.feed(_allowance_record(100.0))
    parser.feed(_record(RUN_TERMINAL_FAILED_TYPE, {"reason": "step cap", "text": ""}))

    [failure] = [signal for signal in parser.finish(1, "") if isinstance(signal, TurnFailed)]

    assert failure == TurnFailed(FailureKind.QUOTA, "step cap", None)


@pytest.mark.parametrize(
    "stderr",
    [
        "missing meta credentials: run `muse login` or set META_API_KEY",
        "some unrecognised crash",
        "",
    ],
    ids=("credentials-stderr", "unrecognised-stderr", "empty-stderr"),
)
def test_exit_without_terminal_record_is_unknown_with_stderr_tail(stderr: str) -> None:
    [failure] = _parser().finish(1, stderr)

    assert isinstance(failure, TurnFailed)
    assert failure.kind is FailureKind.UNKNOWN
    assert stderr in failure.message
    assert failure.resets_at is None
