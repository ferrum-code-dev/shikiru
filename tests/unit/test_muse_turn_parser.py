"""Muse turn parser behavior."""

from __future__ import annotations

import json
from typing import Any

import pytest

from shikiru.agents.contracts import FailureKind, SessionStarted, TurnFailed
from shikiru.plugins.agents.muse.options import (
    RECORD_PAYLOAD_KEY,
    RECORD_PAYLOAD_TYPE_KEY,
    RECORD_STREAM_KEY,
    RUN_USAGE_TYPE,
    STREAM_ID_KEY,
    STREAM_KIND_KEY,
    STREAM_KIND_SESSION,
)
from shikiru.plugins.agents.muse.turn import MuseTurnParser

_SESSION_ID = "muse-session"
_INVALID_PERCENTAGES = (10**400, float("inf"), float("-inf"), float("nan"))
_INVALID_PERCENTAGE_IDS = ("integer-overflow", "positive-infinity", "negative-infinity", "nan")


@pytest.mark.parametrize("used_percent", _INVALID_PERCENTAGES, ids=_INVALID_PERCENTAGE_IDS)
def test_invalid_allowance_percentage_fails_turn_as_malformed(used_percent: Any) -> None:
    parser = MuseTurnParser()
    line = json.dumps(
        {
            RECORD_STREAM_KEY: {
                STREAM_KIND_KEY: STREAM_KIND_SESSION,
                STREAM_ID_KEY: _SESSION_ID,
            },
            RECORD_PAYLOAD_TYPE_KEY: RUN_USAGE_TYPE,
            RECORD_PAYLOAD_KEY: {
                "models": [],
                "allowance": {
                    "as_of_ms": 0,
                    "window": {"used_percent": used_percent},
                },
            },
        }
    )

    assert parser.feed(line) == [SessionStarted(_SESSION_ID)]
    signals = parser.finish(0, "")

    assert len(signals) == 1
    assert isinstance(signals[0], TurnFailed)
    assert signals[0].kind is FailureKind.UNKNOWN
