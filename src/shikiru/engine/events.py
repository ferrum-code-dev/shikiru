"""Shared event construction for engine lifecycle boundaries."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.agents.contracts import COST_BASIS_UNKNOWN
from shikiru.domain.events import EventKind
from shikiru.domain.ladder import pick_rung
from shikiru.domain.recovery import Failed, ProtocolError, Stalled
from shikiru.domain.reports import (
    DECISION_ANSWER,
    STATUS_COMPLETED,
    ConsultAnswer,
    FeedbackAnalysis,
    ImplementationReport,
    PlanReport,
    TriageReport,
    Verdict,
)
from shikiru.domain.steps import ACTING_ROLE

if TYPE_CHECKING:
    from shikiru.agents.contracts import Rung, Usage
    from shikiru.config.settings import Settings
    from shikiru.domain.recovery import TurnResult
    from shikiru.domain.reports import ReportKind
    from shikiru.domain.steps import Outcome
    from shikiru.domain.tasks import Role, Step, Task
    from shikiru.engine.turn_execution import TurnExecution
    from shikiru.store.events import EventLog

TURN_FIELD = "turn"
ROLE_FIELD = "role"
ITERATION_FIELD = "iteration"
REPORT_KIND_FIELD = "report_kind"


def emit_step_started(events: EventLog, task: Task, settings: Settings) -> None:
    """Record the selected role and rung when a task step starts."""
    if task.step is None:
        return
    role = ACTING_ROLE[task.step]
    rung = pick_rung(task, role, settings)
    events.append(
        EventKind.STEP_STARTED,
        task_id=task.id,
        message=f"{task.id} {task.step.value} started",
        data={
            "step": task.step.value,
            "role": role.value,
            "iteration": task.iteration,
            "agent": rung.agent,
            "model": rung.model,
        },
    )


def emit_turn_started(
    events: EventLog,
    task: Task,
    step: Step,
    role: Role,
    rung: Rung,
    *,
    kind: ReportKind,
    run_id: int | None = None,
    turn: str | None = None,
) -> None:
    """Record that `role` started its `step` turn on `rung`, owing a report of `kind`."""
    events.append(
        EventKind.TURN_STARTED,
        task_id=task.id,
        message=f"{task.id} {role.value} turn started",
        data={
            "step": step.value,
            "role": role.value,
            "iteration": task.iteration,
            "agent": rung.agent,
            "model": rung.model,
            "effort": rung.effort,
            REPORT_KIND_FIELD: kind.value,
            **_turn_data(turn),
            **({"run_id": run_id} if run_id is not None else {}),
        },
    )


def emit_turn_stopped(
    events: EventLog,
    task: Task,
    step: Step,
    role: Role,
    *,
    rung: Rung,
    run_id: int,
    turn: str | None = None,
) -> None:
    """Record that `role`'s `step` turn ended without a result because the task was stopped."""
    events.append(
        EventKind.TURN_STOPPED,
        task_id=task.id,
        message=f"{task.id} {role.value} turn stopped",
        data={
            "step": step.value,
            "role": role.value,
            "iteration": task.iteration,
            "agent": rung.agent,
            "model": rung.model,
            "effort": rung.effort,
            "run_id": run_id,
            **_turn_data(turn),
        },
    )


def emit_nudge(
    events: EventLog, task: Task, step: Step, role: Role, *, turn: str | None = None
) -> None:
    """Record that the engine nudged `role`'s stalled `step` turn."""
    events.append(
        EventKind.NUDGE,
        task_id=task.id,
        message=f"{task.id} {role.value} nudge",
        data={"step": step.value, "role": role.value, **_turn_data(turn)},
    )


def emit_turn_failed(
    events: EventLog,
    task: Task,
    step: Step,
    result: TurnResult,
    *,
    rung: Rung,
    run_id: int,
    turn: str | None = None,
    role: Role | None = None,
) -> None:
    """Record a turn that ended without a valid report."""
    if isinstance(result, Failed):
        message = result.message
        data: dict[str, object] = {"kind": result.kind.value, "resets_at": result.resets_at}
    elif isinstance(result, Stalled):
        message = "agent stalled"
        data = {}
    elif isinstance(result, ProtocolError):
        message = "agent protocol error"
        data = {}
    else:
        message = type(result).__name__
        data = {}
    data.update(
        {
            "step": step.value,
            "result": type(result).__name__,
            **_provenance_data(role, task.iteration),
            "agent": rung.agent,
            "model": rung.model,
            "effort": rung.effort,
            "run_id": run_id,
            **_turn_data(turn),
        }
    )
    events.append(EventKind.TURN_FAILED, task_id=task.id, message=message, data=data)


