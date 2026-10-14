"""The capped digest of what a task did before it escalated (03 §8)."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from enum import StrEnum, auto
from typing import TYPE_CHECKING

from shikiru.domain.briefing import one_line, parse_produced, reported_tests, verdict_counts
from shikiru.domain.events import EventKind
from shikiru.domain.reports import ImplementationReport, Verdict
from shikiru.domain.tasks import Step

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
ROUNDS_SEPARATOR = " \N{MIDDLE DOT} "
MINUS = "\N{MINUS SIGN}"
EARLIER_TEMPLATE = "+{count} earlier"
LABEL_SEPARATOR = ": "
VERDICT_SUFFIX = " verdict"
TURN_FAILED_PREFIX = "turn failed"
LABEL_ROUNDS = "Rounds"
LABEL_RECOVERY = "Recovery"
LABEL_PRODUCED = "Produced"
LABEL_OPERATOR = "Operator"
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


class AttemptKind(StrEnum):
    """What one row of the escalation details shows."""

    REASON = auto()
    ROUNDS = auto()
    TURN_OK = auto()
    TURN_FAILED = auto()
    VERDICT = auto()
    RECOVERY = auto()
    PRODUCED = auto()
    OPERATOR = auto()
    EARLIER = auto()


class AttemptTone(StrEnum):
    """How a piece of a row reads: plain, a step name, success, failure or rework."""

    PLAIN = auto()
    STEP = auto()
    SUCCESS = auto()
    FAILURE = auto()
    REWORK = auto()


@dataclass(frozen=True)
class AttemptPart:
    """A run of row text with its tone."""

    text: str
    tone: AttemptTone


@dataclass(frozen=True)
class AttemptItem:
    """One row of the details: a kind, its label (a section or role name) and toned parts."""

    kind: AttemptKind
    label: str
    parts: tuple[AttemptPart, ...]

    @property
    def text(self) -> str:
        """The row text without its label."""
        return "".join(part.text for part in self.parts)

    @property
    def line(self) -> str:
        """The `Label: text` packet line of this row."""
        if self.kind in _UNLABELLED:
            return self.text
        label = self.label + VERDICT_SUFFIX if self.kind is AttemptKind.VERDICT else self.label
        return label + LABEL_SEPARATOR + self.text


_UNLABELLED = frozenset({AttemptKind.REASON, AttemptKind.EARLIER})
_SECTION_KINDS = {
    LABEL_ROUNDS: AttemptKind.ROUNDS,
    LABEL_RECOVERY: AttemptKind.RECOVERY,
    LABEL_PRODUCED: AttemptKind.PRODUCED,
    LABEL_OPERATOR: AttemptKind.OPERATOR,
}
_STEP_NAME = "|".join(step.value for step in Step)
_EARLIER = re.compile(r"\+\d+ earlier")
_TONES: dict[AttemptKind, tuple[tuple[re.Pattern[str], AttemptTone], ...]] = {
    AttemptKind.ROUNDS: (
        (re.compile(rf"\b(?:{_STEP_NAME})(?= {TIMES})"), AttemptTone.STEP),
        (re.compile(r"\(?iteration \d+ of \d+\)?"), AttemptTone.REWORK),
    ),
    AttemptKind.TURN_OK: (
        (re.compile(r"\d+ commits?|\d+ tests? passed|\baccept\b"), AttemptTone.SUCCESS),
        (re.compile(r"\d+ failed"), AttemptTone.FAILURE),
        (re.compile(rf"\b(?:{_STEP_NAME})\b"), AttemptTone.STEP),
    ),
    AttemptKind.TURN_FAILED: ((re.compile(r".+"), AttemptTone.FAILURE),),
    AttemptKind.VERDICT: (
        (re.compile(r"\baccept\b"), AttemptTone.SUCCESS),
        (re.compile(r"changes requested"), AttemptTone.REWORK),
        (re.compile(r"\d+ blockers?"), AttemptTone.FAILURE),
    ),
    AttemptKind.RECOVERY: ((re.compile(r".+"), AttemptTone.REWORK),),
    AttemptKind.PRODUCED: (
        (re.compile(r"\+\d+"), AttemptTone.SUCCESS),
        (re.compile(rf"[-{MINUS}]\d+"), AttemptTone.FAILURE),
    ),
}


def _toned(kind: AttemptKind, text: str) -> tuple[AttemptPart, ...]:
    marks = sorted(
        (match.start(), match.end(), tone)
        for pattern, tone in _TONES.get(kind, ())
        for match in pattern.finditer(text)
    )
    parts: list[AttemptPart] = []
    cursor = 0
    for start, end, tone in marks:
        if start < cursor:
            continue
        if start > cursor:
            parts.append(AttemptPart(text[cursor:start], AttemptTone.PLAIN))
        parts.append(AttemptPart(text[start:end], tone))
        cursor = end
    if cursor < len(text):
        parts.append(AttemptPart(text[cursor:], AttemptTone.PLAIN))
    return tuple(parts)


def _item(kind: AttemptKind, label: str, text: str) -> AttemptItem:
    return AttemptItem(kind, label, _toned(kind, text))


def parse_attempt_lines(content: str) -> tuple[AttemptItem, ...]:
    """Rebuild the details rows from a packet's `What was tried` text.

    The first line is the reason; a canned one-sentence packet is a single reason row.
    """
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if not lines:
        return ()
    return (_item(AttemptKind.REASON, "", lines[0]), *map(_parse_line, lines[1:]))


def _parse_line(line: str) -> AttemptItem:
    if _EARLIER.fullmatch(line):
        return _item(AttemptKind.EARLIER, "", line)
    label, separator, text = line.partition(LABEL_SEPARATOR)
    if not separator:
        return _item(AttemptKind.REASON, "", line)
    if label in _SECTION_KINDS:
        return _item(_SECTION_KINDS[label], label, text)
    if label.endswith(VERDICT_SUFFIX):
        return _item(AttemptKind.VERDICT, label.removesuffix(VERDICT_SUFFIX), text)
    failed = text.startswith(TURN_FAILED_PREFIX)
    return _item(AttemptKind.TURN_FAILED if failed else AttemptKind.TURN_OK, label, text)


def _count(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def _counted(counts: Counter[EventKind], nouns: tuple[tuple[EventKind, str], ...]) -> str:
    return ", ".join(_count(counts[kind], noun) for kind, noun in nouns if counts[kind])


def _rounds(events: Sequence[Event], task: Task, max_iterations: int) -> AttemptItem | None:
    steps = Counter(
        str(event.data.get(STEP_FIELD)) for event in events if event.kind is EventKind.STEP_STARTED
    )
    if not steps:
        return None
    text = ROUNDS_SEPARATOR.join(f"{step} {TIMES}{count}" for step, count in steps.items())
    if task.iteration > 0:
        text += f" (iteration {task.iteration} of {max_iterations})"
    return _item(AttemptKind.ROUNDS, LABEL_ROUNDS, text)


def _implementation(role: str, report: ImplementationReport) -> AttemptItem:
    tests = reported_tests(report.tests)
    parts = [_count(len(report.commits), "commit")]
    if tests.passed:
        parts.append(f"{_count(tests.passed, 'test')} passed")
    if tests.failed:
        parts.append(f"{tests.failed} failed")
    return _item(AttemptKind.TURN_OK, role, ", ".join(parts))


def _verdict(role: str, report: Verdict) -> AttemptItem:
    counts = verdict_counts(report)
    text = _DECISION_WORDS.get(counts.decision, counts.decision)
    if counts.blocker:
        text += f" ({_count(counts.blocker, 'blocker')})"
    return _item(AttemptKind.VERDICT, role, text)


def _turn_item(event: Event, reports: Mapping[str, Report]) -> AttemptItem:
    role = str(event.data.get(ROLE_FIELD, "agent")).capitalize()
    if event.kind is EventKind.TURN_FAILED:
        message = one_line(event.message, FAILURE_MESSAGE_LIMIT) or "no message"
        return _item(AttemptKind.TURN_FAILED, role, f"{TURN_FAILED_PREFIX} ({message})")
    report = reports.get(str(event.data.get(TURN_FIELD)))
    if isinstance(report, ImplementationReport):
        return _implementation(role, report)
    if isinstance(report, Verdict):
        return _verdict(role, report)
    step = event.data.get(STEP_FIELD)
    return _item(AttemptKind.TURN_OK, role, f"{step} {event.data.get(OUTCOME_FIELD)}")


def _turn_items(
    events: Sequence[Event], reports: Mapping[str, Report], room: int
) -> list[AttemptItem]:
    items = [
        _turn_item(event, reports)
        for event in events
        if event.kind in (EventKind.TURN_COMPLETED, EventKind.TURN_FAILED)
    ]
    if len(items) <= room:
        return items
    kept = max(room - 1, 0)
    earlier = _item(AttemptKind.EARLIER, "", EARLIER_TEMPLATE.format(count=len(items) - kept))
    return [earlier, *items[len(items) - kept :]][:room]


def _produced(facts: str) -> AttemptItem | None:
    produced = parse_produced(facts)
    if produced is None:
        return None
    text = (
        f"{_count(produced.commits, 'commit')}, {_count(produced.files, 'file')}, "
        f"+{produced.insertions} {MINUS}{produced.deletions}"
    )
    return _item(AttemptKind.PRODUCED, LABEL_PRODUCED, text)


def _section(kind: AttemptKind, label: str, text: str) -> AttemptItem | None:
    return _item(kind, label, text) if text else None


def attempt_lines(items: Sequence[AttemptItem]) -> str:
    """Return the packet text of `items`, one `Label: text` line per row."""
    return "\n".join(item.line for item in items)


def summarise_attempts(
    reason: str,
    events: Sequence[Event],
    reports: Mapping[str, Report],
    task: Task,
    facts: str,
    *,
    max_iterations: int,
    limit: int,
) -> tuple[AttemptItem, ...]:
    """Return at most `limit` chronological rows of what `task` did, `reason` first.

    Rounds, the latest turns with their outcome, recovery, produced numbers and Operator input
    appear only when recorded; the oldest turns collapse into a `+N earlier` row.
    """
    counts = Counter(event.kind for event in events)
    head = [
        _item(AttemptKind.REASON, "", reason),
        _rounds(events, task, max_iterations),
    ]
    tail = [
        _section(AttemptKind.RECOVERY, LABEL_RECOVERY, _counted(counts, _RECOVERY_NOUNS)),
        _produced(facts),
        _section(AttemptKind.OPERATOR, LABEL_OPERATOR, _counted(counts, _OPERATOR_NOUNS)),
    ]
    leading = [item for item in head if item is not None]
    trailing = [item for item in tail if item is not None]
    room = max(limit - len(leading) - len(trailing), 0)
    return (*leading, *_turn_items(events, reports, room), *trailing)[:limit]
