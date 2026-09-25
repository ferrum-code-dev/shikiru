from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from shikiru.config.settings import TimeoutsConfig
from shikiru.domain.activity import is_stalled

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_TIMEOUTS = TimeoutsConfig(
    stall=1200, tool=7200, max_quota_wait=28800, integration_wait=1800, helper=300
)


@pytest.mark.parametrize(
    ("elapsed_seconds", "tool_running", "expected"),
    [
        (_TIMEOUTS.stall - 1, False, False),
        (_TIMEOUTS.stall, False, True),
        (_TIMEOUTS.tool - 1, True, False),
        (_TIMEOUTS.tool, True, True),
        (_TIMEOUTS.stall, True, False),
    ],
    ids=[
        "below-stall-timeout",
        "at-stall-timeout",
        "below-tool-timeout",
        "at-tool-timeout",
        "stall-timeout-does-not-apply-to-a-running-tool",
    ],
)
def test_is_stalled_uses_the_timeout_matching_the_activity_kind(
    elapsed_seconds: int, tool_running: bool, expected: bool
) -> None:
    last_activity = _NOW - timedelta(seconds=elapsed_seconds)

    assert is_stalled(last_activity, tool_running, _NOW, _TIMEOUTS) is expected
