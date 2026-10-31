from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from shikiru.domain.pushes import PushTarget, is_due, pending_deadline

NOW = datetime(2026, 1, 1, 12, tzinfo=UTC)
HOUR = 3600
INTERVAL = 4 * HOUR


@pytest.mark.parametrize(
    ("last_pushed_at", "interval", "expected"),
    [
        pytest.param(None, INTERVAL, NOW + timedelta(seconds=INTERVAL), id="first-starts-interval"),
        pytest.param(NOW - timedelta(hours=1), INTERVAL, NOW + timedelta(hours=3), id="after-push"),
        pytest.param(NOW - timedelta(hours=5), INTERVAL, NOW, id="overdue-is-immediate"),
        pytest.param(NOW - timedelta(hours=1), 0, NOW, id="zero-is-immediate"),
        pytest.param(None, 0, NOW, id="zero-without-push"),
    ],
)
def test_deadline_of_newly_pending_work(
    last_pushed_at: datetime | None, interval: int, expected: datetime
) -> None:
    assert pending_deadline(last_pushed_at=last_pushed_at, now=NOW, interval=interval) == expected


def _target(*, registered: int, pushed: int, due_at: datetime | None) -> PushTarget:
    return PushTarget(
        base="main", registered=registered, pushed=pushed, due_at=due_at, last_pushed_at=None
    )


@pytest.mark.parametrize(
    ("target", "interval", "due"),
    [
        pytest.param(_target(registered=1, pushed=0, due_at=NOW), INTERVAL, True, id="deadline"),
        pytest.param(
            _target(registered=1, pushed=0, due_at=NOW + timedelta(seconds=1)),
            INTERVAL,
            False,
            id="before-deadline",
        ),
        pytest.param(
            _target(registered=1, pushed=0, due_at=NOW + timedelta(hours=1)),
            0,
            True,
            id="zero-ignores-deadline",
        ),
        pytest.param(_target(registered=2, pushed=2, due_at=None), 0, False, id="nothing-pending"),
    ],
)
def test_pending_work_is_due_at_its_deadline_or_always_for_zero(
    target: PushTarget, interval: int, due: bool
) -> None:
    assert is_due(target, now=NOW, interval=interval) is due
