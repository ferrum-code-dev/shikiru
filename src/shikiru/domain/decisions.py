"""Pure derivation of a task's Decisions: verification rounds and the Operator's actions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, cast

from shikiru.domain.briefing import severity_counts, squashed
from shikiru.domain.commands import EVENT_TEXT_KEY, ResolutionKind
from shikiru.domain.events import EventKind

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from shikiru.domain.briefing import VerdictCounts
    from shikiru.domain.events import Event

DECISION_KEY = "decision"
ISSUES_KEY = "issues"
SEVERITY_KEY = "severity"
RESOLUTION_KEY = "resolution"


class OperatorAction(StrEnum):
    """What the Operator did, in the words the Decisions rows show."""

    ANSWERED = "answered"
    RETRIED = "retried"
    ACCEPTED = "accepted"
    CANCELLED = "cancelled"
    MESSAGED = "messaged"


_RESOLUTION_ACTIONS = {
    ResolutionKind.ANSWER.value: OperatorAction.ANSWERED,
    ResolutionKind.RETRY.value: OperatorAction.RETRIED,
    ResolutionKind.ACCEPT.value: OperatorAction.ACCEPTED,
    ResolutionKind.CANCEL.value: OperatorAction.CANCELLED,
}


@dataclass(frozen=True)
class OperatorEntry:
    """One Operator action with its time and the text they gave, if any."""

    at: datetime
    action: OperatorAction
    text: str | None


@dataclass(frozen=True)
class TaskDecisions:
    """Verification rounds in order, then the Operator's actions in order."""

    rounds: tuple[VerdictCounts, ...]
    operator: tuple[OperatorEntry, ...]


def _severities(data: dict[str, object]) -> list[str]:
    issues = data.get(ISSUES_KEY)
    found = (
        cast("dict[str, object]", issue).get(SEVERITY_KEY)
        for issue in (cast("list[object]", issues) if isinstance(issues, list) else [])
        if isinstance(issue, dict)
    )
    return [severity for severity in found if isinstance(severity, str)]


def _text(data: dict[str, object]) -> str | None:
    text = data.get(EVENT_TEXT_KEY)
    return squashed(text) if isinstance(text, str) else None


def _operator_entry(event: Event) -> OperatorEntry | None:
    if event.kind is EventKind.OPERATOR_MESSAGE:
        return OperatorEntry(event.ts, OperatorAction.MESSAGED, _text(event.data))
    action = _RESOLUTION_ACTIONS.get(str(event.data.get(RESOLUTION_KEY)))
    return OperatorEntry(event.ts, action, _text(event.data)) if action is not None else None


def derive_decisions(verdicts: Sequence[Event], operator_events: Sequence[Event]) -> TaskDecisions:
    """Derive the Decisions of a task from its recorded verdict and Operator events.

    Rounds follow the verdict events in the order given; Operator actions are ordered by time.
    """
    rounds = tuple(
        severity_counts(str(event.data.get(DECISION_KEY)), _severities(event.data))
        for event in verdicts
    )
    entries = (_operator_entry(event) for event in sorted(operator_events, key=lambda e: e.id))
    return TaskDecisions(rounds, tuple(entry for entry in entries if entry is not None))
