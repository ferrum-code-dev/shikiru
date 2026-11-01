"""Quota wait wording: the block's clock text, the provider reset and the engine retry note."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from operator import itemgetter
from typing import TYPE_CHECKING, Protocol

from shikiru.agents.contracts import (
    QUOTA_WINDOW_FIVE_HOUR,
    QUOTA_WINDOW_SEVEN_DAY,
    FailureKind,
    is_exhausted,
)
from shikiru.domain.events import EventKind
from shikiru.domain.recovery import quota_block_until

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from datetime import datetime

    from shikiru.agents.contracts import QuotaWindow
    from shikiru.domain.events import Event
    from shikiru.domain.tasks import Role, Task

QUOTA_BLOCKED_PHRASE = "quota blocked until"
QUOTA_RETRY_PHRASE = "retry at"
QUOTA_WORD = "quota"
ENGINE_RETRY_DIFFERS = "Engine retry differs from the {window} reset."
QUOTA_TODAY_WORD = "Today"
QUOTA_NOT_REPORTED = "not reported"
QUOTA_WINDOW_LABELS = {QUOTA_WINDOW_FIVE_HOUR: "5-hour", QUOTA_WINDOW_SEVEN_DAY: "weekly"}
QUOTA_WAIT_CLOCK_FORMAT = "%H:%M"
QUOTA_WAIT_DATE_FORMAT = "%b %d"
QUOTA_WAIT_ZERO_OFFSET_SUFFIX = "Z"
QUOTA_UNDER_A_MINUTE = "<1m"
QUOTA_BANNER_SEPARATOR = " · "
QUOTA_WAITING_SUFFIX = "waiting"
QUOTA_TASK_NOUN = "task"
QUOTA_TASKS_NOUN = "tasks"
QUOTA_TURN_LIFECYCLE = (
    EventKind.STEP_STARTED,
    EventKind.TURN_STARTED,
    EventKind.TURN_COMPLETED,
    EventKind.TURN_FAILED,
    EventKind.TURN_STOPPED,
    EventKind.RUNG_CHANGED,
)
_SECONDS_PER_MINUTE = 60
_MINUTES_PER_HOUR = 60
_HOURS_PER_DAY = 24
_MINUTES_PER_DAY = _HOURS_PER_DAY * _MINUTES_PER_HOUR


class QuotaAction(StrEnum):
    """What the engine does at the time a quota wait shows: probe for newer evidence or retry."""

    NEXT_CHECK = "next check"
    RETRY = "retry"


@dataclass(frozen=True)
class ScheduledQuotaAction:
    """The engine's next action on the quota block set at `blocked_at`, and when it happens."""

    action: QuotaAction
    at: datetime
    blocked_at: datetime


def quota_reset_clock(until: datetime, now: datetime) -> str:
    """Return when a block lifts in the zone `until` and `now` carry, as the viewer's local time.

    The date is shown when the reset falls on another local day than `now`; `Z` marks only a
    zero offset, so a non-UTC local time never carries it.
    """
    clock = until.strftime(QUOTA_WAIT_CLOCK_FORMAT)
    if until.date() != now.date():
        clock = f"{until.strftime(QUOTA_WAIT_DATE_FORMAT)} {clock}"
    suffix = QUOTA_WAIT_ZERO_OFFSET_SUFFIX if until.utcoffset() == timedelta(0) else ""
    return f"{clock}{suffix}"


def provider_reset_text(resets_at: datetime, now: datetime) -> str:
    """Return a provider's reset as `Today` and a clock on today's local day, else as a clock."""
    clock = quota_reset_clock(resets_at, now)
    if resets_at.date() == now.date():
        return f"{QUOTA_TODAY_WORD} {clock}"
    return clock


def quota_window_label(name: str) -> str:
    """Return the words a quota window is named by, such as `5-hour` for `5h`."""
    return QUOTA_WINDOW_LABELS.get(name, name)


