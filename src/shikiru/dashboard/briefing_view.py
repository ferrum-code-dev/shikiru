"""The task briefing: header, summary rows and action verbs rendered from one `TaskDetailView`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from rich.cells import cell_len
from rich.padding import Padding
from rich.table import Table
from rich.text import Text

from shikiru.agents.contracts import (
    BILLING_MODE_SUBSCRIPTION,
    COST_BASIS_API_EQUIVALENT,
    COST_BASIS_BILLED,
)
from shikiru.domain.briefing import (
    LAST_WORDS_ELLIPSIS,
    BriefingAction,
    PullRequestLine,
    ReportedTests,
    VerdictCounts,
    briefing_actions,
    rework_progress,
    squashed,
)
from shikiru.domain.quota_waits import quota_wait_description
from shikiru.domain.results import ResultOutcome
from shikiru.domain.scheduling import WaitReason
from shikiru.domain.tasks import Hold, Role, Status
from shikiru.ui.compact import ROLE_LABELS, StageActivity, duration_text, recorded_age
from shikiru.ui.glyphs import GLYPH_CURRENT_STAGE, GLYPH_STALLED, ROLE_GLYPHS
from shikiru.ui.section import (
    BOLD_PRIMARY,
    GROUP_SEPARATOR,
    LABEL_GAP,
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
    ROLE_COLORS,
    SAGE,
    SECONDARY_TEXT_COLOR,
    STEEL_BLUE,
    STOPPED_DURING,
    STYLE_BOLD,
    SUCCESS_COLOR,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from rich.console import Console, ConsoleOptions, RenderableType, RenderResult

    from shikiru.dashboard.contract import TaskDetailView, TaskSessionView
    from shikiru.domain.results import TaskResults

PROGRESS_TITLE = "Progress"
RESULT_SO_FAR_TITLE = "Result so far"
RESULT_TITLE = "Result"
PROBLEMS_TITLE = "Problems"
NEEDS_YOU_PREFIX = "Needs you: "
PENDING_PREFIX = "Pending: "
STEP_LABEL = "Step"
REWORK_LABEL = "Rework"
NOW_LABEL = "Now"
SUMMARY_LABEL = "Summary"
BRANCH_LABEL = "Branch"
MERGED_LABEL = "Merged"
COMMITS_LABEL = "Commits"
TESTS_LABEL = "Tests"
PR_LABEL = "PR"
VERDICT_LABEL = "Verdict"
BLOCKER_LABEL = "Blocker"
PENDING_LABEL = "Pending"
ADDED_LABEL = "Added"
LIVE_LABEL = "Live"
LIVE_UNAVAILABLE = "unavailable ({error}); showing recorded results"
RISKS_LABEL = "Risks"
COST_LABEL = "Cost"
LABEL_WIDTH = max(
    len(label)
    for label in (
        STEP_LABEL,
        REWORK_LABEL,
        NOW_LABEL,
        SUMMARY_LABEL,
        BRANCH_LABEL,
        MERGED_LABEL,
        COMMITS_LABEL,
        TESTS_LABEL,
        PR_LABEL,
        VERDICT_LABEL,
        BLOCKER_LABEL,
        PENDING_LABEL,
        ADDED_LABEL,
        RISKS_LABEL,
    )
)
GROUP_INDENT = 2
BRANCH_ARROW = "\N{RIGHTWARDS ARROW}"
KEPT_NOT_MERGED = "(kept, not merged)"
ACTIVE_WORD = "active"
STALLED_WORD = "stalled"
IN_STEP_WORD = "in step"
TOTAL_WORD = "total"
REWORK_WORD = "rework"
ADDED_WORD = "added"
COMPLEXITY_WORD = "complexity"
QUOTE = '"'
MINUS = "\N{MINUS SIGN}"
PASSED_MARK = "\N{CHECK MARK}"
FAILED_MARK = "\N{BALLOT X}"
REPORTED_BY_IMPLEMENTER = "reported by the Implementer"
WAITING_ON = "Waiting on"
NO_QUESTION = "Escalation open; no validated decision question is recorded."
ESCALATION_NEAR = "escalates after {count} more {noun}"
REJECTION_NOUNS = ("rejection", "rejections")
ROLE_SHORT_LABELS = {Role.IMPLEMENTER.value: "Impl"}
DECISION_COLORS = {
    "accept": SUCCESS_COLOR,
    "changes_requested": AMBER,
    "escalate": ALERT_COLOR,
}
PR_STATE_COLORS = {"open": STEEL_BLUE, "merged": SUCCESS_COLOR, "closed": ALERT_COLOR}
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
    BriefingAction.CANCEL: "Cancel",
}
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
TERMINAL_STATUSES = frozenset({Status.DONE.value, Status.CANCELLED.value})
_RUN_STATUSES = frozenset({Status.RUNNING.value, Status.INTEGRATING.value})
_STATUS_COLORS = {
    Status.DONE.value: SUCCESS_COLOR,
    Status.CANCELLED.value: IDLE_COLOR,
    Status.QUEUED.value: IDLE_COLOR,
}
_BOLD_AMBER = f"{STYLE_BOLD} {AMBER}"
_BOLD_GREY = f"{STYLE_BOLD} {SECONDARY_TEXT_COLOR}"
_PLAIN_GREY = f"not {STYLE_BOLD} {SECONDARY_TEXT_COLOR}"
_DONE = Status.DONE.value


@dataclass(frozen=True)
class BriefingLayout:
    """How much room the briefing has: compact below the wide mockup, tiny when rows run out."""

    now_lines: int
    compact: bool = False
    tiny: bool = False


def briefing_verbs(view: TaskDetailView) -> tuple[BriefingAction, ...]:
    """Return the verbs the actions row offers for `view`."""
    return briefing_actions(
        Status(view.status),
        view.holds,
        operator_owned=bool(view.ownership),
        can_take_over=view.can_take_over,
        waiting=view.wait_reason is not None,
    )


def needed_from_you(view: TaskDetailView, now: datetime) -> str | None:
    """Return what the operator must act on: a decision question first, else a wait reason."""
    if _escalated(view):
        question = next(
            (
                report.question
                for report in (view.latest_report, view.latest_verdict)
                if report is not None and report.question
            ),
            None,
        )
        return _sentence(question) if question is not None else NO_QUESTION
    if view.blocker is not None and view.blocker.quota_wait is not None:
        quota = view.blocker.quota_wait
        return _sentence(f"{WAITING_ON} {quota_wait_description(quota.agent, quota.until, now)}")
    blocker_quota = view.blocker is not None and view.blocker.kind == WaitReason.QUOTA.value
    reason = view.wait_reason or (WaitReason.QUOTA.value if blocker_quota else None)
    if reason is None or reason == WaitReason.PAUSED.value:
        return None
    return _sentence(f"{WAITING_ON} {reason.replace('_', ' ')}")


def _escalated(view: TaskDetailView) -> bool:
    return view.status == Status.ESCALATED.value


def total_cost_text(sessions: Sequence[TaskSessionView]) -> str | None:
    """Return the task's summed cost with its basis, `subscription`, or nothing when unknown."""
    groups = _cost_groups(sessions)
    return _joined(groups).plain if groups else None


