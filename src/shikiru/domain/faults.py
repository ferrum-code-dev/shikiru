"""Open engine fault streaks derived from fault, recovery and dismissal events."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from shikiru.domain.events import Event, EventKind
from shikiru.domain.tasks import DomainModel

if TYPE_CHECKING:
    from collections.abc import Iterable

SCOPE_KEY = "scope"
ERROR_KEY = "error"
RECOVERABLE_KEY = "recoverable"
REPEAT_KEY = "repeat"
DETAIL_KEY = "detail"
COMMAND_KEY = "command"
EXIT_CODE_KEY = "exit_code"
STDERR_KEY = "stderr"
TRACEBACK_KEY = "traceback"
FAULT_EVENT_ID_KEY = "fault_event_id"
_BACKOFF_FACTOR = 2
FAULT_EVENT_KINDS = (
    EventKind.ENGINE_FAULT,
    EventKind.ENGINE_RECOVERED,
    EventKind.ENGINE_FAULT_DISMISSED,
)


class FaultStreak(DomainModel):
    """A fault that is still failing: when it began, was last recorded and how often it failed."""

    scope: str
    task_id: str | None
    error: str
    recoverable: bool
    first_seen: datetime
    last_seen: datetime
    repeat: int
    event_id: int
    message: str
    detail: str | None = None
    command: tuple[str, ...] | None = None
    exit_code: int | None = None
    stderr: str | None = None
    traceback: str | None = None


def open_fault_streaks(events: Iterable[Event]) -> list[FaultStreak]:
    """Fold fault, recovery and dismissal `events` into the still-open streaks.

    A fault without a `scope` is a one-off record, not a streak, and is ignored. A streak keeps
    the evidence of its latest record; evidence that record did not carry stays `None`. A dismissal
    closes only the exact fault occurrence it names, preserving later occurrences of that streak.
    """
    open_streaks: dict[tuple[str, str | None], FaultStreak] = {}
    for event in events:
        scope = event.data.get(SCOPE_KEY)
        if scope is None:
            continue
        key = (scope, event.task_id)
        if event.kind is EventKind.ENGINE_RECOVERED:
            open_streaks.pop(key, None)
        elif event.kind is EventKind.ENGINE_FAULT_DISMISSED:
            previous = open_streaks.get(key)
            if previous is not None and event.data.get(FAULT_EVENT_ID_KEY) == previous.event_id:
                open_streaks.pop(key)
        elif event.kind is EventKind.ENGINE_FAULT:
            previous = open_streaks.get(key)
            open_streaks[key] = FaultStreak(
                scope=scope,
                task_id=event.task_id,
                error=event.data[ERROR_KEY],
                recoverable=event.data[RECOVERABLE_KEY],
                first_seen=previous.first_seen if previous else event.ts,
                last_seen=event.ts,
                repeat=(previous.repeat if previous else 0) + event.data[REPEAT_KEY],
                event_id=event.id,
                message=event.message,
                detail=event.data.get(DETAIL_KEY),
                command=_command(event.data.get(COMMAND_KEY)),
                exit_code=event.data.get(EXIT_CODE_KEY),
                stderr=event.data.get(STDERR_KEY),
                traceback=event.data.get(TRACEBACK_KEY),
            )
    return list(open_streaks.values())


def _command(recorded: list[str] | None) -> tuple[str, ...] | None:
    return None if recorded is None else tuple(recorded)


def next_backoff(previous: float, *, initial: float, maximum: float) -> float:
    """Return the retry delay after `previous` (0 before the first failure): doubled, capped."""
    return initial if previous <= 0 else min(previous * _BACKOFF_FACTOR, maximum)