def quota_wait_description(agent: str, until: datetime, now: datetime) -> str:
    """Name the blocked `agent` and when its block lifts, in the zone `until` and `now` carry."""
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


def quota_retry_description(agent: str, until: datetime, now: datetime) -> str:
    """Name the `agent` quota and the engine's retry, in the zone `until` carries."""
    return (
        f"{agent} {QUOTA_WORD}{QUOTA_BANNER_SEPARATOR}{QUOTA_RETRY_PHRASE} "
        f"{quota_reset_clock(until, now)}"
    )


def quota_clock_text(agent: str, until: datetime, now: datetime) -> tuple[str, str]:
    """Return the clock-driven wording of a quota wait: its retry description and its time left."""
    return quota_retry_description(agent, until, now), quota_remaining(until, now)


def engine_retry_note(retry: datetime, windows: Sequence[QuotaWindow], now: datetime) -> str | None:
    """Explain how the engine's retry differs from the provider reset it follows, when it does.

    The engine follows the latest pending reset of the windows reported exhausted, plus its margin.
    A retry on that instant needs no note; any other retry is named against that reset. Without a
    pending exhausted reset, no reset is known to compare with, so nothing is said.
    """
    pending = [
        (window.resets_at, window.name)
        for window in windows
        if is_exhausted(window) and window.resets_at is not None and window.resets_at > now
    ]
    if not pending:
        return None
    latest_reset, window_name = max(pending, key=itemgetter(0))
    if retry == quota_block_until(latest_reset, now):
        return None
    return ENGINE_RETRY_DIFFERS.format(window=quota_window_label(window_name))


def next_quota_reload(now: datetime, instants: Iterable[datetime]) -> datetime:
    """Return the first of `instants` after `now`, the next moment a quota wait's data changes.

    `instants` are the moments the block lifts, its reroute window closes and other blocks lift.
    The wording moves with the clock on its own and never needs this reload.
    """
    return min(instant for instant in instants if instant > now)


class LatestEvents(Protocol):
    """Reads the newest events of one task, in chronological order."""

    def __call__(
        self, limit: int, *, kinds: Sequence[EventKind] | None = None, task_id: str | None = None
    ) -> list[Event]:
        """Return up to `limit` of the newest events matching `kinds` and `task_id`."""
        ...


def current_quota_wait_events(
    task: Task, role: Role, agent: str, latest_events: LatestEvents
) -> tuple[Event, Event] | None:
    """Return the turn failure and quota block that put `task`'s current turn in a quota wait.

    The wait is current only while the task's latest turn lifecycle event is a quota failure of
    its current step, the turn that failed was `role`'s on `agent` in the current iteration, and
    the latest quota event of the task is the block of that agent set after the failure.
    """
    if task.step is None:
        return None
    lifecycle = latest_events(1, kinds=QUOTA_TURN_LIFECYCLE, task_id=task.id)
    if (
        not lifecycle
        or lifecycle[0].kind is not EventKind.TURN_FAILED
        or lifecycle[0].data.get("kind") != FailureKind.QUOTA.value
        or lifecycle[0].data.get("step") != task.step.value
    ):
        return None
    failure = lifecycle[0]
    starts = latest_events(1, kinds=(EventKind.TURN_STARTED,), task_id=task.id)
    if not starts or starts[0].id >= failure.id:
        return None
    started = starts[0]
    if (
        started.data.get("step") != task.step.value
        or started.data.get("iteration") != task.iteration
        or started.data.get("role") != role.value
        or started.data.get("agent") != agent
    ):
        return None
    quota_events = latest_events(
        1, kinds=(EventKind.QUOTA_BLOCKED, EventKind.QUOTA_CLEARED), task_id=task.id
    )
    if (
        not quota_events
        or quota_events[0].kind is not EventKind.QUOTA_BLOCKED
        or quota_events[0].id <= failure.id
        or quota_events[0].data.get("agent") != agent
    ):
        return None
    return failure, quota_events[0]
