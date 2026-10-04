from __future__ import annotations

from datetime import UTC, datetime

import pytest

from shikiru.domain.events import Event, EventKind
from shikiru.domain.reports import DECISION_CHANGES_REQUESTED, ImplementationReport, ReportKind
from shikiru.domain.tasks import Mode, Step
from shikiru.domain.workflow import (
    WORKFLOW_DECISION_KEY,
    WORKFLOW_ITERATION_KEY,
    WORKFLOW_STEP_KEY,
    WORKFLOW_TURN_KEY,
    WorkflowMilestoneState,
    WorkflowProgress,
    WorkflowReport,
    project_workflow,
)
from tests.support.factories import make_task

_START = datetime(2026, 1, 1, tzinfo=UTC)
_FIRST_EVENT_ID = 1
_SECOND_EVENT_ID = 2
_THIRD_EVENT_ID = 3
_FOURTH_EVENT_ID = 4
_CURRENT_ITERATION = 2
_PREVIOUS_ITERATION = 1


def _event(
    event_id: int,
    kind: EventKind,
    *,
    step: Step | None = None,
    iteration: int | None = None,
    turn: str | None = None,
    decision: str | None = None,
) -> Event:
    data: dict[str, object] = {}
    if step is not None:
        data[WORKFLOW_STEP_KEY] = step.value
    if iteration is not None:
        data[WORKFLOW_ITERATION_KEY] = iteration
    if turn is not None:
        data[WORKFLOW_TURN_KEY] = turn
    if decision is not None:
        data[WORKFLOW_DECISION_KEY] = decision
    return Event(
        id=event_id,
        ts=_START,
        task_id="T-1",
        kind=kind,
        message=kind.value,
        data=data,
    )


def _stage_states(progress: WorkflowProgress) -> dict[Step, WorkflowMilestoneState]:
    return {item.stage: item.state for item in progress.milestones}


@pytest.mark.parametrize(
    ("mode", "step", "events", "expected"),
    [
        pytest.param(
            Mode.PLAN,
            Step.PLAN,
            (),
            (Step.TRIAGE, Step.PLAN, Step.INTEGRATE),
            id="planning-workflow",
        ),
        pytest.param(
            Mode.IMPLEMENT,
            Step.IMPLEMENT,
            (),
            (Step.TRIAGE, Step.IMPLEMENT, Step.VERIFY, Step.INTEGRATE),
            id="implementation-workflow",
        ),
        pytest.param(
            Mode.IMPLEMENT,
            Step.CONSULT,
            (
                _event(
                    _FIRST_EVENT_ID,
                    EventKind.STEP_STARTED,
                    step=Step.CONSULT,
                    iteration=_PREVIOUS_ITERATION,
                ),
            ),
            (Step.TRIAGE, Step.IMPLEMENT, Step.CONSULT, Step.VERIFY, Step.INTEGRATE),
            id="consult-workflow",
        ),
    ],
)
def test_workflow_stages_follow_task_mode_and_recorded_consult(
    mode: Mode,
    step: Step,
    events: tuple[Event, ...],
    expected: tuple[Step, ...],
) -> None:
    progress = project_workflow(
        make_task(mode=mode, step=step),
        events,
        (),
        event_window_complete=False,
    )

    assert tuple(item.stage for item in progress.milestones) == expected


@pytest.mark.parametrize(
    ("events", "event_window_complete", "expected"),
    [
        pytest.param(
            (_event(_FIRST_EVENT_ID, EventKind.TASK_QUEUED),),
            True,
            True,
            id="retained-origin",
        ),
        pytest.param(
            (_event(_FIRST_EVENT_ID, EventKind.TASK_QUEUED),),
            False,
            False,
            id="truncated",
        ),
        pytest.param(
            (_event(_FIRST_EVENT_ID, EventKind.STEP_STARTED, step=Step.IMPLEMENT),),
            True,
            False,
            id="missing-origin",
        ),
        pytest.param((), True, False, id="empty-history"),
    ],
)
def test_history_is_complete_only_with_retained_queue_origin(
    events: tuple[Event, ...],
    event_window_complete: bool,
    expected: bool,
) -> None:
    progress = project_workflow(
        make_task(mode=Mode.IMPLEMENT, step=Step.IMPLEMENT),
        events,
        (),
        event_window_complete=event_window_complete,
    )

    assert progress.history_complete is expected


def test_prior_iteration_report_does_not_complete_reentered_stage() -> None:
    events = (
        _event(_FIRST_EVENT_ID, EventKind.TASK_QUEUED),
        _event(
            _SECOND_EVENT_ID,
            EventKind.STEP_STARTED,
            step=Step.IMPLEMENT,
            iteration=_PREVIOUS_ITERATION,
        ),
        _event(
            _THIRD_EVENT_ID,
            EventKind.TURN_STARTED,
            step=Step.IMPLEMENT,
            iteration=_PREVIOUS_ITERATION,
            turn="implementation-i1",
        ),
        _event(
            _FOURTH_EVENT_ID,
            EventKind.STEP_STARTED,
            step=Step.IMPLEMENT,
            iteration=_CURRENT_ITERATION,
        ),
    )
    report = ImplementationReport(
        status="completed",
        summary="Previous iteration completed.",
        commits=[],
        tests=[],
        trackers_updated=True,
    )
    progress = project_workflow(
        make_task(mode=Mode.IMPLEMENT, step=Step.VERIFY, iteration=_CURRENT_ITERATION),
        events,
        (
            WorkflowReport(
                turn="implementation-i1",
                kind=ReportKind.IMPLEMENTATION,
                data=report,
            ),
        ),
        event_window_complete=True,
    )

    assert _stage_states(progress)[Step.IMPLEMENT] is WorkflowMilestoneState.UNKNOWN
    assert _stage_states(progress)[Step.VERIFY] is WorkflowMilestoneState.CURRENT


def test_changes_requested_verdict_marks_verify_rework_after_its_entry() -> None:
    events = (
        _event(_FIRST_EVENT_ID, EventKind.TASK_QUEUED),
        _event(
            _SECOND_EVENT_ID,
            EventKind.STEP_STARTED,
            step=Step.IMPLEMENT,
            iteration=_PREVIOUS_ITERATION,
        ),
        _event(
            _THIRD_EVENT_ID,
            EventKind.STEP_STARTED,
            step=Step.VERIFY,
            iteration=_PREVIOUS_ITERATION,
        ),
        _event(
            _FOURTH_EVENT_ID,
            EventKind.VERDICT,
            decision=DECISION_CHANGES_REQUESTED,
        ),
    )
    progress = project_workflow(
        make_task(mode=Mode.IMPLEMENT, step=Step.IMPLEMENT, iteration=_CURRENT_ITERATION),
        events,
        (),
        event_window_complete=True,
    )

    assert _stage_states(progress)[Step.IMPLEMENT] is WorkflowMilestoneState.CURRENT
    assert _stage_states(progress)[Step.VERIFY] is WorkflowMilestoneState.REWORK
