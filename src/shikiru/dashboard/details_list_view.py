"""Rows of the task detail folds: section names, their data headlines and the row renderable."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from rich.cells import cell_len
from rich.text import Text

from shikiru.dashboard.briefing_view import total_cost_text
from shikiru.dashboard.fold_bodies import priority_word
from shikiru.domain.results import ResultOutcome
from shikiru.domain.tasks import Status
from shikiru.ui.section import BOLD_STEEL, GROUP_SEPARATOR, grey
from shikiru.ui.tokens import STEEL_BLUE

if TYPE_CHECKING:
    from collections.abc import Callable

    from rich.console import Console, ConsoleOptions, RenderableType, RenderResult

    from shikiru.dashboard.contract import TaskDetailView

ROW_LEAD = "  "
_HEADLINE_GAP = "  "
DECISION_OPEN = "question open"
_PR_WORD = "PR"
_DECISION_NOUNS = ("decision", "decisions")
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
        headline = entry.headline or ""
        if headline and cell_len(entry.name) + cell_len(_HEADLINE_GAP) + cell_len(headline) > room:
            headline = ""
        name = Text(entry.name, style=BOLD_STEEL if self.selected else STEEL_BLUE)
        name.truncate(room - cell_len(headline), overflow="ellipsis", pad=True)
        line = Text(ROW_LEAD)
        line.append_text(name)
        line.append_text(grey(headline))
        yield line


def entry_renderable(entry: DetailsEntry, *, selected: bool) -> RenderableType:
    """Return one row: steel-blue name (bold when selected), grey headline at the right."""
    return _EntryLine(entry, selected)


def _usage_headline(view: TaskDetailView) -> str | None:
    return total_cost_text(view.sessions)


def _workspace_headline(view: TaskDetailView) -> str | None:
    return f"{_BRANCH_WORD} {view.branch}" if view.branch else None


def _settings_headline(view: TaskDetailView) -> str | None:
    parts = [
        view.mode or "",
        f"{priority_word(view.priority)} {_PRIORITY_WORD}" if view.priority is not None else "",
    ]
    return GROUP_SEPARATOR.join(part for part in parts if part) or None


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
    count = len(view.decisions.entries) if view.decisions is not None else 0
    return f"{count} {_DECISION_NOUNS[count != 1]}" if count else None


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
