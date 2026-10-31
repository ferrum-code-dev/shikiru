"""The one wording for a quota wait, shared by the task list, Usage and task details."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

QUOTA_BLOCKED_PHRASE = "quota blocked until"
QUOTA_AGENT_LABEL = "Blocked agent"
QUOTA_WAIT_CLOCK_FORMAT = "%H:%M"
QUOTA_WAIT_DATE_FORMAT = "%b %d"
QUOTA_WAIT_UTC_SUFFIX = "Z"
QUOTA_UNDER_A_MINUTE = "<1m"
QUOTA_BANNER_SEPARATOR = " · "
QUOTA_WAITING_SUFFIX = "waiting"
QUOTA_TASK_NOUN = "task"
QUOTA_TASKS_NOUN = "tasks"
_SECONDS_PER_MINUTE = 60
_MINUTES_PER_HOUR = 60
_HOURS_PER_DAY = 24
_MINUTES_PER_DAY = _HOURS_PER_DAY * _MINUTES_PER_HOUR


def quota_reset_clock(until: datetime, now: datetime) -> str:
    """Return when a block lifts, in UTC; the date is shown when it is not today."""
    deadline = until.astimezone(UTC)
    clock = deadline.strftime(QUOTA_WAIT_CLOCK_FORMAT)
    if deadline.date() != now.astimezone(UTC).date():
        clock = f"{deadline.strftime(QUOTA_WAIT_DATE_FORMAT)} {clock}"
    return f"{clock}{QUOTA_WAIT_UTC_SUFFIX}"


def quota_wait_description(agent: str, until: datetime, now: datetime) -> str:
    """Name the blocked `agent` and when its block lifts, in UTC; the date is shown off today."""
    return f"{agent} {QUOTA_BLOCKED_PHRASE} {quota_reset_clock(until, now)}"


def quota_banner(description: str, waiting_tasks: int) -> str:
    """Append how many tasks wait behind the block to its `description`, when any do."""
    if waiting_tasks == 0:
        return description
    noun = QUOTA_TASK_NOUN if waiting_tasks == 1 else QUOTA_TASKS_NOUN
    return f"{description}{QUOTA_BANNER_SEPARATOR}{waiting_tasks} {noun} {QUOTA_WAITING_SUFFIX}"


def quota_remaining(until: datetime, now: datetime) -> str:
    """Return the whole minutes left until a block lifts, as `1d 2h 3m`, `2h 3m` or `3m`."""
    minutes = max(int((until - now).total_seconds()), 0) // _SECONDS_PER_MINUTE
    if minutes == 0:
        return QUOTA_UNDER_A_MINUTE
    days, minutes = divmod(minutes, _MINUTES_PER_DAY)
    hours, minutes = divmod(minutes, _MINUTES_PER_HOUR)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def quota_clock_text(agent: str, until: datetime, now: datetime) -> tuple[str, str]:
    """Return the clock-driven wording of a quota wait: its description and its time left."""
    return quota_wait_description(agent, until, now), quota_remaining(until, now)


def next_quota_reload(now: datetime, instants: Iterable[datetime]) -> datetime:
    """Return the first of `instants` after `now`, the next moment a quota wait's data changes.

    `instants` are the moments the block lifts, its reroute window closes and other blocks lift.
    The wording moves with the clock on its own and never needs this reload.
    """
    return min(instant for instant in instants if instant > now)
