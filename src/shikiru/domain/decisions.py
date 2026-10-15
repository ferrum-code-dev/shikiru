"""Pure derivation of a task's Decisions: a chronological log of who decided what."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, cast

from shikiru.domain.briefing import pull_request_line, squashed
from shikiru.domain.commands import EVENT_TEXT_KEY, ResolutionKind
from shikiru.domain.events import EventKind
from shikiru.domain.reports import (
    DECISION_ACCEPT,
    DECISION_ESCALATE,
    DECISION_SUPERSEDE,
    SEVERITY_BLOCKER,
)
from shikiru.domain.tasks import Hold

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import datetime

    from shikiru.domain.events import Event

MAX_DETAIL_LINES = 3
DECISION_KEY = "decision"
SUMMARY_KEY = "summary"
ISSUES_KEY = "issues"
SEVERITY_KEY = "severity"
DESCRIPTION_KEY = "description"
LOCATION_KEY = "location"
RESOLUTION_KEY = "resolution"
ROLE_KEY = "role"
HOLD_KEY = "hold"
NEW_TASKS_KEY = "new_task_ids"
SCOPE_CHANGES_KEY = "scope_changes"
RUNG_FROM_KEY = "from"
RUNG_TO_KEY = "to"
OPERATOR_KEY = "operator"
_SEVERITY_ORDER = {SEVERITY_BLOCKER: 0, "major": 1, "minor": 2}
_UNRANKED = len(_SEVERITY_ORDER)
_VERDICT_WORDS = {DECISION_ACCEPT: "accepted", DECISION_SUPERSEDE: "superseded"}


class Decider(StrEnum):
    """Who made a decision, as the Decisions rows name them."""

    LEADER = "Leader"
    YOU = "You"
    ENGINE = "Engine"


@dataclass(frozen=True)
class DecisionEntry:
    """One decision: when, by whom, a what/why headline and its capped detail lines."""

    at: datetime
    decider: Decider
    headline: str
    details: tuple[str, ...]


@dataclass(frozen=True)
class TaskDecisions:
    """A task's decisions, oldest first."""

    entries: tuple[DecisionEntry, ...]


def _text(data: dict[str, object], key: str) -> str | None:
    value = data.get(key)
    return squashed(value) if isinstance(value, str) else None


def _items(data: dict[str, object], key: str) -> list[object]:
    value = data.get(key)
    return cast("list[object]", value) if isinstance(value, list) else []


def _entry(event: Event, decider: Decider, headline: str, *details: str) -> DecisionEntry:
    return DecisionEntry(event.ts, decider, headline, details[:MAX_DETAIL_LINES])


def _issue_line(issue: dict[str, object]) -> str | None:
    description = _text(issue, DESCRIPTION_KEY)
    location = _text(issue, LOCATION_KEY)
    if description is None:
        return None
    return f"{description} ({location})" if location else description


def _issue_lines(data: dict[str, object]) -> list[str]:
    issues = cast(
        "list[dict[str, object]]",
        [issue for issue in _items(data, ISSUES_KEY) if isinstance(issue, dict)],
    )
    ranked = sorted(
        issues,
        key=lambda issue: _SEVERITY_ORDER.get(str(issue.get(SEVERITY_KEY)), _UNRANKED),
    )
    return [line for line in map(_issue_line, ranked) if line is not None]


def _verdict(event: Event, _: str | None) -> DecisionEntry | None:
    decision = str(event.data.get(DECISION_KEY))
    if decision == DECISION_ESCALATE:
        return None
    summary = _text(event.data, SUMMARY_KEY)
    headline = _VERDICT_WORDS.get(decision, decision.replace("_", " "))
    return _entry(
        event, Decider.LEADER, headline, *([summary] if summary else []), *_issue_lines(event.data)
    )


def _escalation(event: Event, _: str | None) -> DecisionEntry:
    question = squashed(event.message)
    return _entry(event, Decider.LEADER, f"escalated: {question}" if question else "escalated")


def _resolution(event: Event, _: str | None) -> DecisionEntry | None:
    answer = _text(event.data, EVENT_TEXT_KEY)
    headlines = {
        ResolutionKind.ANSWER.value: f'answered "{answer}"' if answer else "answered",
        ResolutionKind.RETRY.value: "retried",
        ResolutionKind.ACCEPT.value: "accepted as is",
    }
    headline = headlines.get(str(event.data.get(RESOLUTION_KEY)))
    return _entry(event, Decider.YOU, headline) if headline is not None else None


