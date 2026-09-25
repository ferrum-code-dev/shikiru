"""`EventKind`, the closed set of event kinds written to `events` (docs/design/05-data.md §2)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum, auto
from typing import Any

from pydantic import Field

from shikiru.domain.tasks import DomainModel


class EventKind(StrEnum):
    """Every event kind the engine can emit."""

    TASK_QUEUED = auto()
    TASK_STARTED = auto()
    DISPATCH_FAILED = auto()
    STEP_STARTED = auto()
    TURN_STARTED = auto()
    TURN_COMPLETED = auto()
    TURN_FAILED = auto()
    NUDGE = auto()
    HELPER_ACTION = auto()
    RUNG_CHANGED = auto()
    COMPACTED = auto()
    QUOTA_BLOCKED = auto()
    QUOTA_CLEARED = auto()
    VERDICT = auto()
    SCOPE_CHANGE = auto()
    HOLD_SET = auto()
    HOLD_CLEARED = auto()
    INTERVENTION = auto()
    ESCALATION = auto()
    ESCALATION_RESOLVED = auto()
    INTEGRATION_STARTED = auto()
    PR_OPENED = auto()
    TASK_DONE = auto()
    TASK_CANCELLED = auto()
    PANE_RESTORED = auto()
    ENGINE_STARTED = auto()
    ENGINE_STOPPED = auto()


class Event(DomainModel):
    """One recorded event (mirrors the `events` table, 05 §2)."""

    id: int
    ts: datetime
    task_id: str | None = None
    kind: EventKind
    message: str
    data: dict[str, Any] = Field(default_factory=dict)
