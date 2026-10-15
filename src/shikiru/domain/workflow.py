"""Pure recorded workflow history projection (03 §§2-3/8-9)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, auto
from typing import TYPE_CHECKING

from shikiru.domain.events import Event, EventKind
from shikiru.domain.reports import (
    DECISION_ACCEPT,
    DECISION_ANSWER,
    DECISION_CHANGES_REQUESTED,
    STATUS_COMPLETED,
    ConsultAnswer,
    ImplementationReport,
    PlanReport,
    Report,
    ReportKind,
    TriageReport,
    Verdict,
)
from shikiru.domain.tasks import Mode, Status, Step, Task

if TYPE_CHECKING:
    from collections.abc import Sequence

WORKFLOW_STEP_KEY = "step"
WORKFLOW_ITERATION_KEY = "iteration"
WORKFLOW_TURN_KEY = "turn"
WORKFLOW_DECISION_KEY = "decision"


class WorkflowMilestoneState(StrEnum):
    """Recorded semantic outcome for one workflow stage."""

    DONE = auto()
    CURRENT = auto()
    LATER = auto()
    SKIPPED = auto()
    REWORK = auto()
    UNKNOWN = auto()
    STOPPED = auto()


@dataclass(frozen=True)
class WorkflowReport:
    """One already-validated agent report with its retained turn identity."""

    turn: str
    kind: ReportKind
    data: Report


@dataclass(frozen=True)
class WorkflowMilestone:
    """One stage and its recorded workflow outcome."""

    stage: Step
    state: WorkflowMilestoneState


@dataclass(frozen=True)
class WorkflowProgress:
    """Milestones projected from bounded task history and its completeness signal."""

    milestones: tuple[WorkflowMilestone, ...]
    history_complete: bool


@dataclass(frozen=True)
class _StepEntry:
    event_index: int
    event_id: int
    step: Step | None
    iteration: int | None


def project_workflow(
    task: Task,
    events: Sequence[Event],
    reports: Sequence[WorkflowReport],
    *,
    event_window_complete: bool,
) -> WorkflowProgress:
    """Project factual milestones from retained transitions and validated agent reports."""
    history_complete = (
        event_window_complete and bool(events) and events[0].kind is EventKind.TASK_QUEUED
    )
    entries = _step_entries(events)
    entered_steps = {entry.step for entry in entries if entry.step is not None}
    first_entered_step = entries[0].step if entries else None
    reported_steps = {
        step for report in reports if (step := _report_stage(report.kind)) is not None
    }
    completed_reports = _completed_reports(reports, events, entries)
    stages = _workflow_stages(task, entered_steps, reported_steps, entries)
    if not stages:
        return WorkflowProgress((), history_complete)
    recorded = entered_steps | reported_steps
    settled = _settled_milestones(task, stages, recorded)
    if settled is not None:
        return WorkflowProgress(settled, history_complete)
    stage_positions = {step: index for index, step in enumerate(stages)}
    current_position = stage_positions.get(task.step) if task.step is not None else None
    last_entries = _last_step_entries(entries)
    verdicts = _verdicts(events)

    def milestone_state(step: Step) -> WorkflowMilestoneState:
        if step is task.step:
            state = WorkflowMilestoneState.CURRENT
        elif (
            step is Step.TRIAGE
            and history_complete
            and first_entered_step in {Step.PLAN, Step.IMPLEMENT}
        ):
            state = WorkflowMilestoneState.SKIPPED
        elif step in entered_steps | reported_steps:
            latest_entry = last_entries.get(step)
            later_steps = {
                entry.step
                for entry in entries
                if latest_entry is not None
                and entry.step is not None
                and entry.event_index > latest_entry.event_index
            }
            rework_requested = step is Step.VERIFY and any(
                index > (latest_entry.event_index if latest_entry is not None else -1)
                and decision == DECISION_CHANGES_REQUESTED
                for index, decision in verdicts
            )
            if rework_requested:
                state = WorkflowMilestoneState.REWORK
            elif _completed_by_later_step(step, later_steps) or _report_completes_entered_stage(
                step,
                latest_entry.event_id if latest_entry is not None else None,
                completed_reports,
            ):
                state = WorkflowMilestoneState.DONE
            else:
                state = WorkflowMilestoneState.UNKNOWN
        elif current_position is not None and stage_positions[step] > current_position:
            state = WorkflowMilestoneState.LATER
        else:
            state = WorkflowMilestoneState.UNKNOWN
        return state

    milestones = tuple(
        WorkflowMilestone(
            stage=step,
            state=milestone_state(step),
        )
        for step in stages
    )
    return WorkflowProgress(milestones, history_complete)


def _settled_milestones(
    task: Task, stages: tuple[Step, ...], recorded: set[Step]
) -> tuple[WorkflowMilestone, ...] | None:
    if task.status in (Status.DONE, Status.CANCELLED):
        return _terminal_milestones(task, stages, recorded)
    if task.step is None and not recorded:
        return tuple(WorkflowMilestone(step, WorkflowMilestoneState.LATER) for step in stages)
    return None


def _terminal_milestones(
    task: Task, stages: tuple[Step, ...], recorded: set[Step]
) -> tuple[WorkflowMilestone, ...]:
    def state(step: Step) -> WorkflowMilestoneState:
        if task.status is Status.CANCELLED and step is task.step:
            return WorkflowMilestoneState.STOPPED
        if step in recorded or (step is Step.INTEGRATE and task.status is Status.DONE):
            return WorkflowMilestoneState.DONE
        return WorkflowMilestoneState.SKIPPED

    return tuple(WorkflowMilestone(step, state(step)) for step in stages)


def _step_entries(events: Sequence[Event]) -> list[_StepEntry]:
    entries: list[_StepEntry] = []
    for index, event in enumerate(events):
        value = event.data.get(WORKFLOW_STEP_KEY)
        if event.kind is not EventKind.STEP_STARTED:
            continue
        try:
            step = Step(value) if isinstance(value, str) else None
        except ValueError:
            step = None
        iteration = event.data.get(WORKFLOW_ITERATION_KEY)
        entries.append(
            _StepEntry(
                index,
                event.id,
                step,
                iteration if type(iteration) is int else None,
            )
        )
    return entries


def _completed_reports(
    reports: Sequence[WorkflowReport],
    events: Sequence[Event],
    entries: list[_StepEntry],
) -> set[tuple[Step, int]]:
    report_entries: dict[str, tuple[Step, int]] = {}
    for index, event in enumerate(events):
        turn = event.data.get(WORKFLOW_TURN_KEY)
        step_value = event.data.get(WORKFLOW_STEP_KEY)
        iteration = event.data.get(WORKFLOW_ITERATION_KEY)
        if (
            event.kind is EventKind.TURN_STARTED
            and isinstance(turn, str)
            and isinstance(step_value, str)
            and type(iteration) is int
        ):
            try:
                step = Step(step_value)
            except ValueError:
                continue
            if not entries:
                continue
            latest_entry = next(
                (entry for entry in reversed(entries) if entry.event_index < index),
                None,
            )
            if latest_entry is None or (latest_entry.step, latest_entry.iteration) != (
                step,
                iteration,
            ):
                continue
            report_entries[turn] = (step, latest_entry.event_id)
    completed: set[tuple[Step, int]] = set()
    for report in reports:
        stage = _report_stage(report.kind)
        if stage is None or not _report_completes_stage(report.data):
            continue
        report_entry = report_entries.get(report.turn)
        if report_entry is not None and report_entry[0] is stage:
            completed.add((stage, report_entry[1]))
    return completed


def _last_step_entries(
    entries: list[_StepEntry],
) -> dict[Step, _StepEntry]:
    last_entries: dict[Step, _StepEntry] = {}
    for entry in entries:
        if entry.step is not None:
            last_entries[entry.step] = entry
    return last_entries


def _verdicts(events: Sequence[Event]) -> list[tuple[int, object]]:
    return [
        (index, event.data.get(WORKFLOW_DECISION_KEY))
        for index, event in enumerate(events)
        if event.kind is EventKind.VERDICT
    ]


def _workflow_stages(
    task: Task,
    entered_steps: set[Step],
    reported_steps: set[Step],
    entries: list[_StepEntry],
) -> tuple[Step, ...]:
    observed_steps = entered_steps | reported_steps
    if task.mode is Mode.PLAN or task.step is Step.PLAN or Step.PLAN in observed_steps:
        stages = [Step.TRIAGE, Step.PLAN, Step.INTEGRATE]
        if Step.CONSULT in observed_steps or task.step is Step.CONSULT:
            stages.append(Step.CONSULT)
        if Step.VERIFY in entered_steps or task.step is Step.VERIFY:
            stages.append(Step.VERIFY)
        return tuple(stages)
    if (
        task.mode is Mode.IMPLEMENT
        or task.step
        in {
            Step.IMPLEMENT,
            Step.CONSULT,
            Step.VERIFY,
            Step.INTEGRATE,
        }
        or observed_steps & {Step.IMPLEMENT, Step.CONSULT, Step.VERIFY, Step.INTEGRATE}
    ):
        stages = [Step.TRIAGE, Step.IMPLEMENT, Step.VERIFY, Step.INTEGRATE]
    elif task.step is Step.TRIAGE:
        stages = [Step.TRIAGE]
    else:
        return ()
    if Step.CONSULT in observed_steps or task.step is Step.CONSULT:
        if _consult_follows_integration(task, entries):
            stages.append(Step.CONSULT)
        else:
            stages.insert(stages.index(Step.IMPLEMENT) + 1, Step.CONSULT)
    return tuple(stages)


def _consult_follows_integration(task: Task, entries: list[_StepEntry]) -> bool:
    if task.step is Step.CONSULT and task.status is Status.INTEGRATING:
        return True
    for index in range(len(entries) - 1, -1, -1):
        entry = entries[index]
        if entry.step is not Step.CONSULT or entry.iteration != task.iteration:
            continue
        if index == 0:
            return False
        previous = entries[index - 1]
        return previous.step is Step.INTEGRATE and previous.iteration == task.iteration
    if task.step is Step.CONSULT and entries:
        latest = entries[-1]
        return latest.step is Step.INTEGRATE and latest.iteration == task.iteration
    return False


def _completed_by_later_step(stage: Step, later_steps: set[Step]) -> bool:
    match stage:
        case Step.TRIAGE:
            return bool(later_steps & {Step.PLAN, Step.IMPLEMENT})
        case Step.PLAN:
            return Step.INTEGRATE in later_steps
        case Step.IMPLEMENT:
            return bool(later_steps & {Step.VERIFY, Step.CONSULT})
        case Step.CONSULT:
            return bool(later_steps & {Step.IMPLEMENT, Step.INTEGRATE})
        case Step.VERIFY:
            return Step.INTEGRATE in later_steps
        case Step.INTEGRATE:
            return False


def _report_stage(kind: ReportKind) -> Step | None:
    match kind:
        case ReportKind.TRIAGE:
            return Step.TRIAGE
        case ReportKind.PLAN:
            return Step.PLAN
        case ReportKind.IMPLEMENTATION:
            return Step.IMPLEMENT
        case ReportKind.CONSULT:
            return Step.CONSULT
        case ReportKind.VERDICT:
            return Step.VERIFY


def _report_completes_stage(report: Report) -> bool:
    match report:
        case TriageReport() | PlanReport():
            return True
        case ImplementationReport(status=status) if status == STATUS_COMPLETED:
            return True
        case ConsultAnswer(decision=decision) if decision == DECISION_ANSWER:
            return True
        case Verdict(decision=decision) if decision == DECISION_ACCEPT:
            return True
        case _:
            return False


def _report_completes_entered_stage(
    stage: Step,
    entered_identity: int | None,
    completed_reports: set[tuple[Step, int]],
) -> bool:
    return entered_identity is not None and (stage, entered_identity) in completed_reports
