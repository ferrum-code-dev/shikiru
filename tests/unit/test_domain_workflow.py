from __future__ import annotations

from datetime import UTC, datetime

import pytest

from shikiru.domain.events import Event, EventKind
from shikiru.domain.reports import (
    DECISION_ACCEPT,
    DECISION_CHANGES_REQUESTED,
    ImplementationReport,
    ReportKind,
    Verdict,
)
from shikiru.domain.tasks import Mode, Status, Step
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
_FIFTH_EVENT_ID = 5
_SIXTH_EVENT_ID = 6
_SEVENTH_EVENT_ID = 7
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
    ("mode", "step", "status", "iteration", "events", "expected", "before"),
    [
        pytest.param(
            Mode.PLAN,
            Step.PLAN,
            Status.RUNNING,
            0,
            (),
            {Step.PLAN: WorkflowMilestoneState.CURRENT},
            (),
            id="planning-workflow",
        ),
        pytest.param(
            Mode.IMPLEMENT,
            Step.IMPLEMENT,
            Status.RUNNING,
            _CURRENT_ITERATION,
            (),
            {Step.IMPLEMENT: WorkflowMilestoneState.CURRENT},
            (),
            id="implementation-workflow",
        ),
        pytest.param(
            Mode.IMPLEMENT,
            Step.CONSULT,
            Status.RUNNING,
            _CURRENT_ITERATION,
            (
                _event(
                    _FIRST_EVENT_ID,
                    EventKind.STEP_STARTED,
                    step=Step.IMPLEMENT,
                    iteration=_CURRENT_ITERATION,
                ),
                _event(
                    _SECOND_EVENT_ID,
                    EventKind.TURN_STARTED,
                    step=Step.IMPLEMENT,
                    iteration=_CURRENT_ITERATION,
                    turn="t1",
                ),
                _event(
                    _THIRD_EVENT_ID,
                    EventKind.STEP_STARTED,
                    step=Step.CONSULT,
                    iteration=_CURRENT_ITERATION,
                ),
            ),
            {
                Step.IMPLEMENT: WorkflowMilestoneState.DONE,
                Step.CONSULT: WorkflowMilestoneState.CURRENT,
            },
            ((Step.IMPLEMENT, Step.CONSULT),),
            id="implement-ended-by-consult-is-done",
        ),
        pytest.param(
            Mode.PLAN,
            Step.CONSULT,
            Status.INTEGRATING,
            0,
            (),
            {
                Step.TRIAGE: WorkflowMilestoneState.UNKNOWN,
                Step.PLAN: WorkflowMilestoneState.UNKNOWN,
                Step.INTEGRATE: WorkflowMilestoneState.UNKNOWN,
                Step.CONSULT: WorkflowMilestoneState.CURRENT,
            },
            ((Step.INTEGRATE, Step.CONSULT),),
            id="plan-integration-consult-with-missing-history",
        ),
        pytest.param(
            Mode.PLAN,
            Step.CONSULT,
            Status.ESCALATED,
            0,
            (),
            {
                Step.INTEGRATE: WorkflowMilestoneState.UNKNOWN,
                Step.CONSULT: WorkflowMilestoneState.CURRENT,
            },
            ((Step.INTEGRATE, Step.CONSULT),),
            id="plan-escalation-retains-integration-consult",
        ),
        pytest.param(
            Mode.PLAN,
            Step.CONSULT,
            Status.QUEUED,
            0,
            (),
            {
                Step.INTEGRATE: WorkflowMilestoneState.UNKNOWN,
                Step.CONSULT: WorkflowMilestoneState.CURRENT,
            },
            ((Step.INTEGRATE, Step.CONSULT),),
            id="plan-queue-retains-integration-consult",
        ),
        pytest.param(
            Mode.PLAN,
            Step.VERIFY,
            Status.INTEGRATING,
            0,
            (),
            {
                Step.INTEGRATE: WorkflowMilestoneState.UNKNOWN,
                Step.VERIFY: WorkflowMilestoneState.CURRENT,
            },
            ((Step.INTEGRATE, Step.VERIFY),),
            id="plan-current-verify-is-retained",
        ),
        pytest.param(
            Mode.PLAN,
            Step.INTEGRATE,
            Status.INTEGRATING,
            0,
            (
                _event(
                    _FIRST_EVENT_ID,
                    EventKind.STEP_STARTED,
                    step=Step.VERIFY,
                    iteration=0,
                ),
            ),
            {
                Step.INTEGRATE: WorkflowMilestoneState.CURRENT,
                Step.VERIFY: WorkflowMilestoneState.UNKNOWN,
            },
            ((Step.INTEGRATE, Step.VERIFY),),
            id="plan-history-retains-actual-verify-entry",
        ),
        pytest.param(
            Mode.IMPLEMENT,
            Step.CONSULT,
            Status.INTEGRATING,
            _CURRENT_ITERATION,
            (
                _event(
                    _FIRST_EVENT_ID,
                    EventKind.STEP_STARTED,
                    step=Step.IMPLEMENT,
                    iteration=_CURRENT_ITERATION,
                ),
                _event(
                    _SECOND_EVENT_ID,
                    EventKind.STEP_STARTED,
                    step=Step.VERIFY,
                    iteration=_CURRENT_ITERATION,
                ),
                _event(
                    _THIRD_EVENT_ID,
                    EventKind.STEP_STARTED,
                    step=Step.INTEGRATE,
                    iteration=_CURRENT_ITERATION,
                ),
                _event(
                    _FOURTH_EVENT_ID,
                    EventKind.STEP_STARTED,
                    step=Step.CONSULT,
                    iteration=_CURRENT_ITERATION,
                ),
            ),
            {
                Step.IMPLEMENT: WorkflowMilestoneState.DONE,
                Step.VERIFY: WorkflowMilestoneState.DONE,
                Step.INTEGRATE: WorkflowMilestoneState.UNKNOWN,
                Step.CONSULT: WorkflowMilestoneState.CURRENT,
            },
            ((Step.INTEGRATE, Step.CONSULT),),
            id="integration-consult-follows-verify-and-merge",
        ),
        pytest.param(
            Mode.IMPLEMENT,
            Step.CONSULT,
            Status.ESCALATED,
            _CURRENT_ITERATION,
            (
                _event(
                    _FIRST_EVENT_ID,
                    EventKind.STEP_STARTED,
                    step=Step.IMPLEMENT,
                    iteration=_CURRENT_ITERATION,
                ),
                _event(
                    _SECOND_EVENT_ID,
                    EventKind.STEP_STARTED,
                    step=Step.VERIFY,
                    iteration=_CURRENT_ITERATION,
                ),
                _event(
                    _THIRD_EVENT_ID,
                    EventKind.STEP_STARTED,
                    step=Step.INTEGRATE,
                    iteration=_CURRENT_ITERATION,
                ),
                _event(
                    _FOURTH_EVENT_ID,
                    EventKind.STEP_STARTED,
                    step=Step.CONSULT,
                    iteration=_CURRENT_ITERATION,
                ),
            ),
            {
                Step.IMPLEMENT: WorkflowMilestoneState.DONE,
                Step.VERIFY: WorkflowMilestoneState.DONE,
                Step.INTEGRATE: WorkflowMilestoneState.UNKNOWN,
                Step.CONSULT: WorkflowMilestoneState.CURRENT,
            },
            ((Step.INTEGRATE, Step.CONSULT),),
            id="retained-integration-context-survives-escalation",
        ),
    ],
)
def test_workflow_stages_follow_task_mode_and_recorded_consult(
    *,
    mode: Mode,
    step: Step,
    status: Status,
    iteration: int,
    events: tuple[Event, ...],
    expected: dict[Step, WorkflowMilestoneState],
    before: tuple[tuple[Step, Step], ...],
) -> None:
    progress = project_workflow(
        make_task(mode=mode, step=step, status=status, iteration=iteration),
        events,
        (),
        event_window_complete=False,
    )
    states = _stage_states(progress)

    for stage, state in expected.items():
        assert states[stage] is state
    positions = {item.stage: index for index, item in enumerate(progress.milestones)}
    for earlier, later in before:
        assert positions[earlier] < positions[later]


