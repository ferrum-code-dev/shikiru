"""Scheduling rules for pushing locally integrated base branches."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True, slots=True)
class PushTarget:
    """A base branch with integrations to publish and the scheduling facts of its pushes."""

    base: str
    registered: int
    pushed: int
    due_at: datetime | None
    last_pushed_at: datetime | None

    @property
    def pending(self) -> bool:
        """Return whether an integration was registered that no push has acknowledged."""
        return self.registered > self.pushed


def pending_deadline(*, last_pushed_at: datetime | None, now: datetime, interval: int) -> datetime:
    """Return when work that becomes pending at `now` is due, given `interval` seconds.

    Without an earlier push the interval starts now; after one, the work is due an interval
    after it, or at once when that moment has passed. Zero means due at once.
    """
    if interval == 0:
        return now
    if last_pushed_at is None:
        return now + timedelta(seconds=interval)
    return max(last_pushed_at + timedelta(seconds=interval), now)


def is_due(target: PushTarget, *, now: datetime, interval: int) -> bool:
    """Return whether `target` has pending work whose deadline has come."""
    if not target.pending or target.due_at is None:
        return False
    return interval == 0 or now >= target.due_at