def header_text(view: TaskDetailView, now: datetime, *, compact: bool) -> Text:
    """Return the identity line, the state line and, when wide, the time line."""
    header = _identity_text(view)
    header.append("\n")
    header.append_text(_state_line(view, now, compact=compact))
    time_line = None if compact else _time_line(view, now)
    if time_line is not None:
        header.append("\n")
        header.append_text(time_line)
    return header


@dataclass(frozen=True)
class Briefing:
    """Render a task's Progress, Result and Problems groups for the width they are drawn at."""

    view: TaskDetailView
    now: datetime
    layout: BriefingLayout

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        """Yield the milestones and the groups, laid out wide or compact."""
        width = options.max_width
        blocks = (
            self._compact_blocks(console, width)
            if self.layout.compact
            else self._wide_blocks(console, width)
        )
        yield from blocks

    def _milestones(self) -> list[RenderableType]:
        if not self.view.milestones:
            return []
        return [StageActivity(self.view).render(glyphs_only=self.layout.compact)]

    def _wide_blocks(self, console: Console, width: int) -> list[RenderableType]:
        view = self.view
        value_width = width - GROUP_INDENT - LABEL_WIDTH - LABEL_GAP
        return [
            *self._milestones(),
            *_group(PROGRESS_TITLE, self._progress_rows(console, value_width), width),
            *_labelled(self._summary_rows(console, width - LABEL_WIDTH - LABEL_GAP), width),
            *_group(_result_title(view), _result_rows(view), width),
            *_group(PROBLEMS_TITLE, _problem_rows(view, self.now), width),
            *_group(COST_LABEL, _cost_rows(view), width),
        ]

    def _progress_rows(self, console: Console, value_width: int) -> list[Row]:
        view = self.view
        if view.status in TERMINAL_STATUSES:
            return []
        rows = [*_step_rows(view, self.now), *_rework_rows(view)]
        if _working(view):
            rows.append((grey(NOW_LABEL), _now_groups(view, self.now)))
            if view.last_words is not None:
                lines = _wrapped(
                    console,
                    grey(_quote(view.last_words)),
                    Text(),
                    value_width,
                    self.layout.now_lines,
                )
                rows.extend((Text(), (line,)) for line in lines)
        return rows

    def _summary_rows(self, console: Console, value_width: int) -> list[Row]:
        summary = _done_summary(self.view)
        if summary is None:
            return []
        lines = _wrapped(console, grey(summary), Text(), value_width, self.layout.now_lines)
        return [
            (Text(SUMMARY_LABEL, style=STEEL_BLUE) if index == 0 else Text(), (line,))
            for index, line in enumerate(lines)
        ]

    def _compact_blocks(self, console: Console, width: int) -> list[RenderableType]:
        view = self.view
        needed = _compact_needed(view, self.now, width)
        body = [
            *self._compact_progress(console, width),
            *_compact_summary(view, width),
            *_compact_result(view, width),
            *_compact_verdict(view),
            *_compact_cost(view),
        ]
        milestones = self._milestones()
        return [*needed, *body, *milestones] if self.layout.tiny else [*milestones, *needed, *body]

    def _compact_progress(self, console: Console, width: int) -> list[RenderableType]:
        view = self.view
        if not _working(view):
            return []
        lines: list[RenderableType] = list(
            _compact_activity(console, view, self.now, width, self.layout.now_lines)
        )
        escalation = _escalation_text(view)
        if escalation is not None:
            lines.append(escalation)
        return lines


