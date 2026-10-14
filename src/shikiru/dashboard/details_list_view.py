"""Rows of the task detail folds: section names, their data headlines and the row renderable."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from rich.cells import cell_len
from rich.text import Text

from shikiru.dashboard.briefing_view import VERDICT_LABELS, total_cost_text
from shikiru.domain.results import ResultOutcome
from shikiru.domain.tasks import Status
from shikiru.ui.section import BOLD_PRIMARY, GROUP_SEPARATOR, grey
from shikiru.ui.tokens import PRIMARY_TEXT_COLOR

if TYPE_CHECKING:
    from collections.abc import Callable

    from rich.console import Console, ConsoleOptions, RenderableType, RenderResult

    from shikiru.dashboard.contract import TaskDetailView

_ARROWS = "\N{UPWARDS ARROW}\N{DOWNWARDS ARROW}"
EVENTS_HINT = f"{_ARROWS} select · End follow"
RELATIONSHIPS_HINT = f"{_ARROWS} select · Enter open"
ROW_LEAD = "  "
_HEADLINE_GAP = "  "
DECISION_OPEN = "decision open"
_PR_WORD = "PR"
_LAST_WORD = "last"
_ROUND_NOUNS = "rounds"
_COMMIT_NOUNS = {True: "commit", False: "commits"}
_BRANCH_WORD = "branch"
_PRIORITY_WORD = "priority"
_BLOCKER_NOUNS = ("blocker", "blockers")


class DetailsSection(StrEnum):
    """The task detail sections, in fold order, named as the headers show them."""

    REQUEST = "Task description"
    RESULTS = "Results"
    DECISIONS = "Decisions"
    EVENTS = "Events"
    AGENT_ACTIVITY = "Agent activity"
    USAGE = "Usage"
    BLOCKERS = "Dependencies"
    WORKSPACE = "Workspace"
    SETTINGS = "Settings"


@dataclass(frozen=True)
class DetailsEntry:
    """One fold header row: what it opens, its name and its optional grey headline."""

    entry_id: str
    name: str
    headline: str | None = None


def section_headline(section: DetailsSection, view: TaskDetailView | None) -> str | None:
    """Return a section's headline from recorded fields, or nothing when it has none."""
    headline = _HEADLINES.get(section)
    return headline(view) if headline is not None and view is not None else None


@dataclass(frozen=True)
class _EntryLine:
    entry: DetailsEntry
    selected: bool

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        del console
        entry = self.entry
        room = max(options.max_width - len(ROW_LEAD), 1)
        headline = f"{_HEADLINE_GAP}{entry.headline}" if entry.headline else ""
        if cell_len(entry.name) + cell_len(headline) > room:
            headline = ""
        name = Text(entry.name, style=BOLD_PRIMARY if self.selected else PRIMARY_TEXT_COLOR)
        name.truncate(room - cell_len(headline), overflow="ellipsis", pad=True)
        line = Text(ROW_LEAD)
        line.append_text(name)
        line.append_text(grey(headline))
        yield line


def entry_renderable(entry: DetailsEntry, *, selected: bool) -> RenderableType:
    """Return one row: the name in bold when selected, and the headline in grey on the right."""
    return _EntryLine(entry, selected)


def _usage_headline(view: TaskDetailView) -> str | None:
    return total_cost_text(view.sessions)


def _workspace_headline(view: TaskDetailView) -> str | None:
    return f"{_BRANCH_WORD} {view.branch}" if view.branch else None


def _settings_headline(view: TaskDetailView) -> str | None:
    return f"{_PRIORITY_WORD} {view.priority}" if view.priority is not None else None


def _results_headline(view: TaskDetailView) -> str | None:
    results = view.results
    if results is None:
        return None
    if results.outcome is ResultOutcome.SO_FAR:
        return ResultOutcome.SO_FAR.value
    commits = len(results.commits) + results.more_commits
    pull_request = results.pull_request
    parts = [
        f"{commits} {_COMMIT_NOUNS[commits == 1]}" if commits else "",
        f"{_PR_WORD} #{pull_request.number}"
        if pull_request is not None and pull_request.number is not None
        else "",
    ]
    return GROUP_SEPARATOR.join(part for part in parts if part) or None


def _decisions_headline(view: TaskDetailView) -> str | None:
    if view.status == Status.ESCALATED.value:
        return DECISION_OPEN
    rounds = view.decisions.rounds if view.decisions is not None else ()
    if not rounds:
        return None
    last = VERDICT_LABELS.get(rounds[-1].decision, rounds[-1].decision).lower()
    if len(rounds) == 1:
        return last
    return f"{len(rounds)} {_ROUND_NOUNS}{GROUP_SEPARATOR}{_LAST_WORD} {last}"


def _blockers_headline(view: TaskDetailView) -> str | None:
    relationships = view.relationships
    count = relationships.blocking_dependency_count if relationships is not None else 0
    count = count or int(view.blocker is not None)
    if not count:
        return None
    return f"{count} {_BLOCKER_NOUNS[0] if count == 1 else _BLOCKER_NOUNS[1]}"


_HEADLINES: dict[DetailsSection, Callable[[TaskDetailView], str | None]] = {
    DetailsSection.RESULTS: _results_headline,
    DetailsSection.DECISIONS: _decisions_headline,
    DetailsSection.USAGE: _usage_headline,
    DetailsSection.WORKSPACE: _workspace_headline,
    DetailsSection.BLOCKERS: _blockers_headline,
    DetailsSection.SETTINGS: _settings_headline,
}
