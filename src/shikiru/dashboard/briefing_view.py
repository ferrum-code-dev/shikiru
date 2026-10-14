"""The task briefing: header, summary rows and action verbs rendered from one `TaskDetailView`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from rich.cells import cell_len
from rich.padding import Padding
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from shikiru.agents.contracts import (
    BILLING_MODE_SUBSCRIPTION,
    COST_BASIS_API_EQUIVALENT,
    COST_BASIS_BILLED,
)
from shikiru.domain.briefing import (
    BriefingAction,
    ProducedNumbers,
    PullRequestLine,
    ReportedTests,
    VerdictCounts,
    briefing_actions,
    one_line,
    rework_progress,
)
from shikiru.domain.scheduling import WaitReason
from shikiru.domain.tasks import Hold, Role, Status
from shikiru.ui.compact import ROLE_LABELS, StageActivity, duration_text, recorded_age
from shikiru.ui.glyphs import GLYPH_CURRENT_STAGE, GLYPH_STALLED, ROLE_GLYPHS
from shikiru.ui.section import (
    BOLD_PRIMARY,
    GROUP_SEPARATOR,
    LABEL_GAP,
    STACKED_BELOW_WIDTH,
    Row,
    Section,
    grey,
    labelled_rows,
)
from shikiru.ui.strip import PanelState
from shikiru.ui.tokens import (
    ALERT_COLOR,
    AMBER,
    ENGINE_COLOR,
    HOLD_COLOR,
    IDLE_COLOR,
    NEUTRAL_BORDER_COLOR,
    ROLE_COLORS,
    SAGE,
    SECONDARY_TEXT_COLOR,
    STYLE_BOLD,
    SUCCESS_COLOR,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from rich.console import Console, ConsoleOptions, RenderableType, RenderResult

    from shikiru.dashboard.contract import TaskDetailView, TaskSessionView

NEEDED_TITLE = "Needed from you"
NEEDS_YOU_PREFIX = "Needs you: "
NOW_LABEL = "Now"
PRODUCED_LABEL = "Produced"
VERDICT_LABEL = "Verdict"
PR_LABEL = "PR"
COST_LABEL = "Cost"
LABEL_WIDTH = len(PRODUCED_LABEL)
ACTIVE_WORD = "active"
STALLED_WORD = "stalled"
IN_STEP_WORD = "in step"
TOTAL_WORD = "total"
REWORK_WORD = "rework"
NEEDED_INDENT = 2
QUOTE = '"'
QUOTE_PAIR_WIDTH = len(QUOTE) * 2
MINUS = "\N{MINUS SIGN}"
PASSED_MARK = "\N{CHECK MARK}"
FAILED_MARK = "\N{BALLOT X}"
TESTS_WORD = "tests"
AGENT_REPORTED = "(agent-reported)"
WAITING_ON = "Waiting on"
NO_QUESTION = "Escalation open; no validated decision question is recorded."
ESCALATION_NEAR = "escalates after {count} more {noun}"
REJECTION_NOUNS = ("rejection", "rejections")
ROLE_SHORT_LABELS = {Role.IMPLEMENTER.value: "Impl"}
VERDICT_LABELS = {
    "accept": "Accepted",
    "changes_requested": "Rejected",
    "escalate": "Escalated",
    "supersede": "Superseded",
}
MIXED_COST_BASIS = "mixed basis"
BILLED_WORD = "billed"
ESTIMATE_WORD = "API est."
SUBSCRIPTION_WORD = "subscription"
COST_FORMAT = ".2f"
ACTION_LABELS = {
    BriefingAction.TAKE_OVER: "Take over",
    BriefingAction.HAND_BACK: "Hand back",
    BriefingAction.MESSAGE: "Message",
    BriefingAction.PAUSE: "Pause",
    BriefingAction.RESUME: "Resume",
    BriefingAction.ANSWER: "Answer",
}
DETAILS_LABEL = "Enter details \N{BLACK RIGHT-POINTING SMALL TRIANGLE}"
COMPACT_DETAILS_LABEL = "Enter \N{BLACK RIGHT-POINTING SMALL TRIANGLE}"
TERMINAL_STATE_LABELS = {
    Status.ESCALATED.value: ("!", "Escalated"),
    Status.DONE.value: ("\N{CHECK MARK}", "Done"),
    Status.CANCELLED.value: ("\N{WHITE CIRCLE}", "Cancelled"),
    Status.QUEUED.value: ("\N{MIDDLE DOT}", "Queued"),
}
ACTIVE_STAGE_LABELS = {
    "triage": "Triaging",
    "plan": "Planning",
    "implement": "Implementing",
    "consult": "Consulting",
    "verify": "Verifying",
    "integrate": "Integrating",
}
_ROLE_GLYPHS = {role.value: glyph for role, glyph in ROLE_GLYPHS.items()}
_WORKING_PANEL_STATES = frozenset({PanelState.ACTIVE, PanelState.STALLED})
_TERMINAL_STATUSES = frozenset({Status.DONE.value, Status.CANCELLED.value})
_RUN_STATUSES = frozenset({Status.RUNNING.value, Status.INTEGRATING.value})
_STATUS_COLORS = {
    Status.DONE.value: SUCCESS_COLOR,
    Status.CANCELLED.value: IDLE_COLOR,
    Status.QUEUED.value: IDLE_COLOR,
}
_BOLD_AMBER = f"{STYLE_BOLD} {AMBER}"


@dataclass(frozen=True)
class BriefingLayout:
    """How much room the briefing has: compact below the wide mockup, tiny when rows run out."""

    compact: bool = False
    tiny: bool = False


def briefing_verbs(view: TaskDetailView) -> tuple[BriefingAction, ...]:
    """Return the verbs the actions row offers for `view`."""
    return briefing_actions(Status(view.status), view.holds, operator_owned=bool(view.ownership))


def needed_from_you(view: TaskDetailView) -> str | None:
    """Return what the operator must act on: a decision question first, else a wait reason."""
    if view.status == Status.ESCALATED.value:
        question = next(
            (
                report.question
                for report in (view.latest_report, view.latest_verdict)
                if report is not None and report.question
            ),
            None,
        )
        return _sentence(question) if question is not None else NO_QUESTION
    blocker_quota = view.blocker is not None and view.blocker.kind == WaitReason.QUOTA.value
    reason = view.wait_reason or (WaitReason.QUOTA.value if blocker_quota else None)
    if reason is None or reason == WaitReason.PAUSED.value:
        return None
    return _sentence(f"{WAITING_ON} {reason.replace('_', ' ')}")


def header_text(view: TaskDetailView, now: datetime, *, compact: bool) -> Text:
    """Return the identity line, the state line and, when wide, the time line."""
    header = Text(f"{view.task_id} · {view.title}", style=BOLD_PRIMARY)
    header.append("\n")
    header.append_text(_state_line(view, now, compact=compact))
    time_line = None if compact else _time_line(view, now)
    if time_line is not None:
        header.append("\n")
        header.append_text(time_line)
    return header


@dataclass(frozen=True)
class Briefing:
    """Render a task's summary rows for the width they are drawn at."""

    view: TaskDetailView
    now: datetime
    layout: BriefingLayout

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        """Yield the milestones and the needed, working and produced blocks."""
        del console
        width = options.max_width
        compact = self.layout.compact
        milestones = self._milestones()
        needed = self._needed(width)
        facts = self._facts(width)
        blocks = [needed, facts, milestones] if self.layout.tiny else [milestones, needed, facts]
        shown = [block for block in blocks if block]
        for index, block in enumerate(shown):
            if index and not compact:
                yield Rule(style=NEUTRAL_BORDER_COLOR)
            yield from block

    def _milestones(self) -> list[RenderableType]:
        if not self.view.milestones:
            return []
        return [StageActivity(self.view).render(glyphs_only=self.layout.compact)]

    def _needed(self, width: int) -> list[RenderableType]:
        text = needed_from_you(self.view)
        if text is None:
            return []
        if self.layout.compact:
            return [_hanging(NEEDS_YOU_PREFIX, text, width)]
        body = Padding(Text(text), (0, 0, 0, NEEDED_INDENT))
        return [Section(NEEDED_TITLE, body, ruled=False, title_style=_BOLD_AMBER)]

    def _facts(self, width: int) -> list[RenderableType]:
        if self.layout.compact:
            return self._compact_facts(width)
        value_width = width if width < STACKED_BELOW_WIDTH else width - LABEL_WIDTH - LABEL_GAP
        rows = self._wide_rows(value_width)
        return [labelled_rows(rows, width, LABEL_WIDTH)] if rows else []

    def _wide_rows(self, value_width: int) -> list[Row]:
        view = self.view
        rows: list[Row] = []
        if _working(view):
            rows.append((grey(NOW_LABEL), (_activity_text(view, self.now, compact=False),)))
            if view.last_words is not None:
                rows.append((Text(), (Text(_quote(view.last_words, value_width)),)))
            rows.extend(_escalation_row(view))
        rows.extend(_produced_rows(view, value_width))
        verdict = _verdict_groups(view.verdict_counts)
        if verdict:
            rows.append((grey(VERDICT_LABEL), verdict))
        pull_request = _pull_request_groups(view.pull_request)
        if pull_request:
            rows.append((grey(PR_LABEL), pull_request))
        cost = _cost_groups(view.sessions, compact=False)
        if cost:
            rows.append((grey(COST_LABEL), cost))
        return rows

    def _compact_facts(self, width: int) -> list[RenderableType]:
        view = self.view
        lines: list[RenderableType] = []
        if _working(view):
            lines.append(_compact_activity(view, self.now, width))
            lines.extend(row[1][0] for row in _escalation_row(view))
        summary = _terminal_summary(view, width)
        produced = _compact_produced(view)
        if produced is not None:
            lines.append(produced)
        if summary is not None:
            lines.append(summary)
        verdict = _verdict_groups(view.verdict_counts)
        if verdict:
            lines.append(_joined(verdict))
        pull_request = _pull_request_groups(view.pull_request)
        if pull_request:
            lines.append(
                Text.assemble((f"{PR_LABEL} ", SECONDARY_TEXT_COLOR), _joined(pull_request))
            )
        cost = _cost_groups(view.sessions, compact=True)
        if cost:
            lines.append(_joined(cost))
        return lines


