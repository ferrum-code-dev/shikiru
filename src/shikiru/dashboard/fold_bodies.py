"""Bodies of the Results and Decisions folds: grey labels, white values, one row per fact."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Group
from rich.text import Text

from shikiru.dashboard.briefing_view import (
    BRANCH_ARROW,
    BRANCH_LABEL,
    COMMITS_LABEL,
    MINUS,
    PR_LABEL,
    RISKS_LABEL,
    SUMMARY_LABEL,
    TESTS_LABEL,
    branch_row,
    needed_from_you,
    pull_request_groups,
    tests_groups,
)
from shikiru.domain.decisions import Decider
from shikiru.domain.results import ResultOutcome
from shikiru.domain.tasks import Status
from shikiru.ui.section import BOLD_STEEL, Row, grey, labelled_rows
from shikiru.ui.tokens import (
    ALERT_COLOR,
    AMBER,
    STEEL_BLUE,
    STYLE_BOLD,
    SUCCESS_COLOR,
)

if TYPE_CHECKING:
    from rich.console import Console, ConsoleOptions, RenderableType, RenderResult

    from shikiru.dashboard.contract import TaskDetailView
    from shikiru.domain.briefing import ProducedNumbers
    from shikiru.domain.decisions import DecisionEntry
    from shikiru.domain.results import PlanChange, TaskResults

PRIORITY_WORDS = {0: "standard", 1: "high", -1: "low"}
CHANGES_LABEL = "Changes"
PLAN_LABEL = "Plan"
OPEN_LABEL = "Open"
NOTHING_RECORDED = "Nothing is recorded yet."
CRITERIA_LABEL = "Acceptance criteria"
NO_CRITERIA = "No validated acceptance criteria are recorded."
CRITERION_LEAD = "\N{BULLET} "
PATH_LABEL = "Path"
HOME_MARK = "~"
MODE_LABEL = "Mode"
SUBMITTED_LABEL = "Submitted"
REQUEUED_LABEL = "Requeued"
STATUS_REASON_LABEL = "Status reason"
PRIORITY_LABEL = "Priority"
MORE_COMMITS = "+{count} more"
ADDED_TASK = "added {id}"
CLOCK_FORMAT = "%H:%M"
TIME_GAP = "  "
_BOLD_AMBER = f"{STYLE_BOLD} {AMBER}"
DECIDER_WIDTH = max(len(decider.value) for decider in Decider)


def _counted(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def _stacked(label: str, lines: list[Text]) -> list[Row]:
    return [(grey(label if index == 0 else ""), (line,)) for index, line in enumerate(lines)]


def _commit_rows(results: TaskResults) -> list[Row]:
    lines = [
        Text.assemble(Text(commit.sha, style=STEEL_BLUE), TIME_GAP, commit.subject)
        for commit in results.commits
    ]
    if results.more_commits:
        lines.append(grey(MORE_COMMITS.format(count=results.more_commits)))
    return _stacked(COMMITS_LABEL, lines)


def _changes_groups(produced: ProducedNumbers | None) -> tuple[Text, ...]:
    if produced is None:
        return ()
    return (
        Text.assemble(
            _counted(produced.files, "file"),
            TIME_GAP,
            (f"+{produced.insertions}", SUCCESS_COLOR),
            " ",
            (f"{MINUS}{produced.deletions}", ALERT_COLOR),
        ),
    )


def _plan_lines(change: PlanChange) -> list[Text]:
    added = [
        Text(ADDED_TASK.format(id=" ".join(filter(None, task))), style=SUCCESS_COLOR)
        for task in change.new_tasks
    ]
    return [*added, *(Text(scope) for scope in change.scope_changes)]


def _summary_rows(results: TaskResults) -> list[Row]:
    if results.plan_summary is None:
        return []
    return [(grey(SUMMARY_LABEL), (Text(results.plan_summary),))]


def _results_rows(results: TaskResults) -> list[Row]:
    optional = (
        (CHANGES_LABEL, _changes_groups(results.produced)),
        (TESTS_LABEL, tests_groups(results.tests)),
        (PR_LABEL, pull_request_groups(results.pull_request)),
    )
    plan = _stacked(
        PLAN_LABEL, [line for change in results.plan_changes for line in _plan_lines(change)]
    )
    lead = [*plan, *_summary_rows(results)] if results.plan_summary is not None else []
    return [
        *lead,
        *branch_row(results),
        *_commit_rows(results),
        *((grey(label), groups) for label, groups in optional if groups),
        *([] if lead else plan),
        *_stacked(RISKS_LABEL, [Text(risk, style=AMBER) for risk in results.residual_risks]),
    ]


def _body(width: int, rows: list[Row], notes: list[Text]) -> RenderableType:
    if not rows:
        return Group(*notes, grey(NOTHING_RECORDED))
    label_width = max(len(label.plain) for label, _ in rows)
    return Group(*notes, labelled_rows(rows, width, label_width))


@dataclass(frozen=True)
class ResultsBody:
    """The Results fold body: where the work went, what it contains and what is left open."""

    results: TaskResults | None

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        """Yield the so-far note when work is in progress, then the labelled rows."""
        del console
        results = self.results
        rows = _results_rows(results) if results is not None else []
        so_far = results is not None and results.outcome is ResultOutcome.SO_FAR
        yield _body(
            options.max_width, rows, [grey(ResultOutcome.SO_FAR.value)] if so_far and rows else []
        )


def _decision_row(entry: DecisionEntry) -> Row:
    decider = Text(entry.decider.value.ljust(DECIDER_WIDTH), style=STEEL_BLUE)
    label = Text.assemble(grey(entry.at.strftime(CLOCK_FORMAT)), TIME_GAP, decider)
    return label, (
        Text("\n").join([Text(entry.headline), *(grey(line) for line in entry.details)]),
    )


def _decision_rows(view: TaskDetailView) -> list[Row]:
    rows: list[Row] = []
    if view.status == Status.ESCALATED.value:
        question = needed_from_you(view)
        if question is not None:
            rows.append((Text(OPEN_LABEL, style=_BOLD_AMBER), (Text(question, style=AMBER),)))
    if view.decisions is not None:
        rows.extend(_decision_row(entry) for entry in view.decisions.entries)
    return rows


@dataclass(frozen=True)
class DecisionsBody:
    """The Decisions fold body: the open question, then every decision oldest first."""

    view: TaskDetailView

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        """Yield the labelled rows in order, or the nothing-recorded note."""
        del console
        yield _body(options.max_width, _decision_rows(self.view), [])


@dataclass(frozen=True)
class DescriptionBody:
    """The Task description fold body: the request, then the acceptance criteria as a list."""

    view: TaskDetailView

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        """Yield the request text and the criteria list."""
        del console, options
        yield Text(self.view.request)
        yield Text(CRITERIA_LABEL, style=BOLD_STEEL)
        criteria = self.view.acceptance_criteria
        if not criteria:
            yield grey(NO_CRITERIA)
        for item in criteria:
            yield Text.assemble(grey(CRITERION_LEAD), item)


def home_shortened(path: str, home: Path) -> str:
    """Return `path` as `~/...` when it lies under `home`, otherwise unchanged."""
    location = Path(path)
    return (
        str(Path(HOME_MARK) / location.relative_to(home)) if location.is_relative_to(home) else path
    )


def _workspace_rows(view: TaskDetailView, home: Path) -> list[Row]:
    rows: list[Row] = []
    if view.branch:
        base = view.results.base if view.results is not None else None
        target = view.branch if base is None else f"{view.branch} {BRANCH_ARROW} {base}"
        rows.append((grey(BRANCH_LABEL), (Text(target),)))
    rows.append((grey(PATH_LABEL), (Text(home_shortened(view.worktree_path, home)),)))
    return rows


@dataclass(frozen=True)
class WorkspaceBody:
    """The Workspace fold body: the task branch and where its worktree lives."""

    view: TaskDetailView
    home: Path

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        """Yield the labelled branch and path rows."""
        del console
        yield _body(options.max_width, _workspace_rows(self.view, self.home), [])


def priority_word(priority: int) -> str:
    """Name a dispatch priority: `standard` at 0, `high` above it and `low` below it."""
    return PRIORITY_WORDS[(priority > 0) - (priority < 0)]


def _present(value: str | None, style: str = "") -> Text | None:
    return Text(value, style=style) if value else None


def _settings_rows(view: TaskDetailView) -> list[Row]:
    entries = (
        (MODE_LABEL, _present(view.mode)),
        (SUBMITTED_LABEL, _present(view.submitted_age)),
        (REQUEUED_LABEL, _present(view.requeued_age)),
        (STATUS_REASON_LABEL, _present(view.status_reason, AMBER)),
        (
            PRIORITY_LABEL,
            None
            if view.priority is None
            else Text(f"{priority_word(view.priority)} ({view.priority})"),
        ),
    )
    return [(grey(label), (value,)) for label, value in entries if value is not None]


@dataclass(frozen=True)
class SettingsBody:
    """The Settings fold body: mode, ages, status reason and dispatch priority."""

    view: TaskDetailView

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        """Yield the labelled settings rows."""
        del console
        yield _body(options.max_width, _settings_rows(self.view), [])
