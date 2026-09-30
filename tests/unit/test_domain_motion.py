from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from shikiru.config.settings import TimeoutsConfig
from shikiru.domain.motion import (
    MotionStamp,
    MotionStampKind,
    MotionState,
    QuotaMotionState,
    TaskMotionState,
    frame,
)
from tests.support.clock import FakeClock
from tests.support.factories import make_settings

_START = datetime(2026, 1, 1, tzinfo=UTC)
_TIMEOUTS = TimeoutsConfig(
    stall=3, tool=5, max_quota_wait=60, integration_wait=30, pr_wait=60, helper=10
)


def _state(
    *,
    animations: bool = True,
    tasks: tuple[TaskMotionState, ...] = (),
    quotas: tuple[QuotaMotionState, ...] = (),
    escalations_open: bool = False,
) -> MotionState:
    return MotionState(
        animations=animations,
        timeouts=_TIMEOUTS,
        refresh_hz=4,
        stream_active_seconds=10,
        stamp_flash_seconds=0.4,
        escalation_pulse_hz=1,
        tasks=tasks,
        quotas=quotas,
        escalations_open=escalations_open,
    )


@pytest.mark.parametrize(
    ("elapsed", "tool_running", "held", "stalled"),
    [
        (2, False, False, False),
        (3, False, False, True),
        (4, True, False, False),
        (5, True, False, True),
        (8, False, True, False),
    ],
    ids=["working", "stall-timeout", "running-tool", "tool-timeout", "held"],
)
def test_stream_state_uses_activity_kind_and_does_not_spin_while_held(
    elapsed: int, tool_running: bool, held: bool, stalled: bool
) -> None:
    clock = FakeClock(_START + timedelta(seconds=elapsed))
    task = TaskMotionState(
        "T-1",
        _START,
        last_signal_at=_START,
        tool_running=tool_running,
        turn_active=True,
        held=held,
    )

    [result] = frame(_state(tasks=(task,)), clock.now()).tasks

    assert result.stalled is stalled
    assert result.spinner_visible is (not held and not stalled)


def test_spinner_freezes_on_the_last_active_tick_after_the_stream_window() -> None:
    clock = FakeClock(_START)
    task = TaskMotionState("T-1", _START, last_signal_at=_START, turn_active=True)
    state = _state(tasks=(task,))
    state = MotionState(
        animations=state.animations,
        timeouts=TimeoutsConfig(
            stall=30, tool=60, max_quota_wait=60, integration_wait=30, pr_wait=60, helper=10
        ),
        refresh_hz=state.refresh_hz,
        stream_active_seconds=state.stream_active_seconds,
        stamp_flash_seconds=state.stamp_flash_seconds,
        escalation_pulse_hz=state.escalation_pulse_hz,
        tasks=state.tasks,
    )
    clock.advance(9.75)
    [last_active] = frame(state, clock.now()).tasks

    clock.advance(0.25)
    [frozen] = frame(state, clock.now()).tasks
    clock.advance(3)
    [still_frozen] = frame(state, clock.now()).tasks

    assert frozen.spinner_phase == last_active.spinner_phase
    assert still_frozen.spinner_phase == frozen.spinner_phase
    assert frozen.spinner_visible


def test_static_frames_hold_phase_and_hide_outcome_flashes() -> None:
    clock = FakeClock(_START)
    task = TaskMotionState(
        "T-1",
        _START,
        last_signal_at=_START,
        turn_active=True,
        stamp=MotionStamp(_START, MotionStampKind.VERDICT, "accept"),
    )

    state = _state(animations=False, tasks=(task,))
    [initial] = frame(state, clock.now()).tasks
    clock.advance(11)
    [result] = frame(
        MotionState(
            animations=state.animations,
            timeouts=state.timeouts,
            refresh_hz=state.refresh_hz,
            stream_active_seconds=state.stream_active_seconds,
            stamp_flash_seconds=state.stamp_flash_seconds,
            escalation_pulse_hz=state.escalation_pulse_hz,
            tasks=(
                TaskMotionState(
                    "T-1",
                    _START,
                    last_signal_at=clock.now(),
                    turn_active=True,
                    stamp=MotionStamp(_START, MotionStampKind.VERDICT, "accept"),
                ),
            ),
        ),
        clock.now(),
    ).tasks

    assert initial.spinner_phase == result.spinner_phase
    assert result.spinner_phase == 0
    assert result.spinner_visible
    assert not result.stamp_flash


def test_animated_spinner_phase_is_capped_at_four_hertz() -> None:
    clock = FakeClock(_START)
    task = TaskMotionState("T-1", _START, last_signal_at=_START, turn_active=True)
    state = _state(tasks=(task,))
    uncapped = MotionState(
        animations=state.animations,
        timeouts=state.timeouts,
        refresh_hz=90,
        stream_active_seconds=state.stream_active_seconds,
        stamp_flash_seconds=state.stamp_flash_seconds,
        escalation_pulse_hz=state.escalation_pulse_hz,
        tasks=state.tasks,
    )
    [initial] = frame(uncapped, clock.now()).tasks

    clock.advance(0.25)
    [advanced] = frame(uncapped, clock.now()).tasks

    assert advanced.spinner_phase - initial.spinner_phase == 1


@pytest.mark.parametrize(
    ("age", "flashes"),
    [(0, True), (0.399, True), (0.4, False), (0.401, False)],
    ids=["just-landed", "within-window", "at-window-end", "after-window"],
)
def test_stamp_flash_has_a_four_hundred_millisecond_window(age: float, flashes: bool) -> None:
    clock = FakeClock(_START + timedelta(seconds=age))
    task = TaskMotionState("T-1", _START, stamp=MotionStamp(_START, MotionStampKind.MERGED))

    [result] = frame(_state(tasks=(task,)), clock.now()).tasks

    assert result.stamp_flash is flashes
    assert (result.stamp_kind is MotionStampKind.MERGED) is flashes


@pytest.mark.parametrize(
    ("elapsed", "red"),
    [(0, True), (0.5, False), (1, True), (1.5, False)],
    ids=["red", "dim", "red-again", "dim-again"],
)
def test_escalation_pulse_alternates_at_one_hertz(elapsed: float, red: bool) -> None:
    clock = FakeClock(_START + timedelta(seconds=elapsed))

    result = frame(_state(escalations_open=True), clock.now())

    assert result.escalation_red is red


def test_elapsed_time_and_quota_countdown_advance_from_the_injected_clock() -> None:
    clock = FakeClock(_START + timedelta(seconds=61.2))
    task = TaskMotionState("T-1", _START)
    quota = QuotaMotionState("codex", "weekly", _START + timedelta(seconds=70))

    result = frame(_state(tasks=(task,), quotas=(quota,)), clock.now())

    assert result.tasks[0].elapsed_seconds == 61
    assert result.quotas[0].remaining_seconds == 9


def test_non_stream_activity_does_not_show_a_spinner() -> None:
    settings = make_settings()
    clock = FakeClock(_START)
    task = TaskMotionState("T-1", _START, turn_started_at=_START, turn_active=True)

    [result] = frame(
        MotionState(
            animations=True,
            timeouts=settings.orchestration.timeouts,
            refresh_hz=4,
            stream_active_seconds=10,
            stamp_flash_seconds=0.4,
            escalation_pulse_hz=1,
            tasks=(task,),
        ),
        clock.now(),
    ).tasks

    assert not result.spinner_visible
