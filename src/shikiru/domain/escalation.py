"""Escalation options and their recommended default (docs/design/03-workflow.md §8)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum, auto

from shikiru.domain.commands import ResolutionKind
from shikiru.domain.tasks import DomainModel, EscalationReason, Step


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
    EscalationReason.PR_CLOSED: _RETRY_OR_CANCEL,
    EscalationReason.AGENT_AUTH: _RETRY_OR_CANCEL,
    EscalationReason.AGENT_ERROR: _RETRY_OR_CANCEL,
    EscalationReason.AGENT_CRASH: _RETRY_OR_CANCEL,
    EscalationReason.AGENT_STUCK: _RETRY_OR_CANCEL,
    EscalationReason.AGENT_PROTOCOL: _RETRY_OR_CANCEL,
    EscalationReason.LADDER_EXHAUSTED: _RETRY_OR_CANCEL,
    EscalationReason.QUOTA_EXHAUSTED: _RETRY_OR_CANCEL,
    EscalationReason.DEPENDENCY_CANCELLED: _RETRY_OR_CANCEL,
}


def options_for(reason: EscalationReason) -> tuple[EscalationOption, ...]:
    """Return the resolution options for `reason`, with exactly one recommended default (03 §8)."""
    return _OPTIONS_BY_REASON[reason]


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
