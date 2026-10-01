"""Open engine fault streaks derived from `engine_fault` and `engine_recovered` events (05 §2)."""

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
_BACKOFF_FACTOR = 2


class FaultStreak(DomainModel):
    """A fault that is still failing: when it began, was last recorded and how often it failed."""

    scope: str
    task_id: str | None
    error: str
    recoverable: bool
    first_seen: datetime
    last_seen: datetime
    repeat: int


def open_fault_streaks(events: Iterable[Event]) -> list[FaultStreak]:
    """Fold chronological fault and recovery `events` into the streaks that were never recovered.

    A fault without a `scope` is a one-off record, not a streak, and is ignored.
    """
    open_streaks: dict[tuple[str, str | None], FaultStreak] = {}
    for event in events:
        scope = event.data.get(SCOPE_KEY)
        if scope is None:
            continue
        key = (scope, event.task_id)
        if event.kind is EventKind.ENGINE_RECOVERED:
            open_streaks.pop(key, None)
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
            )
    return list(open_streaks.values())


def next_backoff(previous: float, *, initial: float, maximum: float) -> float:
    """Return the retry delay after `previous` (0 before the first failure): doubled, capped."""
    return initial if previous <= 0 else min(previous * _BACKOFF_FACTOR, maximum)