def _group(title: str, rows: list[Row], width: int) -> list[RenderableType]:
    if not rows:
        return []
    body = Padding(labelled_rows(rows, width - GROUP_INDENT, LABEL_WIDTH), (0, 0, 0, GROUP_INDENT))
    return [Section(title, body, ruled=False, title_style=STEEL_BLUE)]


def _labelled(rows: list[Row], width: int) -> list[RenderableType]:
    return [labelled_rows(rows, width, LABEL_WIDTH)] if rows else []


def _step_rows(view: TaskDetailView, now: datetime) -> list[Row]:
    if view.actual_stage is None:
        return []
    groups = [Text(view.actual_stage)]
    in_step = _in_step_time(view, now)
    if in_step is not None:
        groups.append(Text(in_step))
    return [(grey(STEP_LABEL), tuple(groups))]


def _rework_rows(view: TaskDetailView) -> list[Row]:
    groups: list[Text] = []
    rework = rework_progress(view.iteration, view.max_iterations)
    if rework is not None:
        groups.append(Text(f"{rework[0]} of {rework[1]}"))
    escalation = _escalation_text(view)
    if escalation is not None:
        groups.append(escalation)
    return [(grey(REWORK_LABEL), tuple(groups))] if groups else []


def _now_groups(view: TaskDetailView, now: datetime) -> tuple[Text, ...]:
    role = ROLE_LABELS.get(view.acting_role, view.acting_role.title())
    return (Text(role), _activity_text(view, now, compact=False))


def _result_title(view: TaskDetailView) -> str:
    results = view.results
    return (
        RESULT_SO_FAR_TITLE
        if results is None or results.outcome is ResultOutcome.SO_FAR
        else RESULT_TITLE
    )


def _result_rows(view: TaskDetailView) -> list[Row]:
    results = view.results
    if results is None:
        return []
    rows = [
        *live_unavailable_rows(results),
        *branch_row(results),
        _row(COMMITS_LABEL, _commit_groups(results, compact=False)),
        _row(TESTS_LABEL, tests_groups(results.tests)),
        _row(PR_LABEL, pull_request_groups(results.pull_request)),
        _row(ADDED_LABEL, _added_groups(results)),
    ]
    return [row for row in rows if row is not None]


