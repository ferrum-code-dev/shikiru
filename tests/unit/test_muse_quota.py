"""Muse account quota parsing from `muse usage --json` output."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from shikiru.agents.contracts import QUOTA_WINDOW_FIVE_HOUR, QUOTA_WINDOW_SEVEN_DAY
from shikiru.errors import ShikiruError
from shikiru.plugins.agents.muse.quota import parse_usage

_FIXTURE = Path(__file__).parents[1] / "fixtures" / "agents" / "muse" / "usage_logged_in.json"
_OBSERVED_MS = 1794502717750
_OBSERVED = datetime.fromtimestamp(_OBSERVED_MS / 1000, tz=UTC)
_BEFORE = _OBSERVED - timedelta(seconds=1)
_FIVE_HOUR_RESET_MS = 1794514840000
_WEEKLY_RESET_MS = 1794787200000
_LONG_AGO = datetime(2000, 1, 1, tzinfo=UTC)


def _usage(allowance: object) -> str:
    return json.dumps({"schemaVersion": 1, "allowance": allowance, "reason": None, "hint": None})


def _allowance(**overrides: Any) -> dict[str, Any]:
    allowance: dict[str, Any] = {
        "tier": "Muse Code Everyday Usage",
        "window": {"usedPercent": 10, "resetsAtMs": _FIVE_HOUR_RESET_MS},
        "weekly": {"usedPercent": 3, "resetsAtMs": _WEEKLY_RESET_MS},
        "asOfMs": _OBSERVED_MS,
        "source": "mint",
    }
    allowance.update(overrides)
    return allowance


def _without(key: str) -> dict[str, Any]:
    allowance = _allowance()
    del allowance[key]
    return allowance


def test_logged_in_usage_reads_both_windows_with_their_resets() -> None:
    reading = parse_usage(_FIXTURE.read_text(encoding="utf-8"), _LONG_AGO)

    assert reading is not None
    assert reading.observed_at == _OBSERVED
    windows = {window.name: window for window in reading.windows}
    assert set(windows) == {QUOTA_WINDOW_FIVE_HOUR, QUOTA_WINDOW_SEVEN_DAY}
    assert windows[QUOTA_WINDOW_FIVE_HOUR].used_pct == 10
    assert windows[QUOTA_WINDOW_FIVE_HOUR].resets_at == datetime.fromtimestamp(
        _FIVE_HOUR_RESET_MS / 1000, tz=UTC
    )
    assert windows[QUOTA_WINDOW_SEVEN_DAY].used_pct == 3
    assert windows[QUOTA_WINDOW_SEVEN_DAY].resets_at == datetime.fromtimestamp(
        _WEEKLY_RESET_MS / 1000, tz=UTC
    )


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(_usage(None), id="null-allowance"),
        pytest.param(_usage(_allowance(window=None, weekly=None)), id="no-window"),
    ],
)
def test_usage_without_a_window_has_no_reading(text: str) -> None:
    assert parse_usage(text, _LONG_AGO) is None


@pytest.mark.parametrize(
    ("since", "readable"),
    [
        pytest.param(_BEFORE, True, id="observed-after-since"),
        pytest.param(_OBSERVED, False, id="observed-at-since"),
        pytest.param(_OBSERVED + timedelta(seconds=1), False, id="observed-before-since"),
    ],
)
def test_reading_is_usable_only_when_observed_after_since(since: datetime, readable: bool) -> None:
    reading = parse_usage(_usage(_allowance()), since)

    assert (reading is not None) is readable


@pytest.mark.parametrize(
    ("used_percent", "expected"),
    [
        pytest.param(42.5, 42.5, id="within-range"),
        pytest.param(150, 100.0, id="above-full"),
    ],
)
def test_used_percent_is_capped_at_the_full_window(used_percent: float, expected: float) -> None:
    reading = parse_usage(
        _usage(_allowance(window={"usedPercent": used_percent, "resetsAtMs": None})), _LONG_AGO
    )

    assert reading is not None
    assert reading.windows[0].used_pct == expected


def test_explicit_null_window_is_not_reported() -> None:
    reading = parse_usage(_usage(_allowance(window=None)), _LONG_AGO)

    assert reading is not None
    assert [window.name for window in reading.windows] == [QUOTA_WINDOW_SEVEN_DAY]


def test_window_without_a_reset_keeps_its_reset_unknown() -> None:
    reading = parse_usage(
        _usage(_allowance(window={"usedPercent": 10, "resetsAtMs": None})), _LONG_AGO
    )

    assert reading is not None
    assert reading.windows[0].resets_at is None


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("not json", id="not-json"),
        pytest.param(json.dumps([]), id="not-an-object"),
        pytest.param(_usage("allowance"), id="allowance-not-an-object"),
        pytest.param('{"schemaVersion": 1, "reason": null}', id="missing-allowance"),
        pytest.param("{}", id="empty-object"),
        pytest.param(_usage(_without("window")), id="missing-window"),
        pytest.param(_usage(_without("weekly")), id="missing-weekly"),
        pytest.param(_usage(_allowance(window={"usedPercent": 10})), id="missing-reset-field"),
        pytest.param(_usage(_allowance(asOfMs=None)), id="missing-observation-time"),
        pytest.param(_usage(_allowance(window={"usedPercent": "10"})), id="percent-not-a-number"),
        pytest.param(_usage(_allowance(window={"usedPercent": True})), id="percent-is-boolean"),
        pytest.param(
            _usage(_allowance(window={"usedPercent": 10, "resetsAtMs": "soon"})),
            id="reset-not-an-integer",
        ),
    ],
)
def test_unreadable_usage_raises_a_shikiru_error(text: str) -> None:
    with pytest.raises(ShikiruError):
        parse_usage(text, _LONG_AGO)