def test_plan_integration_verdict_does_not_invent_a_verify_stage() -> None:
    task = make_task(mode=Mode.PLAN, step=Step.INTEGRATE, status=Status.INTEGRATING)
    events = (
        _event(
            _FIRST_EVENT_ID,
            EventKind.STEP_STARTED,
            step=Step.INTEGRATE,
            iteration=task.iteration,
        ),
    )
    report = WorkflowReport(
        turn="post-rebase-verify",
        kind=ReportKind.VERDICT,
        data=Verdict(
            decision=DECISION_ACCEPT,
            summary="Conflicts verified.",
            commit_subject="fix: resolve conflicts",
        ),
    )

    progress = project_workflow(task, events, (report,), event_window_complete=False)
    states = _stage_states(progress)

    assert states[Step.INTEGRATE] is WorkflowMilestoneState.CURRENT
    assert Step.VERIFY not in states


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


@pytest.mark.parametrize(
    ("current_turn_started", "report_turn", "expected"),
    [
        pytest.param(False, "implement-before-reentry", WorkflowMilestoneState.UNKNOWN, id="old"),
        pytest.param(True, "implement-after-reentry", WorkflowMilestoneState.DONE, id="current"),
    ],
)
def test_report_completion_is_bound_to_the_latest_same_iteration_stage_entry(
    current_turn_started: bool,
    report_turn: str,
    expected: WorkflowMilestoneState,
) -> None:
    events = [
        _event(_FIRST_EVENT_ID, EventKind.TASK_QUEUED),
        _event(
            _SECOND_EVENT_ID,
            EventKind.STEP_STARTED,
            step=Step.IMPLEMENT,
            iteration=_CURRENT_ITERATION,
        ),
        _event(
            _THIRD_EVENT_ID,
            EventKind.TURN_STARTED,
            step=Step.IMPLEMENT,
            iteration=_CURRENT_ITERATION,
            turn="implement-before-reentry",
        ),
        _event(
            _FOURTH_EVENT_ID,
            EventKind.STEP_STARTED,
            step=Step.CONSULT,
            iteration=_CURRENT_ITERATION,
        ),
        _event(
            _FIFTH_EVENT_ID,
            EventKind.STEP_STARTED,
            step=Step.IMPLEMENT,
            iteration=_CURRENT_ITERATION,
        ),
    ]
    if current_turn_started:
        events.append(
            _event(
                _SIXTH_EVENT_ID,
                EventKind.TURN_STARTED,
                step=Step.IMPLEMENT,
                iteration=_CURRENT_ITERATION,
                turn="implement-after-reentry",
            )
        )
    events.append(
        _event(
            _SEVENTH_EVENT_ID,
            EventKind.STEP_STARTED,
            step=Step.INTEGRATE,
            iteration=_CURRENT_ITERATION,
        )
    )
    report = ImplementationReport(
        status="completed",
        summary="Recorded implementation report.",
        commits=[],
        tests=[],
        trackers_updated=True,
    )
    progress = project_workflow(
        make_task(mode=Mode.IMPLEMENT, step=Step.INTEGRATE, iteration=_CURRENT_ITERATION),
        tuple(events),
        (
            WorkflowReport(
                turn=report_turn,
                kind=ReportKind.IMPLEMENTATION,
                data=report,
            ),
        ),
        event_window_complete=True,
    )

    assert _stage_states(progress)[Step.IMPLEMENT] is expected
    assert _stage_states(progress)[Step.INTEGRATE] is WorkflowMilestoneState.CURRENT


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


