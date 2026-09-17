"""Escalation options and their recommended default (docs/design/03-workflow.md §8)."""

from __future__ import annotations

from dataclasses import dataclass

from shikiru.domain.tasks import EscalationReason

_RETRY_OR_CANCEL_REASONS = frozenset(
    {
        EscalationReason.INTEGRATION_CONFLICT,
        EscalationReason.INTEGRATION_BLOCKED,
        EscalationReason.PR_CLOSED,
        EscalationReason.AGENT_AUTH,
        EscalationReason.AGENT_ERROR,
        EscalationReason.AGENT_CRASH,
        EscalationReason.AGENT_STUCK,
        EscalationReason.AGENT_PROTOCOL,
        EscalationReason.LADDER_EXHAUSTED,
        EscalationReason.QUOTA_EXHAUSTED,
        EscalationReason.DEPENDENCY_CANCELLED,
    }
)


@dataclass(frozen=True)
class EscalationOption:
    """One option `resolve` accepts; exactly one option per reason is `recommended`."""

    label: str
    recommended: bool = False


def options_for(reason: EscalationReason) -> tuple[EscalationOption, ...]:
    """Return the resolution options for `reason`, with exactly one recommended default (03 §8)."""
    if reason is EscalationReason.DECISION_REQUIRED:
        return (EscalationOption("answer", recommended=True), EscalationOption("cancel"))
    if reason is EscalationReason.ITERATIONS_EXHAUSTED:
        return (
            EscalationOption("answer", recommended=True),
            EscalationOption("accept"),
            EscalationOption("cancel"),
        )
    if reason in _RETRY_OR_CANCEL_REASONS:
        return (EscalationOption("retry", recommended=True), EscalationOption("cancel"))
    message = f"no options defined for escalation reason {reason!r}"
    raise ValueError(message)
