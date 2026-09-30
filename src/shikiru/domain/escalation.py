"""Escalation options and their recommended default (docs/design/03-workflow.md §8)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum, auto
from typing import TYPE_CHECKING

from shikiru.domain.commands import ResolutionKind
from shikiru.domain.ladder import reset_for_integration_attempt, reset_for_rung_change
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import Counters, DomainModel, EscalationReason, Role, Status, Step, Task

if TYPE_CHECKING:
    from shikiru.agents.contracts import Rung


@dataclass(frozen=True)
class EscalationOption:
    """One option `resolve` accepts; exactly one option per reason is `recommended`."""

    label: ResolutionKind
    recommended: bool = False


_ANSWER_OR_CANCEL = (
    EscalationOption(ResolutionKind.ANSWER, recommended=True),
    EscalationOption(ResolutionKind.CANCEL),
)
_RETRY_OR_CANCEL = (
    EscalationOption(ResolutionKind.RETRY, recommended=True),
    EscalationOption(ResolutionKind.CANCEL),
)

_OPTIONS_BY_REASON: dict[EscalationReason, tuple[EscalationOption, ...]] = {
    EscalationReason.DECISION_REQUIRED: _ANSWER_OR_CANCEL,
    EscalationReason.ITERATIONS_EXHAUSTED: (
        EscalationOption(ResolutionKind.ANSWER, recommended=True),
        EscalationOption(ResolutionKind.ACCEPT),
        EscalationOption(ResolutionKind.CANCEL),
    ),
    EscalationReason.INTEGRATION_CONFLICT: _RETRY_OR_CANCEL,
    EscalationReason.INTEGRATION_BLOCKED: _RETRY_OR_CANCEL,
    EscalationReason.UNCOMMITTED_WORK: _RETRY_OR_CANCEL,
    EscalationReason.PR_CLOSED: _RETRY_OR_CANCEL,
    EscalationReason.AGENT_AUTH: _RETRY_OR_CANCEL,
    EscalationReason.AGENT_ERROR: _RETRY_OR_CANCEL,
    EscalationReason.AGENT_STUCK: _RETRY_OR_CANCEL,
    EscalationReason.AGENT_PROTOCOL: _RETRY_OR_CANCEL,
    EscalationReason.LADDER_EXHAUSTED: _RETRY_OR_CANCEL,
    EscalationReason.QUOTA_EXHAUSTED: _RETRY_OR_CANCEL,
    EscalationReason.DEPENDENCY_CANCELLED: _RETRY_OR_CANCEL,
    EscalationReason.ENGINE_ERROR: _RETRY_OR_CANCEL,
}


def options_for(reason: EscalationReason) -> tuple[EscalationOption, ...]:
    """Return the resolution options for `reason`, with exactly one recommended default (03 §8)."""
    return _OPTIONS_BY_REASON[reason]


def reset_for_resolution(counters: Counters, reason: EscalationReason) -> Counters:
    """Reset the persisted counter represented by `reason` when it is resolved (03 §8)."""
    if reason in (EscalationReason.DECISION_REQUIRED, EscalationReason.ITERATIONS_EXHAUSTED):
        return counters.model_copy(update={"consults": 0})
    if reason in (
        EscalationReason.INTEGRATION_CONFLICT,
        EscalationReason.INTEGRATION_BLOCKED,
        EscalationReason.PR_CLOSED,
    ):
        return counters.model_copy(update={"conflict_rounds": 0})
    return counters


def transition_for_resolution(
    task: Task,
    reason: EscalationReason,
    *,
    step: Step | None,
    resolution: ResolutionKind,
    rung: Rung | None = None,
    role: Role | None = None,
) -> Transition:
    """Build the state transition for one validated escalation resolution."""
    counters = reset_for_resolution(task.counters, reason)
    set_overrides: dict[Role, Rung] = {}
    target_step = step or task.step
    status = Status.QUEUED
    if resolution is ResolutionKind.ANSWER:
        target_step = Step.CONSULT
    elif resolution is ResolutionKind.ACCEPT:
        target_step = Step.INTEGRATE
        counters = reset_for_integration_attempt(counters)
    elif resolution is ResolutionKind.CANCEL:
        status = Status.CANCELLED
    if rung is not None:
        if role is None:
            message = "a rung resolution requires its acting role"
            raise ValueError(message)
        set_overrides[role] = rung
        counters = reset_for_rung_change(counters)
    return Transition(
        step=target_step,
        status=status,
        iteration=task.iteration,
        counters=counters,
        rung_index=dict(task.rung_index),
        set_overrides=set_overrides,
    )


class EscalationStatus(StrEnum):
    """An escalation's lifecycle (05 §2)."""

    OPEN = auto()
    RESOLVED = auto()


class Escalation(DomainModel):
    """One raised escalation (03 §8; mirrors the `escalations` table, 05 §2)."""

    id: str
    task_id: str
    step: Step | None = None
    reason: EscalationReason
    headline: str
    packet_path: str
    options: tuple[EscalationOption, ...]
    status: EscalationStatus
    resolution: str | None = None
    created_at: datetime
    resolved_at: datetime | None = None
