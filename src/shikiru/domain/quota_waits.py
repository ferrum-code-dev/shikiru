"""The one wording for a quota wait, shared by the task list, Usage and task details."""

from __future__ import annotations

from datetime import UTC
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

QUOTA_BLOCKED_PHRASE = "quota blocked until"
QUOTA_WAIT_CLOCK_FORMAT = "%H:%M"
QUOTA_WAIT_DATE_FORMAT = "%b %d"
QUOTA_WAIT_UTC_SUFFIX = "Z"
QUOTA_BANNER_SEPARATOR = " · "
QUOTA_WAITING_SUFFIX = "waiting"
QUOTA_TASK_NOUN = "task"
QUOTA_TASKS_NOUN = "tasks"


def quota_wait_description(agent: str, until: datetime, now: datetime) -> str:
    """Name the blocked `agent` and when its block lifts, in UTC; the date is shown off today."""
    deadline = until.astimezone(UTC)
    clock = deadline.strftime(QUOTA_WAIT_CLOCK_FORMAT)
    if deadline.date() != now.astimezone(UTC).date():
        clock = f"{deadline.strftime(QUOTA_WAIT_DATE_FORMAT)} {clock}"
    return f"{agent} {QUOTA_BLOCKED_PHRASE} {clock}{QUOTA_WAIT_UTC_SUFFIX}"


def quota_banner(description: str, waiting_tasks: int) -> str:
    """Append how many tasks wait behind the block to its `description`, when any do."""
    if waiting_tasks == 0:
        return description
    noun = QUOTA_TASK_NOUN if waiting_tasks == 1 else QUOTA_TASKS_NOUN
    return f"{description}{QUOTA_BANNER_SEPARATOR}{waiting_tasks} {noun} {QUOTA_WAITING_SUFFIX}"
