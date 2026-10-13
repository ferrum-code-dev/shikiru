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


ESCALATION_REASON_LABELS: dict[EscalationReason, str] = {
    EscalationReason.DECISION_REQUIRED: "Decision required",
    EscalationReason.ITERATIONS_EXHAUSTED: "Iterations exhausted",
    EscalationReason.INTEGRATION_CONFLICT: "Integration conflict",
    EscalationReason.INTEGRATION_BLOCKED: "Integration blocked",
    EscalationReason.UNCOMMITTED_WORK: "Uncommitted work",
    EscalationReason.OFF_TASK_BRANCH: "Off task branch",
    EscalationReason.PR_CLOSED: "Pull request closed",
    EscalationReason.AGENT_AUTH: "Agent authentication",
    EscalationReason.AGENT_ERROR: "Agent error",
    EscalationReason.AGENT_STUCK: "Agent stuck",
    EscalationReason.AGENT_PROTOCOL: "Agent protocol error",
    EscalationReason.LADDER_EXHAUSTED: "Ladder exhausted",
    EscalationReason.QUOTA_EXHAUSTED: "Quota exhausted",
    EscalationReason.DEPENDENCY_CANCELLED: "Dependency cancelled",
    EscalationReason.ENGINE_ERROR: "Engine error",
}


ESCALATION_CAUSES: dict[EscalationReason, str] = {
    EscalationReason.DECISION_REQUIRED: "The Leader needs an Operator decision to continue.",
    EscalationReason.ITERATIONS_EXHAUSTED: "The final review round still requested changes.",
    EscalationReason.INTEGRATION_CONFLICT: "Rebase conflicts could not be resolved automatically.",
    EscalationReason.INTEGRATION_BLOCKED: "Integration is blocked: see the question.",
    EscalationReason.UNCOMMITTED_WORK: (
        "The task workspace has uncommitted changes; commit or discard them, then retry."
    ),
    EscalationReason.OFF_TASK_BRANCH: (
        "The task workspace is not on its task branch; move its work onto the branch, then retry."
    ),
    EscalationReason.PR_CLOSED: "The pull request was closed without merging.",
    EscalationReason.AGENT_AUTH: (
        "The agent cannot access its provider; check credentials, billing, or account status."
    ),
    EscalationReason.AGENT_ERROR: "The agent kept failing after every automatic retry.",
    EscalationReason.AGENT_STUCK: "The agent's screen stayed stuck after every automatic recovery.",
    EscalationReason.AGENT_PROTOCOL: "The turn never produced a report, even with a Helper rebuild",
    EscalationReason.LADDER_EXHAUSTED: "Every rung on the ladder was tried without success.",
    EscalationReason.QUOTA_EXHAUSTED: "The agent's quota stayed exhausted past the wait budget.",
    EscalationReason.DEPENDENCY_CANCELLED: "A dependency of this task was cancelled.",
    EscalationReason.ENGINE_ERROR: "The engine hit an unexpected error while running this task.",
}

RESOLUTION_CONSEQUENCES: dict[ResolutionKind, str] = {
    ResolutionKind.ANSWER: "Give the Leader guidance; the task continues from a consult turn.",
    ResolutionKind.RETRY: "Re-run the step that raised this escalation.",
    ResolutionKind.ACCEPT: "Integrate the work as it stands.",
    ResolutionKind.CANCEL: (
        "Cancel the task and keep its branch; preserve its worktree when uncommitted work remains "
        "or it is off its task branch."
    ),
}


def escalation_reason_label(reason: EscalationReason) -> str:
    """Return the Operator-facing name of `reason`."""
    return ESCALATION_REASON_LABELS[reason]


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
    EscalationReason.OFF_TASK_BRANCH: _RETRY_OR_CANCEL,
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
        base=task,
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