def live_unavailable_rows(results: TaskResults) -> list[Row]:
    if results.live_error is None:
        return []
    notice = Text(LIVE_UNAVAILABLE.format(error=results.live_error), style=AMBER)
    return [(grey(LIVE_LABEL), (notice,))]


def _added_groups(results: TaskResults) -> tuple[Text, ...]:
    return tuple(
        Text.assemble(
            (f"{ADDED_WORD} {task_id}", SUCCESS_COLOR),
            (f" {title}" if title else "", SECONDARY_TEXT_COLOR),
        )
        for change in results.plan_changes
        for task_id, title in change.new_tasks
    )


def _verdict_groups(counts: VerdictCounts) -> tuple[Text, ...]:
    decision = Text(
        VERDICT_LABELS.get(counts.decision, counts.decision),
        style=DECISION_COLORS.get(counts.decision, SECONDARY_TEXT_COLOR),
    )
    return (decision, *severity_groups(counts))


def _row(label: str, groups: tuple[Text, ...]) -> Row | None:
    return (grey(label), groups) if groups else None


def branch_row(results: TaskResults) -> list[Row]:
    if results.branch is None:
        return []
    if results.outcome is ResultOutcome.NOT_MERGED:
        return [(grey(BRANCH_LABEL), (Text(f"{results.branch} {KEPT_NOT_MERGED}"),))]
    target = (
        results.branch
        if results.base is None
        else f"{results.branch} {BRANCH_ARROW} {results.base}"
    )
    label = MERGED_LABEL if results.outcome is ResultOutcome.MERGED else BRANCH_LABEL
    return [(grey(label), (Text(target),))]


def _problem_rows(view: TaskDetailView, now: datetime) -> list[Row]:
    rows: list[Row | None] = []
    counts = view.verdict_counts
    if counts is not None and counts.has_problems:
        rows.append(_row(VERDICT_LABEL, _verdict_groups(counts)))
    needed = needed_from_you(view, now)
    if needed is not None:
        label = BLOCKER_LABEL if _escalated(view) else PENDING_LABEL
        rows.append((Text(label, style=_BOLD_AMBER), (Text(needed),)))
    risks = view.results.residual_risks if view.results is not None else ()
    rows.append(_row(RISKS_LABEL, tuple(Text(risk, style=AMBER) for risk in risks)))
    return [row for row in rows if row is not None]


def _cost_rows(view: TaskDetailView) -> list[Row]:
    amounts, bases = _cost_amounts(view.sessions)
    if not amounts:
        groups = _subscription_groups(view.sessions)
        return [(Text(), groups)] if groups else []
    word, style = _cost_qualifier(bases)
    return [
        (
            grey(ROLE_SHORT_LABELS.get(role, label)),
            (Text(f"${amounts[role]:{COST_FORMAT}} {word}", style=style),),
        )
        for role, label in ROLE_LABELS.items()
        if role in amounts
    ]


def _compact_needed(view: TaskDetailView, now: datetime, width: int) -> list[RenderableType]:
    text = needed_from_you(view, now)
    prefix = NEEDS_YOU_PREFIX if _escalated(view) else PENDING_PREFIX
    return [] if text is None else [_hanging(prefix, text, width)]


def _compact_summary(view: TaskDetailView, width: int) -> list[RenderableType]:
    summary = _done_summary(view)
    return [] if summary is None else [grey(_ellipsized(summary, width))]


def _compact_result(view: TaskDetailView, width: int) -> list[RenderableType]:
    results = view.results
    if results is None:
        return []
    lines: list[RenderableType] = _compact_live_notice(results, width)
    if results.outcome is not ResultOutcome.SO_FAR:
        lines.extend(_inline_row(row) for row in branch_row(results))
    produced = _joined((*_commit_groups(results, compact=True), *_compact_tests(results.tests)))
    if produced.plain:
        lines.append(produced)
    pull_request = pull_request_groups(results.pull_request)
    if pull_request:
        lines.append(Text.assemble(grey(f"{PR_LABEL} "), _joined(pull_request)))
    lines.extend(_added_groups(results))
    return lines


def _inline_row(row: Row) -> Text:
    return Text.assemble(row[0], " ", row[1][0])


def _compact_live_notice(results: TaskResults, width: int) -> list[RenderableType]:
    notices: list[RenderableType] = []
    for row in live_unavailable_rows(results):
        notice = _inline_row(row)
        notice.truncate(max(width, 1), overflow="ellipsis")
        notices.append(notice)
    return notices


