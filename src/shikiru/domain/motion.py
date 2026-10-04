"""Pure dashboard timing rules for live task and quota views (06 §6.5)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, auto
from math import ceil
from typing import TYPE_CHECKING

from shikiru.config.settings import MAX_MOTION_REFRESH_HZ
from shikiru.domain.activity import is_stalled

if TYPE_CHECKING:
    from datetime import datetime

    from shikiru.config.settings import TimeoutsConfig

PULSE_PHASES = 2


class MotionStampKind(StrEnum):
    """A task outcome that briefly stamps its comic strip."""

    VERDICT = auto()
    MERGED = auto()


@dataclass(frozen=True)
class MotionStamp:
    """A verdict or merge event that may briefly stamp its task strip."""

    at: datetime
    kind: MotionStampKind
    detail: str = ""


@dataclass(frozen=True)
class TaskMotionState:
    """Timing evidence for the task's current panel and most recent outcome."""

    task_id: str
    step_started_at: datetime
    actual_step_started_at: datetime | None = None
    turn_started_at: datetime | None = None
    last_signal_at: datetime | None = None
    last_signal: str | None = None
    last_signal_identity: str | None = None
    last_signal_role: str | None = None
    last_signal_turn: str | None = None
    tool_running: bool = False
    turn_active: bool = False
    held: bool = False
    ended_at: datetime | None = None
    stamp: MotionStamp | None = None


@dataclass(frozen=True)
class QuotaMotionState:
    """One quota reset deadline rendered as a live countdown."""

    agent: str
    window: str
    resets_at: datetime | None


@dataclass(frozen=True)
class MotionState:
    """The immutable inputs needed to render one dashboard frame."""

    animations: bool
    timeouts: TimeoutsConfig
    refresh_hz: int
    stream_active_seconds: float
    stamp_flash_seconds: float
    escalation_pulse_hz: int
    tasks: tuple[TaskMotionState, ...] = ()
    quotas: tuple[QuotaMotionState, ...] = ()
    escalations_open: bool = False


@dataclass(frozen=True)
class TaskMotionFrame:
    """Timing output for one task strip, without presentation glyphs or colours."""

    task_id: str
    spinner_phase: int
    spinner_visible: bool
    stalled: bool
    elapsed_seconds: int
    actual_step_started_at: datetime | None
    last_signal_at: datetime | None
    last_signal: str | None
    last_signal_identity: str | None
    last_signal_role: str | None
    last_signal_turn: str | None
    stamp_kind: MotionStampKind | None
    stamp_detail: str
    stamp_flash: bool


@dataclass(frozen=True)
class QuotaMotionFrame:
    """Remaining seconds for one provider-reported quota reset."""

    agent: str
    window: str
    remaining_seconds: int | None


@dataclass(frozen=True)
class MotionFrame:
    """All time-derived values for a dashboard render at `now`."""

    tasks: tuple[TaskMotionFrame, ...]
    quotas: tuple[QuotaMotionFrame, ...]
    escalation_red: bool


def frame(state: MotionState, now: datetime) -> MotionFrame:
    """Calculate stream motion, outcome flashes, escalation pulse and live counters."""
    return MotionFrame(
        tasks=tuple(_task_frame(task, state, now) for task in state.tasks),
        quotas=tuple(_quota_frame(quota, now) for quota in state.quotas),
        escalation_red=(
            state.escalations_open
            and (
                not state.animations
                or int(now.timestamp() * state.escalation_pulse_hz * PULSE_PHASES) % PULSE_PHASES
                == 0
            )
        ),
    )


def _task_frame(task: TaskMotionState, state: MotionState, now: datetime) -> TaskMotionFrame:
    activity_at = task.last_signal_at or task.turn_started_at
    stalled = (
        task.turn_active
        and activity_at is not None
        and not task.held
        and is_stalled(activity_at, task.tool_running, now, state.timeouts)
    )
    spinner_phase = 0
    if activity_at is not None:
        age = max((now - activity_at).total_seconds(), 0.0)
        if not state.animations:
            spinner_phase = 0
        elif task.turn_active and not task.held and not stalled:
            if task.last_signal_at is not None and age < state.stream_active_seconds:
                spinner_phase = int(now.timestamp() * min(state.refresh_hz, MAX_MOTION_REFRESH_HZ))
            elif task.last_signal_at is not None:
                spinner_phase = int(
                    ceil(
                        (activity_at.timestamp() + state.stream_active_seconds)
                        * min(state.refresh_hz, MAX_MOTION_REFRESH_HZ)
                    )
                    - 1
                )
    elapsed_end = task.ended_at or now
    elapsed_seconds = max(int((elapsed_end - task.step_started_at).total_seconds()), 0)
    stamp_kind = None
    stamp_detail = ""
    stamp_flash = False
    if state.animations and task.stamp is not None:
        age = (now - task.stamp.at).total_seconds()
        if 0 <= age < state.stamp_flash_seconds:
            stamp_kind = task.stamp.kind
            stamp_detail = task.stamp.detail
            stamp_flash = True
    return TaskMotionFrame(
        task_id=task.task_id,
        spinner_phase=spinner_phase,
        spinner_visible=(
            task.turn_active and not task.held and not stalled and task.last_signal_at is not None
        ),
        stalled=stalled,
        elapsed_seconds=elapsed_seconds,
        actual_step_started_at=task.actual_step_started_at,
        last_signal_at=task.last_signal_at,
        last_signal=task.last_signal,
        last_signal_identity=task.last_signal_identity,
        last_signal_role=task.last_signal_role,
        last_signal_turn=task.last_signal_turn,
        stamp_kind=stamp_kind,
        stamp_detail=stamp_detail,
        stamp_flash=stamp_flash,
    )


def _quota_frame(quota: QuotaMotionState, now: datetime) -> QuotaMotionFrame:
    remaining = (
        max(ceil((quota.resets_at - now).total_seconds()), 0)
        if quota.resets_at is not None
        else None
    )
    return QuotaMotionFrame(
        agent=quota.agent,
        window=quota.window,
        remaining_seconds=remaining,
    )
