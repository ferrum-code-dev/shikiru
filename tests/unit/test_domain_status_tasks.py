from __future__ import annotations

from datetime import UTC, datetime

import pytest

from shikiru.domain.status_tasks import HUMAN_STATUS_TASK_LIMIT, select_human_status_tasks
from shikiru.domain.tasks import Status, Task
from tests.support.factories import make_task

_EARLIER_FINISH = datetime(2026, 1, 1, 0, 1, tzinfo=UTC)
_LATEST_FINISH = datetime(2026, 1, 1, 0, 2, tzinfo=UTC)
_REQUEUE_TIME = datetime(2026, 1, 1, 0, 3, tzinfo=UTC)


def _task(
    seq: int,
    status: Status,
    *,
    priority: int = 0,
    requeued_at: datetime | None = None,
    finished_at: datetime | None = None,
) -> Task:
    return make_task(
        id=f"T-{seq}",
        seq=seq,
        status=status,
        priority=priority,
        requeued_at=requeued_at,
        finished_at=finished_at,
    )


@pytest.mark.parametrize(
    ("tasks", "expected_ids"),
    [
        pytest.param(
            [
                _task(9, Status.QUEUED, priority=3),
                _task(2, Status.ESCALATED),
                _task(17, Status.DONE, finished_at=_EARLIER_FINISH),
                _task(4, Status.RUNNING),
                _task(1, Status.ESCALATED),
                _task(16, Status.DONE, finished_at=_LATEST_FINISH),
                _task(10, Status.QUEUED, priority=2),
                _task(3, Status.INTEGRATING),
                _task(5, Status.RUNNING),
                _task(6, Status.QUEUED, priority=6),
                _task(7, Status.QUEUED, priority=5),
                _task(8, Status.QUEUED, priority=4),
                _task(11, Status.QUEUED, priority=1),
                _task(12, Status.QUEUED),
                _task(13, Status.QUEUED),
                _task(14, Status.QUEUED),
                _task(15, Status.QUEUED),
            ],
            ["T-1", "T-2", "T-3", "T-4", "T-5", "T-6", "T-7", "T-8", "T-9", "T-16"],
            id="escalated-active-dispatch-and-latest-done",
        ),
        pytest.param(
            [
                _task(1, Status.RUNNING),
                *[_task(seq, Status.QUEUED) for seq in range(2, 5)],
                *[_task(seq, Status.DONE, finished_at=_EARLIER_FINISH) for seq in range(5, 13)],
                _task(13, Status.DONE, finished_at=_LATEST_FINISH),
                _task(14, Status.DONE, finished_at=_LATEST_FINISH),
            ],
            ["T-1", "T-2", "T-3", "T-4", "T-14"],
            id="reserve-one-latest-completion-with-sequence-tie-break",
        ),
        pytest.param(
            [
                *[_task(seq, Status.ESCALATED) for seq in range(1, 13)],
                _task(13, Status.RUNNING),
                _task(14, Status.INTEGRATING),
                _task(15, Status.DONE, finished_at=_LATEST_FINISH),
            ],
            [*[f"T-{seq}" for seq in range(1, HUMAN_STATUS_TASK_LIMIT)], "T-15"],
            id="active-groups-cannot-displace-reserved-done",
        ),
        pytest.param(
            [
                _task(1, Status.QUEUED, priority=1),
                _task(2, Status.QUEUED, priority=8),
                _task(3, Status.QUEUED, requeued_at=_REQUEUE_TIME),
                *[_task(seq, Status.CANCELLED) for seq in range(4, 15)],
            ],
            ["T-3", "T-2", "T-1"],
            id="dispatch-order-and-cancelled-exclusion-without-done",
        ),
        pytest.param(
            [_task(seq, Status.ESCALATED) for seq in range(1, 13)],
            [f"T-{seq}" for seq in range(1, HUMAN_STATUS_TASK_LIMIT + 1)],
            id="active-only-output-stops-at-the-row-limit",
        ),
        pytest.param(
            [
                _task(1, Status.DONE),
                _task(2, Status.DONE),
                _task(4, Status.CANCELLED, finished_at=_LATEST_FINISH),
                _task(3, Status.QUEUED),
            ],
            ["T-3", "T-2"],
            id="missing-finish-time-is-not-inferred-and-cancelled-does-not-substitute",
        ),
        pytest.param(
            [
                _task(1, Status.DONE, finished_at=_LATEST_FINISH),
                _task(2, Status.DONE),
                _task(3, Status.QUEUED),
            ],
            ["T-3", "T-1"],
            id="recorded-finish-time-outranks-unknown-legacy-time",
        ),
        pytest.param([], [], id="empty-project"),
    ],
)
def test_human_status_selection_preserves_priority_and_completion(
    tasks: list[Task], expected_ids: list[str]
) -> None:
    selected = select_human_status_tasks(tasks)

    assert [task.id for task in selected] == expected_ids
    assert len(selected) <= HUMAN_STATUS_TASK_LIMIT