def _compact_verdict(view: TaskDetailView) -> list[RenderableType]:
    counts = view.verdict_counts
    lines: list[RenderableType] = []
    if counts is not None and counts.has_problems:
        lines.append(_joined(_verdict_groups(counts)))
    risks = view.results.residual_risks if view.results is not None else ()
    if risks:
        lines.append(Text.assemble(grey(f"{RISKS_LABEL} "), (GROUP_SEPARATOR.join(risks), AMBER)))
    return lines


def _compact_cost(view: TaskDetailView) -> list[RenderableType]:
    cost = _cost_groups(view.sessions)
    return [_joined(cost)] if cost else []


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


def _quote(words: str) -> str:
    return QUOTE + words + QUOTE


def _wrapped(console: Console, text: Text, tail: Text, width: int, max_lines: int) -> list[Text]:
    whole = text.copy()
    whole.append_text(tail)
    lines = list(whole.wrap(console, width))
    for line in lines:
        line.rstrip()
    if len(lines) <= max_lines:
        return lines
    last = lines[max_lines - 1]
    last.rstrip()
    last.truncate(max(width - cell_len(tail.plain) - 1, 0))
    last.append(LAST_WORDS_ELLIPSIS, style=text.style)
    last.append_text(tail)
    return [*lines[: max_lines - 1], last]


def _compact_activity(
    console: Console, view: TaskDetailView, now: datetime, width: int, max_lines: int
) -> list[Text]:
    age = _activity_age(view, now, compact=True)
    suffix = Text(f" {age}" if age is not None else "", style=SECONDARY_TEXT_COLOR)
    if _stalled(view):
        suffix.append(f" {GLYPH_STALLED} {STALLED_WORD}", style=AMBER)
    if view.last_words is None:
        return [Text.assemble(grey(ACTIVE_WORD), suffix)]
    return _wrapped(console, grey(_quote(view.last_words)), suffix, width, max_lines)


def _escalation_text(view: TaskDetailView) -> Text | None:
    remaining = view.escalation_remaining
    if remaining is None:
        return None
    noun = REJECTION_NOUNS[0] if remaining == 1 else REJECTION_NOUNS[1]
    return grey(ESCALATION_NEAR.format(count=remaining, noun=noun))