def _entries(*steps: Step) -> tuple[Event, ...]:
    return tuple(
        _event(index, EventKind.STEP_STARTED, step=step, iteration=_PREVIOUS_ITERATION)
        for index, step in enumerate(steps, start=_FIRST_EVENT_ID)
    )


TERMINAL_ROWS = [
    (
        "done-with-truncated-history",
        Status.DONE,
        None,
        _entries(Step.VERIFY, Step.INTEGRATE),
        False,
        {
            Step.TRIAGE: WorkflowMilestoneState.SKIPPED,
            Step.IMPLEMENT: WorkflowMilestoneState.SKIPPED,
            Step.VERIFY: WorkflowMilestoneState.DONE,
            Step.INTEGRATE: WorkflowMilestoneState.DONE,
        },
    ),
    (
        "cancelled-at-consult-with-kept-step",
        Status.CANCELLED,
        Step.CONSULT,
        _entries(Step.TRIAGE, Step.IMPLEMENT, Step.CONSULT),
        True,
        {
            Step.TRIAGE: WorkflowMilestoneState.DONE,
            Step.IMPLEMENT: WorkflowMilestoneState.DONE,
            Step.CONSULT: WorkflowMilestoneState.STOPPED,
            Step.VERIFY: WorkflowMilestoneState.SKIPPED,
            Step.INTEGRATE: WorkflowMilestoneState.SKIPPED,
        },
    ),
    (
        "cancelled-with-cleared-step",
        Status.CANCELLED,
        None,
        _entries(Step.TRIAGE, Step.IMPLEMENT),
        True,
        {
            Step.TRIAGE: WorkflowMilestoneState.DONE,
            Step.IMPLEMENT: WorkflowMilestoneState.DONE,
            Step.VERIFY: WorkflowMilestoneState.SKIPPED,
            Step.INTEGRATE: WorkflowMilestoneState.SKIPPED,
        },
    ),
    (
        "live-task-keeps-unknown",
        Status.RUNNING,
        Step.IMPLEMENT,
        _entries(Step.IMPLEMENT),
        False,
        {
            Step.TRIAGE: WorkflowMilestoneState.UNKNOWN,
            Step.IMPLEMENT: WorkflowMilestoneState.CURRENT,
            Step.VERIFY: WorkflowMilestoneState.LATER,
            Step.INTEGRATE: WorkflowMilestoneState.LATER,
        },
    ),
    (
        "queued-task-shows-every-stage-later",
        Status.QUEUED,
        None,
        (),
        True,
        {
            Step.TRIAGE: WorkflowMilestoneState.LATER,
            Step.IMPLEMENT: WorkflowMilestoneState.LATER,
            Step.VERIFY: WorkflowMilestoneState.LATER,
            Step.INTEGRATE: WorkflowMilestoneState.LATER,
        },
    ),
]


def test_stages_are_never_unknown_without_a_live_entered_stage() -> None:
    for name, status, step, events, complete, expected in TERMINAL_ROWS:
        task = make_task(mode=Mode.IMPLEMENT, step=step, status=status)

        progress = project_workflow(task, events, (), event_window_complete=complete)

        states = _stage_states(progress)
        assert {stage: states[stage] for stage in expected} == expected, name
