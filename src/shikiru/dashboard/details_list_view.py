"""Rows of the task details list: section names, their data headlines and the row renderable."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from rich.cells import cell_len
from rich.text import Text

from shikiru.dashboard.briefing_view import VERDICT_LABELS, total_cost_text
from shikiru.ui.section import BOLD_PRIMARY, grey
from shikiru.ui.tokens import PRIMARY_TEXT_COLOR, STEEL_BLUE

if TYPE_CHECKING:
    from collections.abc import Callable

    from rich.console import Console, ConsoleOptions, RenderableType, RenderResult

    from shikiru.dashboard.contract import TaskDetailView

DETAILS_HINT = "\N{UPWARDS ARROW}\N{DOWNWARDS ARROW} select · Enter open"
_SELECTED_BAR = "\N{LEFT HALF BLOCK} "
_BAR_BLANK = "  "
_HEADLINE_GAP = "  "
_VERDICT_WORD = "verdict"
_REPORT_WORD = "report"
_BRANCH_WORD = "branch"
_PRIORITY_WORD = "priority"
_BLOCKER_NOUNS = ("blocker", "blockers")


class DetailsSection(StrEnum):
    """The task detail sections, in list order, named as the list shows them."""

    REPORTS = "Reports and decisions"
    REQUEST = "Request and criteria"
    EVENTS = "Events"
    AGENT_ACTIVITY = "Agent activity"
    USAGE = "Usage and sessions"
    FACTS = "Engine facts"
    WORKSPACE = "Workspace"
    BLOCKERS = "Blockers and related"
    SETTINGS = "Task settings"


@dataclass(frozen=True)
class DetailsEntry:
    """One list row: what it opens, its name and its optional grey headline."""

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
        room = max(options.max_width - len(_BAR_BLANK), 1)
        headline = f"{_HEADLINE_GAP}{entry.headline}" if entry.headline else ""
        if cell_len(entry.name) + cell_len(headline) > room:
            headline = ""
        name = Text(entry.name, style=BOLD_PRIMARY if self.selected else PRIMARY_TEXT_COLOR)
        name.truncate(room - cell_len(headline), overflow="ellipsis", pad=True)
        line = Text(_SELECTED_BAR if self.selected else _BAR_BLANK, style=STEEL_BLUE)
        line.append_text(name)
        line.append_text(grey(headline))
        yield line


def entry_renderable(entry: DetailsEntry, *, selected: bool) -> RenderableType:
    """Return one row: selection bar, name, and the headline in grey on the right."""
    return _EntryLine(entry, selected)


def _usage_headline(view: TaskDetailView) -> str | None:
    return total_cost_text(view.sessions)


def _workspace_headline(view: TaskDetailView) -> str | None:
    return f"{_BRANCH_WORD} {view.branch}" if view.branch else None


def _settings_headline(view: TaskDetailView) -> str | None:
    return f"{_PRIORITY_WORD} {view.priority}" if view.priority is not None else None


def _reports_headline(view: TaskDetailView) -> str | None:
    if view.latest_verdict is not None and view.latest_verdict.decision is not None:
        decision = view.latest_verdict.decision
        return f"{_VERDICT_WORD} {VERDICT_LABELS.get(decision, decision).lower()}"
    if view.latest_report is not None:
        return f"{_REPORT_WORD} {view.latest_report.kind}"
    return None


def _blockers_headline(view: TaskDetailView) -> str | None:
    relationships = view.relationships
    count = relationships.blocking_dependency_count if relationships is not None else 0
    count = count or int(view.blocker is not None)
    if not count:
        return None
    return f"{count} {_BLOCKER_NOUNS[0] if count == 1 else _BLOCKER_NOUNS[1]}"


_HEADLINES: dict[DetailsSection, Callable[[TaskDetailView], str | None]] = {
    DetailsSection.REPORTS: _reports_headline,
    DetailsSection.USAGE: _usage_headline,
    DetailsSection.WORKSPACE: _workspace_headline,
    DetailsSection.BLOCKERS: _blockers_headline,
    DetailsSection.SETTINGS: _settings_headline,
}