def counted(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def _commit_groups(results: TaskResults, *, compact: bool) -> tuple[Text, ...]:
    produced = results.produced
    if produced is None:
        count = len(results.commits) + results.more_commits
        return (Text(counted(count, "commit")),) if count else ()
    lines = Text.assemble(
        (f"+{produced.insertions}", SUCCESS_COLOR),
        " ",
        (f"{MINUS}{produced.deletions}", ALERT_COLOR),
    )
    files = () if compact else (Text(counted(produced.files, "file")),)
    return (Text(counted(produced.commits, "commit")), *files, lines)


def tests_groups(tests: ReportedTests | None) -> tuple[Text, ...]:
    if tests is None:
        return ()
    counts = (
        (tests.passed, "passed", SUCCESS_COLOR),
        (tests.failed, "failed", ALERT_COLOR),
        (tests.skipped, "skipped", SECONDARY_TEXT_COLOR),
    )
    groups = tuple(Text(f"{count} {word}", style=color) for count, word, color in counts if count)
    return (*groups, grey(REPORTED_BY_IMPLEMENTER)) if groups else ()


def _compact_tests(tests: ReportedTests | None) -> tuple[Text, ...]:
    if tests is None or not (tests.passed or tests.failed):
        return ()
    marks = ((tests.passed, PASSED_MARK), (tests.failed, FAILED_MARK))
    return (grey(" ".join(f"{count}{mark}" for count, mark in marks if count)),)


def _done_summary(view: TaskDetailView) -> str | None:
    if view.status != _DONE or view.latest_report is None:
        return None
    return squashed(view.latest_report.summary)


def severity_groups(counts: VerdictCounts) -> tuple[Text, ...]:
    """Return the non-zero issue counts of a verdict, each in its severity colour."""
    severities = (
        (counts.blocker, "blocker", ALERT_COLOR),
        (counts.major, "major", AMBER),
        (counts.minor, "minor", SECONDARY_TEXT_COLOR),
    )
    return tuple(Text(f"{count} {word}", style=color) for count, word, color in severities if count)


def pull_request_groups(pull_request: PullRequestLine | None) -> tuple[Text, ...]:
    if pull_request is None:
        return ()
    state = pull_request.state.lower()
    number = f"#{pull_request.number} " if pull_request.number is not None else ""
    groups = [Text(f"{number}{state}", style=PR_STATE_COLORS.get(state, ""))]
    if pull_request.checks_pending:
        groups.append(Text("checks pending"))
    if pull_request.checks_failing:
        groups.append(Text("checks failing", style=AMBER))
    return tuple(groups)


def _cost_amounts(sessions: Sequence[TaskSessionView]) -> tuple[dict[str, float], set[str]]:
    amounts: dict[str, float] = {}
    bases: set[str] = set()
    for session in sessions:
        if session.cost_usd is not None:
            amounts[session.role] = amounts.get(session.role, 0.0) + session.cost_usd
            bases.add(session.cost_basis)
    return amounts, bases


def _subscription_groups(sessions: Sequence[TaskSessionView]) -> tuple[Text, ...]:
    subscribed = any(session.billing_mode == BILLING_MODE_SUBSCRIPTION for session in sessions)
    return (Text(SUBSCRIPTION_WORD, style=SAGE),) if subscribed else ()


def _cost_groups(sessions: Sequence[TaskSessionView]) -> tuple[Text, ...]:
    amounts, bases = _cost_amounts(sessions)
    if not amounts:
        return _subscription_groups(sessions)
    word, style = _cost_qualifier(bases)
    return (Text(f"${sum(amounts.values()):{COST_FORMAT}} {word}", style=style),)


def _cost_qualifier(bases: set[str]) -> tuple[str, str]:
    if bases == {COST_BASIS_BILLED}:
        return BILLED_WORD, SAGE
    if bases == {COST_BASIS_API_EQUIVALENT}:
        return ESTIMATE_WORD, AMBER
    return MIXED_COST_BASIS, SECONDARY_TEXT_COLOR


def _in_step_time(view: TaskDetailView, now: datetime) -> str | None:
    if view.actual_stage is None or view.actual_stage_started_at is None:
        return None
    return duration_text((view.stage_ended_at or now) - view.actual_stage_started_at)


def _identity_text(view: TaskDetailView) -> Text:
    identity = Text(f"{view.task_id} · {view.title}", style=BOLD_PRIMARY)
    if view.complexity is None:
        return identity
    identity.append(GROUP_SEPARATOR, style=BOLD_PRIMARY)
    identity.append(view.complexity.title(), style=_BOLD_GREY)
    identity.append(f" {COMPLEXITY_WORD}", style=_PLAIN_GREY)
    return identity


def _time_line(view: TaskDetailView, now: datetime) -> Text | None:
    parts: list[str] = []
    if view.status in TERMINAL_STATUSES:
        if view.started_at is not None and view.stage_ended_at is not None:
            parts.append(f"{duration_text(view.stage_ended_at - view.started_at)} {TOTAL_WORD}")
    else:
        in_step = _in_step_time(view, now)
        if in_step is not None:
            parts.append(f"{in_step} {IN_STEP_WORD}")
        if view.started_at is not None:
            parts.append(f"{duration_text(now - view.started_at)} {TOTAL_WORD}")
    if not parts:
        return None
    return grey(GROUP_SEPARATOR.join(parts))


def _state_line(view: TaskDetailView, now: datetime, *, compact: bool) -> Text:
    marker, label = _marker_and_label(view)
    line = Text()
    line.append(f"{marker} {label}", style=f"{STYLE_BOLD} {_state_color(view)}")
    if view.status in TERMINAL_STATUSES:
        return line
    tail: list[str] = []
    if compact:
        rework = rework_progress(view.iteration, view.max_iterations)
        if rework is not None:
            tail.append(f"{REWORK_WORD} {rework[0]}/{rework[1]}")
        in_step = _in_step_time(view, now)
        if in_step is not None:
            tail.append(in_step)
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
    if view.status in TERMINAL_STATUSES and terminal is not None:
        marker, label = terminal
        if view.status == Status.CANCELLED.value and view.actual_stage is not None:
            label = f"{label}{GROUP_SEPARATOR}{STOPPED_DURING} {view.actual_stage}"
        return marker, label
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
