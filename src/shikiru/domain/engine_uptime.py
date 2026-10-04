"""Pure projections of recorded engine start and stop evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from shikiru.domain.events import Event, EventKind

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

ENGINE_PID_FIELD = "engine_pid"
ENGINE_PROCESS_STARTED_AT_FIELD = "engine_process_started_at"
ENGINE_LIFECYCLE_PAIR_SIZE = 2


@dataclass(frozen=True)
class EngineUptime:
    running_seconds: float | None
    previous_run_seconds: float | None


def project_engine_uptime(
    events: Sequence[Event],
    *,
    now: datetime,
    running: bool,
    identity: tuple[int, float] | None,
) -> EngineUptime:
    """Return live or previous-run duration only when lifecycle evidence is consistent."""
    ordered_events = tuple(sorted(events, key=lambda event: event.id))
    if running:
        return EngineUptime(
            running_seconds=_running_duration(ordered_events, now, identity),
            previous_run_seconds=None,
        )
    return EngineUptime(
        running_seconds=None,
        previous_run_seconds=_previous_duration(ordered_events, now),
    )


def _running_duration(
    events: Sequence[Event], now: datetime, identity: tuple[int, float] | None
) -> float | None:
    if identity is None or not events:
        return None
    latest = events[-1]
    if (
        latest.kind is not EventKind.ENGINE_STARTED
        or _event_identity(latest) != identity
        or latest.ts > now
    ):
        return None
    seconds = (now - latest.ts).total_seconds()
    return seconds if seconds >= 0 else None


def _previous_duration(events: Sequence[Event], now: datetime) -> float | None:
    if len(events) < ENGINE_LIFECYCLE_PAIR_SIZE:
        return None
    started, stopped = events[len(events) - ENGINE_LIFECYCLE_PAIR_SIZE :]
    if (
        started.kind is not EventKind.ENGINE_STARTED
        or stopped.kind is not EventKind.ENGINE_STOPPED
        or _event_identity(started) is None
        or _event_identity(started) != _event_identity(stopped)
        or started.ts > stopped.ts
        or stopped.ts > now
    ):
        return None
    return (stopped.ts - started.ts).total_seconds()


def _event_identity(event: Event) -> tuple[int, float] | None:
    pid = event.data.get(ENGINE_PID_FIELD)
    started_at = event.data.get(ENGINE_PROCESS_STARTED_AT_FIELD)
    if (
        isinstance(pid, bool)
        or not isinstance(pid, int)
        or isinstance(started_at, bool)
        or not isinstance(started_at, int | float)
    ):
        return None
    return pid, float(started_at)
