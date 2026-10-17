"""Structured, toned rows for the reports and recent turn signals of an escalation packet."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, cast

from pydantic import TypeAdapter, ValidationError

from shikiru.agents.contracts import (
    Compacted,
    QuotaReported,
    SessionStarted,
    UsageReported,
)
from shikiru.domain.reports import (
    ConsultAnswer,
    EscalationRequest,
    ImplementationReport,
    NewTask,
    PlanReport,
    Report,
    TriageReport,
    Verdict,
)
from shikiru.domain.timeline_phrases import (
    PHRASE_SEPARATOR,
    SIGNAL_MESSAGE,
    SIGNAL_TOOL_FINISHED,
    SIGNAL_TOOL_STARTED,
    SIGNAL_TURN_ENDED,
    SIGNAL_TURN_FAILED,
    Tone,
    signal_phrase,
    tool_target,
    verdict_phrase,
)
from shikiru.view_log import VIEW_RUNG_KEY, VIEW_SIGNAL_KEY, VIEW_TIME_KEY, VIEW_TURN_KEY

if TYPE_CHECKING:
    from collections.abc import Sequence

    from shikiru.domain.reports import Commit, Issue, Option, TestRun

_REPORT: TypeAdapter[Report] = TypeAdapter(Report)
_LABEL_OPEN = "**"
_LABEL_CLOSE = "**\n\n"
_BLOCK_SEPARATOR = "\n\n"
_LABEL_SEPARATOR = "-"
_SIGNAL_TIME_FORMAT = "%H:%M:%S"
_RECOMMENDED_STAR = "\N{BLACK STAR} "
_LOCATION_SEPARATOR = " @ "
_RUNG_SEPARATOR = " \N{MIDDLE DOT} "
_FIRST_LINE_SEPARATOR = "\n"
_OLD_SCHEMA_INDENT = 2
_REPORT_LABEL = "Report"
_TEXT_LABEL = "Text"
_LINE_LABEL = "Line"
_STATUS_TONES = {"completed": Tone.SUCCESS, "needs_decision": Tone.WARNING, "blocked": Tone.FAILURE}
_RESULT_TONES = {"passed": Tone.SUCCESS, "failed": Tone.FAILURE}
_SEVERITY_TONES = {"blocker": Tone.FAILURE, "major": Tone.WARNING}
_SIGNAL_KINDS = {
    SIGNAL_MESSAGE: "message",
    SIGNAL_TOOL_STARTED: "tool",
    SIGNAL_TOOL_FINISHED: "tool done",
    SIGNAL_TURN_ENDED: "turn end",
    SIGNAL_TURN_FAILED: "failed",
    UsageReported.__name__: "usage",
    SessionStarted.__name__: "session",
    QuotaReported.__name__: "quota",
    Compacted.__name__: "compacted",
}
_REPORT_KINDS = {
    TriageReport: "triage",
    ImplementationReport: "implement",
    PlanReport: "plan",
    ConsultAnswer: "consult",
    Verdict: "verify",
}


@dataclass(frozen=True)
class Part:
    """A run of text in one tone, optionally in the accent colour."""

    text: str
    tone: Tone = Tone.PLAIN
    accent: bool = False


@dataclass(frozen=True)
class DetailRow:
    """One labelled line of a details block; an empty label continues the row above."""

    label: str
    parts: tuple[Part, ...]


@dataclass(frozen=True)
class EvidenceEntry:
    """One table row (lead, kind, one-line outcome) and the structured block that explains it."""

    lead: str
    kind: str
    outcome: Part
    details: tuple[DetailRow, ...]


def _raw(label: str, text: str) -> tuple[DetailRow, ...]:
    return (DetailRow(label, (Part(text),)),)


def _row(label: str, text: str, tone: Tone = Tone.PLAIN) -> list[DetailRow]:
    return [DetailRow(label, (Part(text, tone),))] if text else []


def _listed(label: str, items: Sequence[tuple[Part, ...]]) -> list[DetailRow]:
    return [DetailRow(label if index == 0 else "", parts) for index, parts in enumerate(items)]


def _plain_items(label: str, items: Sequence[str]) -> list[DetailRow]:
    return _listed(label, [(Part(item),) for item in items])


def _counted(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _commit(commit: Commit) -> tuple[Part, ...]:
    return (Part(commit.sha, accent=True), Part(f" {commit.subject}"))


def _test(test: TestRun) -> tuple[Part, ...]:
    parts = [Part(f"{test.name} "), Part(test.result, _RESULT_TONES.get(test.result, Tone.PLAIN))]
    if test.details:
        parts.append(Part(f" {test.details}"))
    return tuple(parts)


def _issue(issue: Issue) -> tuple[Part, ...]:
    parts = [
        Part(issue.severity, _SEVERITY_TONES.get(issue.severity, Tone.PLAIN)),
        Part(f" {issue.description}"),
    ]
    if issue.location:
        parts.append(Part(f"{_LOCATION_SEPARATOR}{issue.location}"))
    return tuple(parts)


def _option(option: Option) -> tuple[Part, ...]:
    star = _RECOMMENDED_STAR if option.recommended else ""
    return (
        Part(f"{star}{option.label}", accent=option.recommended),
        Part(f" {option.consequences}"),
    )


def _new_task(task: NewTask) -> tuple[Part, ...]:
    return (Part(task.title),)


def _escalation(request: EscalationRequest | None) -> list[DetailRow]:
    if request is None:
        return []
    return [
        *_row("Escalation question", request.question),
        *_listed("Escalation options", [_option(option) for option in request.options]),
    ]


def _implementation(report: ImplementationReport) -> tuple[Part, list[DetailRow]]:
    tone = _STATUS_TONES[report.status]
    status = report.status.replace("_", " ")
    failed = sum(_RESULT_TONES.get(test.result) is Tone.FAILURE for test in report.tests)
    bits = [status]
    if report.commits:
        bits.append(_counted(len(report.commits), "commit"))
    if failed:
        bits.append(f"{failed} failed")
    return Part(PHRASE_SEPARATOR.join(bits), tone), [
        *_row("Status", status, tone),
        *_row("Summary", report.summary),
        *_listed("Commits", [_commit(commit) for commit in report.commits]),
        *_listed("Tests", [_test(test) for test in report.tests]),
        *_plain_items("Risks", report.risks),
        *_row("Question", report.question),
    ]


def _verdict(report: Verdict) -> tuple[Part, list[DetailRow]]:
    phrase = verdict_phrase(report.decision)
    text, tone = (phrase.text, phrase.tone) if phrase is not None else (report.decision, Tone.PLAIN)
    outcome = f"{text} ({_counted(len(report.issues), 'issue')})" if report.issues else text
    return Part(outcome, tone), [
        *_row("Decision", text, tone),
        *_row("Summary", report.summary),
        *_listed("Issues", [_issue(issue) for issue in report.issues]),
        *_plain_items("Residual risks", report.residual_risks),
        *_listed("New tasks", [_new_task(task) for task in report.new_tasks]),
        *_plain_items("Scope changes", report.scope_changes),
        *_escalation(report.escalation),
    ]


def _triage(report: TriageReport) -> tuple[Part, list[DetailRow]]:
    return Part(report.title), [
        *_row("Title", report.title),
        *_row("Mode", report.mode.value),
        *_row("Complexity", report.complexity),
        *_plain_items("Acceptance criteria", report.acceptance_criteria),
        *_plain_items("References", report.references),
    ]


def _plan(report: PlanReport) -> tuple[Part, list[DetailRow]]:
    return Part(report.summary), [
        *_row("Summary", report.summary),
        *_listed("Commits", [_commit(commit) for commit in report.commits]),
        *_listed("New tasks", [_new_task(task) for task in report.new_tasks]),
        *_plain_items("Scope changes", report.scope_changes),
    ]


def _consult(report: ConsultAnswer) -> tuple[Part, list[DetailRow]]:
    tone = Tone.WARNING if report.escalation is not None else Tone.PLAIN
    return Part(report.answer or report.decision, tone), [
        *_row("Decision", report.decision, tone),
        *_row("Answer", report.answer),
        *_escalation(report.escalation),
    ]


def _report_content(report: Report) -> tuple[Part, list[DetailRow]]:
    match report:
        case ImplementationReport():
            return _implementation(report)
        case Verdict():
            return _verdict(report)
        case TriageReport():
            return _triage(report)
        case PlanReport():
            return _plan(report)
        case ConsultAnswer():
            return _consult(report)


def _first_line(text: str) -> str:
    return text.strip().partition(_FIRST_LINE_SEPARATOR)[0]


def _readable_json(text: str) -> str:
    try:
        return json.dumps(json.loads(text), indent=_OLD_SCHEMA_INDENT, ensure_ascii=False)
    except json.JSONDecodeError:
        return text


def _label_parts(label: str) -> tuple[str, str]:
    number, separator, rest = label.partition(_LABEL_SEPARATOR)
    if not (separator and number.isdigit()):
        return "", label
    return number, rest.partition(_LABEL_SEPARATOR)[0]


def _report_entry(label: str, body: str) -> EvidenceEntry:
    lead, step = _label_parts(label)
    try:
        report = _REPORT.validate_json(body)
    except ValidationError:
        return EvidenceEntry(
            lead, step, Part(_first_line(body)), _raw(_REPORT_LABEL, _readable_json(body))
        )
    outcome, details = _report_content(report)
    return EvidenceEntry(lead, step or _REPORT_KINDS[type(report)], outcome, tuple(details))


def _plain_entry(text: str) -> EvidenceEntry:
    return EvidenceEntry("", "", Part(_first_line(text)), _raw(_TEXT_LABEL, text.strip()))


def parse_reports(content: str) -> tuple[EvidenceEntry, ...]:
    """Read the packet's `**label**` + JSON report blocks; unreadable ones stay as readable text."""
    entries: list[EvidenceEntry] = []
    for block in content.split(_BLOCK_SEPARATOR + _LABEL_OPEN):
        label, separator, body = block.removeprefix(_LABEL_OPEN).partition(_LABEL_CLOSE)
        entries.append(_report_entry(label, body) if separator else _plain_entry(block))
    return tuple(entries)