def _scope_change(event: Event, _: str | None) -> DecisionEntry:
    added = [str(task) for task in _items(event.data, NEW_TASKS_KEY)]
    scope = [
        text for text in map(squashed, map(str, _items(event.data, SCOPE_CHANGES_KEY))) if text
    ]
    return _entry(
        event, Decider.LEADER, f"added {', '.join(added)}" if added else "scope changed", *scope
    )


def _role(event: Event) -> str:
    return str(event.data.get(ROLE_KEY)).capitalize()


def _rung_change(event: Event, _: str | None) -> DecisionEntry:
    decider = Decider.YOU if event.data.get(OPERATOR_KEY) else Decider.ENGINE
    old = event.data.get(RUNG_FROM_KEY)
    lead = (
        f"{_role(event)}: {old} \N{RIGHTWARDS ARROW}"
        if old
        else f"{_role(event)} \N{RIGHTWARDS ARROW}"
    )
    return _entry(event, decider, f"{lead} {event.data.get(RUNG_TO_KEY)}")


def _operator_message(event: Event, _: str | None) -> DecisionEntry:
    text = _text(event.data, EVENT_TEXT_KEY)
    return _entry(
        event,
        Decider.YOU,
        f'messaged {_role(event)} "{text}"' if text else f"messaged {_role(event)}",
    )


def _you(headline: str) -> Callable[[Event, str | None], DecisionEntry]:
    return lambda event, _: _entry(event, Decider.YOU, headline)


def _role_action(verb: str) -> Callable[[Event, str | None], DecisionEntry]:
    return lambda event, _: _entry(event, Decider.YOU, f"{verb} {_role(event)}")


def _paused(headline: str) -> Callable[[Event, str | None], DecisionEntry | None]:
    def build(event: Event, _: str | None) -> DecisionEntry | None:
        return (
            _entry(event, Decider.YOU, headline)
            if event.data.get(HOLD_KEY) == Hold.PAUSED.value
            else None
        )

    return build


def _pull_request(verb: str) -> Callable[[Event, str | None], DecisionEntry]:
    def build(event: Event, _: str | None) -> DecisionEntry:
        number = pull_request_line(event.data).number
        return _entry(event, Decider.ENGINE, f"PR #{number} {verb}" if number else f"PR {verb}")

    return build


def _done(event: Event, pull_request: str | None) -> DecisionEntry:
    return _entry(event, Decider.ENGINE, f"{pull_request} merged" if pull_request else "merged")


_BUILDERS: dict[EventKind, Callable[[Event, str | None], DecisionEntry | None]] = {
    EventKind.VERDICT: _verdict,
    EventKind.ESCALATION: _escalation,
    EventKind.ESCALATION_RESOLVED: _resolution,
    EventKind.SCOPE_CHANGE: _scope_change,
    EventKind.RUNG_CHANGED: _rung_change,
    EventKind.OPERATOR_MESSAGE: _operator_message,
    EventKind.TAKEN_OVER: _role_action("took over"),
    EventKind.HANDED_BACK: _role_action("handed back"),
    EventKind.HOLD_SET: _paused("paused"),
    EventKind.HOLD_CLEARED: _paused("resumed"),
    EventKind.TASK_RETRIED: _you("retried"),
    EventKind.TASK_CANCELLED: _you("cancelled"),
    EventKind.PR_OPENED: _pull_request("opened"),
    EventKind.PR_WITHDRAWN: _pull_request("withdrawn"),
    EventKind.TASK_DONE: _done,
}


def derive_decisions(events: Sequence[Event]) -> TaskDecisions:
    """Derive a task's decision log from its recorded events, oldest first.

    Events of other kinds are ignored; a cancelling resolution is shown by the cancel event.
    """
    entries: list[DecisionEntry] = []
    pull_request: str | None = None
    for event in sorted(events, key=lambda recorded: recorded.id):
        if event.kind is EventKind.PR_OPENED:
            number = pull_request_line(event.data).number
            pull_request = f"PR #{number}" if number else "PR"
        builder = _BUILDERS.get(event.kind)
        entry = builder(event, pull_request) if builder is not None else None
        if entry is not None:
            entries.append(entry)
    return TaskDecisions(tuple(entries))