def _sentence(value: str) -> str:
    sentence = value.strip().splitlines()[0].strip()
    return sentence if sentence.endswith((".", "!", "?")) else f"{sentence}."


def _hanging(prefix: str, text: str, width: int) -> RenderableType:
    grid = Table.grid()
    grid.add_column(width=cell_len(prefix), no_wrap=True)
    grid.add_column(width=max(width - cell_len(prefix), 1))
    grid.add_row(Text(prefix, style=_BOLD_AMBER), Text(text, style=AMBER))
    return grid


def _joined(groups: Sequence[Text]) -> Text:
    result = Text()
    for index, group in enumerate(groups):
        if index:
            result.append(GROUP_SEPARATOR, style=SECONDARY_TEXT_COLOR)
        result.append_text(group)
    return result


def _ellipsized(text: str, limit: int) -> str:
    clipped = Text(text)
    clipped.truncate(max(limit, 1), overflow="ellipsis")
    return clipped.plain


def _working(view: TaskDetailView) -> bool:
    return (
        view.status in _RUN_STATUSES
        and not view.ownership
        and any(panel.state in _WORKING_PANEL_STATES for panel in view.progress.panels)
    )


def _stalled(view: TaskDetailView) -> bool:
    return any(panel.state is PanelState.STALLED for panel in view.progress.panels)


