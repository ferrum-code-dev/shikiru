"""Human project status task selection."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.domain.scheduling import dispatch_sort_key
from shikiru.domain.tasks import Status

if TYPE_CHECKING:
    from collections.abc import Sequence

    from shikiru.domain.tasks import Task

HUMAN_STATUS_TASK_LIMIT = 10
_OTHER_ACTIVE_STATUSES = (Status.RUNNING, Status.INTEGRATING)


def select_human_status_tasks(tasks: Sequence[Task]) -> tuple[Task, ...]:
    """Select at most ten tasks, reserving the final row for a latest recorded completion."""
    escalated = sorted(
        (task for task in tasks if task.status is Status.ESCALATED), key=lambda task: task.seq
    )
    active = sorted(
        (task for task in tasks if task.status in _OTHER_ACTIVE_STATUSES),
        key=lambda task: task.seq,
    )
    queued = sorted((task for task in tasks if task.status is Status.QUEUED), key=dispatch_sort_key)
    completed = [task for task in tasks if task.status is Status.DONE]
    with_finish_time = [task for task in completed if task.finished_at is not None]
    done = (
        max(with_finish_time, key=lambda task: (task.finished_at, task.seq))
        if with_finish_time
        else max(completed, key=lambda task: task.seq, default=None)
    )
    available_rows = HUMAN_STATUS_TASK_LIMIT - (done is not None)
    selected = (escalated + active + queued)[:available_rows]
    if done is not None:
        selected.append(done)
    return tuple(selected)