def _signal_time(value: object) -> str:
    try:
        return datetime.fromisoformat(str(value)).strftime(_SIGNAL_TIME_FORMAT)
    except ValueError:
        return ""


def _signal_details(signal: str, data: dict[str, object], tone: Tone) -> list[DetailRow]:
    if signal == SIGNAL_MESSAGE:
        return _row("Message", str(data.get("text") or ""))
    if signal == SIGNAL_TURN_ENDED:
        return _row("Last message", str(data.get("last_message") or ""))
    if signal == SIGNAL_TOOL_STARTED:
        return [
            *_row("Tool", str(data.get("name") or "")),
            *_row("Detail", tool_target(str(data.get("detail") or ""))),
        ]
    if signal == SIGNAL_TOOL_FINISHED:
        exit_code = data.get("exit_code")
        result = PHRASE_SEPARATOR.join(
            str(part) for part in (data.get("outcome"), exit_code) if part is not None
        )
        return [*_row("Tool", str(data.get("name") or "")), *_row("Result", result, tone)]
    if signal == SIGNAL_TURN_FAILED:
        return [
            *_row("Failure", str(data.get("kind") or ""), tone),
            *_row("Message", str(data.get("message") or "")),
        ]
    return []


def _rung(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    rung = cast("dict[str, object]", value)
    return _RUNG_SEPARATOR.join(
        str(rung[key]) for key in ("agent", "model", "effort") if rung.get(key)
    )


def _signal_entry(line: str) -> EvidenceEntry:
    try:
        decoded: object = json.loads(line)
    except json.JSONDecodeError:
        decoded = None
    if not isinstance(decoded, dict) or VIEW_SIGNAL_KEY not in decoded:
        return EvidenceEntry("", "", Part(line.strip()), _raw(_LINE_LABEL, line.strip()))
    data = cast("dict[str, object]", decoded)
    signal = str(data[VIEW_SIGNAL_KEY])
    phrase = signal_phrase(signal, data)
    tone = phrase.tone if phrase is not None else Tone.PLAIN
    kind = _SIGNAL_KINDS.get(signal, signal)
    details = [
        *_row("Signal", kind),
        *_row("Rung", _rung(data.get(VIEW_RUNG_KEY))),
        *_row("Turn", str(data.get(VIEW_TURN_KEY) or "")),
        *_signal_details(signal, data, tone),
    ]
    outcome = Part(phrase.text if phrase is not None else "", tone)
    return EvidenceEntry(_signal_time(data.get(VIEW_TIME_KEY)), kind, outcome, tuple(details))


def parse_signals(content: str) -> tuple[EvidenceEntry, ...]:
    """Read the packet's view.jsonl lines; a non-JSON or truncated line stays as a plain entry."""
    return tuple(_signal_entry(line) for line in content.splitlines() if line.strip())