def _activity_age(view: TaskDetailView, now: datetime, *, compact: bool) -> str | None:
    if view.latest_signal_at is None:
        return None
    if compact:
        return duration_text(now - view.latest_signal_at)
    return recorded_age(view.latest_signal_at, now)


def _activity_text(view: TaskDetailView, now: datetime, *, compact: bool) -> Text:
    age = _activity_age(view, now, compact=compact)
    text = grey(ACTIVE_WORD if age is None else f"{ACTIVE_WORD} {age}")
    if _stalled(view):
        text.append(f"{GROUP_SEPARATOR}{GLYPH_STALLED} {STALLED_WORD}", style=AMBER)
    return text


def _quote(words: str, limit: int) -> str:
    return QUOTE + _ellipsized(words, limit - QUOTE_PAIR_WIDTH) + QUOTE


def _compact_activity(view: TaskDetailView, now: datetime, width: int) -> Text:
    age = _activity_age(view, now, compact=True)
    suffix = Text(f" {age}" if age is not None else "", style=SECONDARY_TEXT_COLOR)
    if _stalled(view):
        suffix.append(f" {GLYPH_STALLED} {STALLED_WORD}", style=AMBER)
    if view.last_words is None:
        return Text.assemble(grey(ACTIVE_WORD), suffix)
    line = grey(_quote(view.last_words, width - cell_len(suffix.plain)))
    line.append_text(suffix)
    return line


def _escalation_row(view: TaskDetailView) -> list[Row]:
    remaining = view.escalation_remaining
    if remaining is None:
        return []
    noun = REJECTION_NOUNS[0] if remaining == 1 else REJECTION_NOUNS[1]
    return [(Text(), (grey(ESCALATION_NEAR.format(count=remaining, noun=noun)),))]


