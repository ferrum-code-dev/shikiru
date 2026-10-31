from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from shikiru.domain.quota_waits import (
    next_quota_reload,
    quota_banner,
    quota_clock_text,
    quota_remaining,
    quota_wait_description,
)

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
BEFORE_MIDNIGHT = datetime(2026, 1, 1, 23, 59, 30, tzinfo=UTC)
AFTER_MIDNIGHT = datetime(2026, 1, 2, 0, 0, tzinfo=UTC)
AHEAD_OF_UTC = timezone(timedelta(hours=2))
BEHIND_UTC = timezone(timedelta(hours=-5))
SAME_DAY_RESET = datetime(2026, 1, 1, 14, 20, tzinfo=UTC)
NEXT_DAY_RESET = datetime(2026, 1, 2, 0, 5, tzinfo=UTC)
RESET_AFTER_MIDNIGHT = datetime(2026, 1, 2, 1, 0, tzinfo=UTC)
DESCRIPTION = "claude quota blocked until 14:20Z"
SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 60 * SECONDS_PER_MINUTE
SECONDS_PER_DAY = 24 * SECONDS_PER_HOUR


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
    ("now", "expected"),
    [
        pytest.param(
            BEFORE_MIDNIGHT,
            "claude quota blocked until Jan 02 01:00Z",
            id="before-utc-midnight",
        ),
        pytest.param(AFTER_MIDNIGHT, "claude quota blocked until 01:00Z", id="after-utc-midnight"),
        pytest.param(
            datetime(2026, 1, 2, 0, 30, tzinfo=BEHIND_UTC),
            "claude quota blocked until 01:00Z",
            id="utc-date-decides-in-another-zone",
        ),
    ],
)
def test_quota_wait_description_drops_the_date_when_the_clock_reaches_the_reset_day(
    now: datetime,
    expected: str,
) -> None:
    assert quota_wait_description("claude", RESET_AFTER_MIDNIGHT, now) == expected


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


@pytest.mark.parametrize(
    ("seconds_left", "expected"),
    [
        pytest.param(SECONDS_PER_HOUR + 20 * SECONDS_PER_MINUTE, "1h 20m", id="whole-minutes"),
        pytest.param(SECONDS_PER_HOUR + 20 * SECONDS_PER_MINUTE - 1, "1h 19m", id="partial-minute"),
        pytest.param(3 * SECONDS_PER_MINUTE, "3m", id="minutes-only"),
        pytest.param(SECONDS_PER_MINUTE - 1, "<1m", id="under-a-minute"),
        pytest.param(0, "<1m", id="lifts-now"),
        pytest.param(-SECONDS_PER_MINUTE, "<1m", id="already-lifted"),
        pytest.param(SECONDS_PER_DAY + SECONDS_PER_HOUR, "1d 1h 0m", id="days-hours-and-minutes"),
        pytest.param(
            2 * SECONDS_PER_DAY + 3 * SECONDS_PER_HOUR + 4 * SECONDS_PER_MINUTE,
            "2d 3h 4m",
            id="several-days",
        ),
    ],
)
def test_quota_remaining_counts_the_whole_minutes_left_until_the_block_lifts(
    seconds_left: int,
    expected: str,
) -> None:
    assert quota_remaining(NOW + timedelta(seconds=seconds_left), NOW) == expected


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        pytest.param(
            BEFORE_MIDNIGHT,
            ("claude quota blocked until Jan 02 01:00Z", "1h 0m"),
            id="before-utc-midnight",
        ),
        pytest.param(
            AFTER_MIDNIGHT,
            ("claude quota blocked until 01:00Z", "1h 0m"),
            id="after-utc-midnight-with-the-same-minutes",
        ),
    ],
)
def test_quota_clock_text_changes_at_utc_midnight_even_when_the_minutes_stay_the_same(
    now: datetime,
    expected: tuple[str, str],
) -> None:
    assert quota_clock_text("claude", RESET_AFTER_MIDNIGHT, now) == expected


@pytest.mark.parametrize(
    ("now", "instants", "expected"),
    [
        pytest.param(
            NOW,
            (NOW + timedelta(hours=1),),
            NOW + timedelta(hours=1),
            id="block-lifts",
        ),
        pytest.param(
            NOW,
            (NOW - timedelta(hours=1), NOW + timedelta(hours=2)),
            NOW + timedelta(hours=2),
            id="past-instant-ignored",
        ),
        pytest.param(
            NOW, (NOW, NOW + timedelta(hours=2)), NOW + timedelta(hours=2), id="now-ignored"
        ),
        pytest.param(
            NOW,
            (NOW + timedelta(hours=3), NOW + timedelta(hours=1)),
            NOW + timedelta(hours=1),
            id="earliest-instant-wins",
        ),
        pytest.param(
            BEFORE_MIDNIGHT,
            (RESET_AFTER_MIDNIGHT,),
            RESET_AFTER_MIDNIGHT,
            id="utc-midnight-is-not-a-reload-instant",
        ),
    ],
)
def test_next_quota_reload_is_the_first_instant_after_now(
    now: datetime,
    instants: tuple[datetime, ...],
    expected: datetime,
) -> None:
    assert next_quota_reload(now, instants) == expected
