"""The capped digest of what a task did before it escalated (03 §8)."""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from shikiru.domain.briefing import one_line, parse_produced, reported_tests, verdict_counts
from shikiru.domain.events import EventKind
from shikiru.domain.reports import ImplementationReport, Verdict

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from shikiru.domain.events import Event
    from shikiru.domain.reports import Report
    from shikiru.domain.tasks import Task

TURN_FIELD = "turn"
ROLE_FIELD = "role"
STEP_FIELD = "step"
OUTCOME_FIELD = "outcome"
TIMES = "\N{MULTIPLICATION SIGN}"
EARLIER_TEMPLATE = "+{count} earlier"
FAILURE_MESSAGE_LIMIT = 80
_DECISION_WORDS = {"changes_requested": "changes requested"}
_RECOVERY_NOUNS = (
    (EventKind.NUDGE, "nudge"),
    (EventKind.HELPER_ACTION, "helper action"),
    (EventKind.RUNG_CHANGED, "rung change"),
)
_OPERATOR_NOUNS = (
    (EventKind.OPERATOR_MESSAGE, "message"),
    (EventKind.TASK_RETRIED, "earlier retry"),
)


def _count(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def _counted(counts: Counter[EventKind], nouns: tuple[tuple[EventKind, str], ...]) -> str:
    return ", ".join(_count(counts[kind], noun) for kind, noun in nouns if counts[kind])


def _rounds(events: Sequence[Event], task: Task, max_iterations: int) -> str | None:
    steps = Counter(
        str(event.data.get(STEP_FIELD)) for event in events if event.kind is EventKind.STEP_STARTED
    )
    if not steps:
        return None
    line = "Rounds: " + ", ".join(f"{step} {TIMES}{count}" for step, count in steps.items())
    if task.iteration > 0:
        line += f" (iteration {task.iteration} of {max_iterations})"
    return line


def _implementation(report: ImplementationReport) -> str:
    tests = reported_tests(report.tests)
    parts = [_count(len(report.commits), "commit")]
    if tests.passed:
        parts.append(f"{_count(tests.passed, 'test')} passed")
    if tests.failed:
        parts.append(f"{tests.failed} failed")
    return "Implementer: " + ", ".join(parts)


def _verdict(report: Verdict) -> str:
    counts = verdict_counts(report)
    line = "Leader verdict: " + _DECISION_WORDS.get(counts.decision, counts.decision)
    if counts.blocker:
        line += f" ({_count(counts.blocker, 'blocker')})"
    return line


def _turn_line(event: Event, reports: Mapping[str, Report]) -> str:
    role = str(event.data.get(ROLE_FIELD, "agent")).capitalize()
    if event.kind is EventKind.TURN_FAILED:
        message = one_line(event.message, FAILURE_MESSAGE_LIMIT) or "no message"
        return f"{role}: turn failed ({message})"
    report = reports.get(str(event.data.get(TURN_FIELD)))
    if isinstance(report, ImplementationReport):
        return _implementation(report)
    if isinstance(report, Verdict):
        return _verdict(report)
    step = event.data.get(STEP_FIELD)
    return f"{role}: {step} {event.data.get(OUTCOME_FIELD)}"


def _turn_lines(events: Sequence[Event], reports: Mapping[str, Report], room: int) -> list[str]:
    lines = [
        _turn_line(event, reports)
        for event in events
        if event.kind in (EventKind.TURN_COMPLETED, EventKind.TURN_FAILED)
    ]
    if len(lines) <= room:
        return lines
    kept = max(room - 1, 0)
    hidden = len(lines) - kept
    return [EARLIER_TEMPLATE.format(count=hidden), *lines[len(lines) - kept :]][:room]


def _produced(facts: str) -> str | None:
    produced = parse_produced(facts)
    if produced is None:
        return None
    return (
        f"Produced: {_count(produced.commits, 'commit')}, {_count(produced.files, 'file')}, "
        f"+{produced.insertions} -{produced.deletions}"
    )


def summarise_attempts(
    reason: str,
    events: Sequence[Event],
    reports: Mapping[str, Report],
    task: Task,
    facts: str,
    *,
    max_iterations: int,
    limit: int,
) -> list[str]:
    """Return at most `limit` chronological lines of what `task` did, `reason` first.

    Rounds, the latest turns with their outcome, recovery, produced numbers and Operator input
    appear only when recorded; the oldest turns collapse into a `+N earlier` line.
    """
    counts = Counter(event.kind for event in events)
    recovery = _counted(counts, _RECOVERY_NOUNS)
    operator = _counted(counts, _OPERATOR_NOUNS)
    leading = [_rounds(events, task, max_iterations)]
    trailing = [
        f"Recovery: {recovery}" if recovery else None,
        _produced(facts),
        f"Operator: {operator}" if operator else None,
    ]
    head = [reason, *(line for line in leading if line)]
    tail = [line for line in trailing if line]
    room = limit - len(head) - len(tail)
    turns = _turn_lines(events, reports, max(room, 0))
    return [*head, *turns, *tail][:limit]