def _counted(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def _lines(produced: ProducedNumbers) -> str:
    return f"+{produced.insertions} {MINUS}{produced.deletions}"


def _produced_groups(produced: ProducedNumbers | None) -> tuple[Text, ...]:
    if produced is None:
        return ()
    return (
        Text(_counted(produced.commits, "commit")),
        Text(_counted(produced.files, "file")),
        Text(_lines(produced)),
    )


def _produced_rows(view: TaskDetailView, limit: int) -> list[Row]:
    values: list[tuple[Text, ...]] = []
    produced = _produced_groups(view.produced)
    if produced:
        values.append(produced)
    tests = _tests_text(view.reported_tests)
    if tests is not None:
        values.append((tests,))
    summary = _terminal_summary(view, limit)
    if summary is not None:
        values.append((summary,))
    return [
        (grey(PRODUCED_LABEL) if index == 0 else Text(), groups)
        for index, groups in enumerate(values)
    ]


def _tests_text(tests: ReportedTests | None) -> Text | None:
    if tests is None:
        return None
    counts = ((tests.passed, "passed"), (tests.failed, "failed"), (tests.skipped, "skipped"))
    parts = [f"{count} {word}" for count, word in counts if count]
    if not parts:
        return None
    return grey(f"{TESTS_WORD} {GROUP_SEPARATOR.join(parts)} {AGENT_REPORTED}")


def _compact_tests(tests: ReportedTests | None) -> str | None:
    if tests is None or not (tests.passed or tests.failed):
        return None
    marks = ((tests.passed, PASSED_MARK), (tests.failed, FAILED_MARK))
    return " ".join(f"{count}{mark}" for count, mark in marks if count)


def _compact_produced(view: TaskDetailView) -> Text | None:
    produced = view.produced
    tests = _compact_tests(view.reported_tests)
    if produced is None and tests is None:
        return None
    line = Text()
    if produced is not None:
        line.append(f"{_counted(produced.commits, 'commit')} {_lines(produced)}")
    if tests is not None:
        if produced is not None:
            line.append(GROUP_SEPARATOR, style=SECONDARY_TEXT_COLOR)
        line.append(tests, style=SECONDARY_TEXT_COLOR)
    return line


def _terminal_summary(view: TaskDetailView, limit: int) -> Text | None:
    if view.status not in _TERMINAL_STATUSES or view.latest_report is None:
        return None
    summary = one_line(view.latest_report.summary)
    return grey(_ellipsized(summary, limit)) if summary is not None else None


def _verdict_groups(counts: VerdictCounts | None) -> tuple[Text, ...]:
    if counts is None:
        return ()
    severities = (
        (counts.blocker, "blocker"),
        (counts.major, "major"),
        (counts.minor, "minor"),
    )
    return (
        Text(VERDICT_LABELS.get(counts.decision, counts.decision)),
        *(Text(f"{count} {word}") for count, word in severities if count),
    )


def _pull_request_groups(pull_request: PullRequestLine | None) -> tuple[Text, ...]:
    if pull_request is None:
        return ()
    state = pull_request.state.lower()
    number = f"#{pull_request.number} " if pull_request.number is not None else ""
    groups = [Text(f"{number}{state}")]
    if pull_request.checks_pending:
        groups.append(Text("checks pending"))
    if pull_request.checks_failing:
        groups.append(Text("checks failing", style=AMBER))
    return tuple(groups)


def _cost_groups(sessions: Sequence[TaskSessionView], *, compact: bool) -> tuple[Text, ...]:
    amounts: dict[str, float] = {}
    bases: set[str] = set()
    for session in sessions:
        if session.cost_usd is not None:
            amounts[session.role] = amounts.get(session.role, 0.0) + session.cost_usd
            bases.add(session.cost_basis)
    if not amounts:
        subscribed = any(session.billing_mode == BILLING_MODE_SUBSCRIPTION for session in sessions)
        return (Text(SUBSCRIPTION_WORD, style=SAGE),) if subscribed else ()
    word, style = _cost_qualifier(bases)
    if compact:
        return (Text(f"${sum(amounts.values()):{COST_FORMAT}} {word}", style=style),)
    labels = [
        f"{ROLE_SHORT_LABELS.get(role, label)} ${amounts[role]:{COST_FORMAT}}"
        for role, label in ROLE_LABELS.items()
        if role in amounts
    ]
    labels[-1] = f"{labels[-1]} {word}"
    return tuple(Text(label, style=style) for label in labels)


def _cost_qualifier(bases: set[str]) -> tuple[str, str]:
    if bases == {COST_BASIS_BILLED}:
        return BILLED_WORD, SAGE
    if bases == {COST_BASIS_API_EQUIVALENT}:
        return ESTIMATE_WORD, AMBER
    return MIXED_COST_BASIS, SECONDARY_TEXT_COLOR


def _time_line(view: TaskDetailView, now: datetime) -> Text | None:
    parts: list[str] = []
    if view.actual_stage is not None and view.actual_stage_started_at is not None:
        end = view.stage_ended_at or now
        parts.append(f"{duration_text(end - view.actual_stage_started_at)} {IN_STEP_WORD}")
    if view.started_at is not None and view.status not in _TERMINAL_STATUSES:
        parts.append(f"{duration_text(now - view.started_at)} {TOTAL_WORD}")
    if not parts:
        return None
    return grey(f"   {GROUP_SEPARATOR.join(parts)}")


def _state_line(view: TaskDetailView, now: datetime, *, compact: bool) -> Text:
    marker, label = _marker_and_label(view)
    line = Text()
    line.append(f"{marker} {label}", style=f"{STYLE_BOLD} {_state_color(view)}")
    tail: list[str] = []
    if compact:
        rework = rework_progress(view.iteration, view.max_iterations)
        if rework is not None:
            tail.append(f"{REWORK_WORD} {rework[0]}/{rework[1]}")
        if view.actual_stage is not None and view.actual_stage_started_at is not None:
            end = view.stage_ended_at or now
            tail.append(duration_text(end - view.actual_stage_started_at))
    else:
        if view.actual_stage is not None:
            tail.append(ROLE_LABELS.get(view.acting_role, view.acting_role.title()))
        rework = rework_progress(view.iteration, view.max_iterations)
        if rework is not None:
            tail.append(f"{REWORK_WORD} {rework[0]} of {rework[1]}")
    for part in tail:
        line.append(GROUP_SEPARATOR, style=SECONDARY_TEXT_COLOR)
        line.append(part, style=SECONDARY_TEXT_COLOR)
    return line


def _marker_and_label(view: TaskDetailView) -> tuple[str, str]:
    terminal = TERMINAL_STATE_LABELS.get(view.status)
    if view.actual_stage is not None:
        state = ACTIVE_STAGE_LABELS.get(view.actual_stage, view.actual_stage.title())
    elif terminal is not None:
        state = terminal[1]
    else:
        state = view.status.title()
    labels: list[str] = []
    if view.status == Status.ESCALATED.value:
        labels.append("Decision hold")
    labels.extend(_hold_label(hold) for hold in view.holds)
    owned = bool(view.ownership) and view.status in _RUN_STATUSES and view.actual_stage is not None
    if owned:
        labels.append("Operator-owned")
    if labels:
        marker = "@" if owned else "=" if view.holds else "!"
        return marker, GROUP_SEPARATOR.join((*labels, state))
    if terminal is not None:
        return terminal[0], state
    return _live_marker(view), state


def _live_marker(view: TaskDetailView) -> str:
    working = next(
        (panel for panel in view.progress.panels if panel.state in _WORKING_PANEL_STATES), None
    )
    if working is not None and working.state is PanelState.STALLED:
        return GLYPH_STALLED
    if working is not None and working.spinner_frame:
        return working.spinner_frame
    if view.actual_stage is not None:
        return _ROLE_GLYPHS.get(view.acting_role, GLYPH_CURRENT_STAGE)
    return GLYPH_CURRENT_STAGE


def _hold_label(hold: str) -> str:
    if hold == Hold.PAUSED.value:
        return "Paused"
    if hold == Hold.INTERVENTION.value:
        return "Intervention hold"
    return f"{hold.replace('_', ' ').title()} hold"


def _state_color(view: TaskDetailView) -> str:
    if view.status == Status.ESCALATED.value:
        return ALERT_COLOR
    if view.holds:
        return HOLD_COLOR
    return _STATUS_COLORS.get(view.status) or _live_color(view)


def _live_color(view: TaskDetailView) -> str:
    active = next((p for p in view.progress.panels if p.state is PanelState.ACTIVE), None)
    if active is not None:
        return ROLE_COLORS[active.role] if active.role is not None else ENGINE_COLOR
    return HOLD_COLOR if _stalled(view) else SECONDARY_TEXT_COLOR
