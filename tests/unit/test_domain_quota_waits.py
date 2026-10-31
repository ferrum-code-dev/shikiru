from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from shikiru.domain.quota_waits import quota_banner, quota_wait_description

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
AHEAD_OF_UTC = timezone(timedelta(hours=2))
SAME_DAY_RESET = datetime(2026, 1, 1, 14, 20, tzinfo=UTC)
NEXT_DAY_RESET = datetime(2026, 1, 2, 0, 5, tzinfo=UTC)
DESCRIPTION = "claude quota blocked until 14:20Z"


@pytest.mark.parametrize(
    ("until", "expected"),
    [
        pytest.param(SAME_DAY_RESET, DESCRIPTION, id="same-utc-day"),
        pytest.param(
            NEXT_DAY_RESET,
            "claude quota blocked until Jan 02 00:05Z",
            id="other-utc-day",
        ),
        pytest.param(
            SAME_DAY_RESET.astimezone(AHEAD_OF_UTC),
            DESCRIPTION,
            id="reset-given-in-another-zone",
        ),
    ],
)
def test_quota_wait_description_shows_the_reset_in_utc_and_the_date_only_off_today(
    until: datetime,
    expected: str,
) -> None:
    assert quota_wait_description("claude", until, NOW) == expected


@pytest.mark.parametrize(
    ("waiting_tasks", "expected"),
    [
        pytest.param(0, DESCRIPTION, id="no-task-waits"),
        pytest.param(1, f"{DESCRIPTION} · 1 task waiting", id="one-task-waits"),
        pytest.param(3, f"{DESCRIPTION} · 3 tasks waiting", id="several-tasks-wait"),
    ],
)
def test_quota_banner_counts_the_tasks_waiting_behind_the_block(
    waiting_tasks: int,
    expected: str,
) -> None:
    assert quota_banner(DESCRIPTION, waiting_tasks) == expected