def emit_turn_completed(
    events: EventLog, task: Task, outcome: Outcome, execution: TurnExecution
) -> None:
    """Record a completed turn and, for a verdict, the verdict itself."""
    summary = _turn_summary(outcome)
    events.append(
        EventKind.TURN_COMPLETED,
        task_id=task.id,
        message=summary,
        data={
            "step": task.step.value if task.step is not None else None,
            "iteration": task.iteration,
            "role": execution.role.value,
            "agent": execution.rung.agent,
            "model": execution.rung.model,
            "effort": execution.rung.effort,
            "run_id": execution.run_id,
            REPORT_KIND_FIELD: execution.kind.value,
            "outcome": _turn_outcome(outcome),
            "summary": summary,
            "question": _turn_question(outcome),
            "usage_delta": _usage_delta(execution.usage_before, execution.usage_after),
            **_turn_data(execution.turn_dir.name),
        },
    )
    if isinstance(outcome, Verdict):
        emit_verdict(
            events,
            task,
            outcome,
            summary,
            turn=execution.turn_dir.name,
            role=execution.role,
        )


def emit_verdict(
    events: EventLog,
    task: Task,
    verdict: Verdict,
    summary: str,
    *,
    turn: str | None = None,
    role: Role | None = None,
) -> None:
    """Record the Leader's (or the engine's) verdict on a task."""
    events.append(
        EventKind.VERDICT,
        task_id=task.id,
        message=summary,
        data={
            "decision": verdict.decision,
            "summary": summary,
            "issues": [issue.model_dump() for issue in verdict.issues],
            "residual_risks": list(verdict.residual_risks),
            **_provenance_data(role, task.iteration),
            **_turn_data(turn),
        },
    )


def _turn_data(turn: str | None) -> dict[str, str]:
    return {TURN_FIELD: turn} if turn is not None else {}


def _provenance_data(role: Role | None, iteration: int) -> dict[str, str | int]:
    data: dict[str, str | int] = {ITERATION_FIELD: iteration}
    if role is not None:
        data[ROLE_FIELD] = role.value
    return data


def _turn_summary(outcome: Outcome) -> str:
    if isinstance(outcome, TriageReport):
        return outcome.title
    if isinstance(outcome, ConsultAnswer):
        return outcome.answer if outcome.decision == DECISION_ANSWER else "escalate"
    return outcome.summary


def _turn_question(outcome: Outcome) -> str:
    return outcome.question if isinstance(outcome, ImplementationReport) else ""


def _turn_outcome(outcome: Outcome) -> str:
    if isinstance(outcome, TriageReport):
        return outcome.mode.value
    if isinstance(outcome, PlanReport | FeedbackAnalysis):
        return STATUS_COMPLETED
    if isinstance(outcome, ConsultAnswer):
        return outcome.decision
    if isinstance(outcome, ImplementationReport):
        return outcome.status
    return outcome.decision


def _usage_delta(before: Usage, after: Usage) -> dict[str, int | float | str | None]:
    cost_delta, cost_basis = _comparable_cost_delta(before, after)
    return {
        "input_tokens": after.input_tokens - before.input_tokens,
        "output_tokens": after.output_tokens - before.output_tokens,
        "cache_read_tokens": after.cache_read_tokens - before.cache_read_tokens,
        "cache_write_tokens": after.cache_write_tokens - before.cache_write_tokens,
        "cost_usd": cost_delta,
        "cost_basis": cost_basis,
    }


def _comparable_cost_delta(before: Usage, after: Usage) -> tuple[float | None, str]:
    if (
        before.cost_usd is None
        or after.cost_usd is None
        or before.cost_basis != after.cost_basis
        or after.cost_basis == COST_BASIS_UNKNOWN
        or after.cost_usd < before.cost_usd
    ):
        return None, COST_BASIS_UNKNOWN
    return after.cost_usd - before.cost_usd, after.cost_basis
