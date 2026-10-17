"""The one key table behind the Help panel: groups of key and action rows per context."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from shikiru.dashboard.detail import TASK_DETAIL_VIEWS_KEY, TASK_WINDOW_KEY
from shikiru.dashboard.resolution_view import EXTRA_DETAILS_KEY
from shikiru.dashboard.timeline_filter import CLEAR_KEY, CYCLE_ROLE_KEY, CYCLE_TURN_KEY
from shikiru.dashboard.timeline_section import FILTER_KEY

_ARROWS = "Up/Down"
_ESC = "Esc"
_ENTER = "Enter"
_PALETTE = "Ctrl+P"
_DETACH_KEY = "d"


class HelpTab(StrEnum):
    """A Help context; the value is the tab label."""

    HOME = "Home"
    TASK = "Task details"
    ESCALATIONS = "Escalations"


@dataclass(frozen=True)
class HelpRow:
    """One key and what it does."""

    keys: str
    action: str


@dataclass(frozen=True)
class HelpGroup:
    """A titled run of rows."""

    title: str
    rows: tuple[HelpRow, ...]


@dataclass(frozen=True)
class HelpAvailability:
    """Which optional parts of Home exist, so their keys are listed only when they work."""

    queue: bool
    escalations: bool
    detach_prefix: str | None


def help_groups(tab: HelpTab, availability: HelpAvailability) -> tuple[HelpGroup, ...]:
    """Return the groups of `tab`, with the Home keys that exist under `availability`."""
    if tab is HelpTab.HOME:
        return _home_groups(availability)
    if tab is HelpTab.TASK:
        return _task_groups()
    return _escalation_groups()


def _home_groups(availability: HelpAvailability) -> tuple[HelpGroup, ...]:
    tasks = [HelpRow("Click", "Select a task and show its details")]
    if availability.queue:
        tasks[:0] = [
            HelpRow(_ARROWS, "Select a task, wherever focus is"),
            HelpRow("f", "Cycle the task status filter"),
            HelpRow("/", "Search by task ID or title; Enter keeps it, Esc clears it"),
            HelpRow("m", "Load more tasks, while the list shows the hint"),
        ]
    tasks.append(HelpRow(_ESC, "Close a popup, clear the search, then clear the selection"))
    groups = [HelpGroup("Tasks", tuple(tasks))]
    if availability.escalations:
        groups.append(
            HelpGroup(
                "Escalations",
                (HelpRow(_ENTER, "Open the decision"),),
            )
        )
    everywhere = [
        HelpRow(f"{_PALETTE} / F12", "Open the actions for the selected task"),
        HelpRow("Tab", "Move to the footer, command status and resources; Enter uses them"),
        HelpRow("?", "Show this help"),
    ]
    if availability.detach_prefix is not None:
        detach = f"{availability.detach_prefix} {_DETACH_KEY}"
        everywhere.append(HelpRow(detach, "Detach from tmux"))
    groups.append(HelpGroup("Everywhere", tuple(everywhere)))
    return tuple(groups)


def _task_groups() -> tuple[HelpGroup, ...]:
    return (
        HelpGroup(
            "Task",
            (
                HelpRow("Tab", "Move between the task's actions and its sections"),
                HelpRow(_ENTER, "Run the focused action"),
                HelpRow(TASK_DETAIL_VIEWS_KEY, "Focus task details sections"),
                HelpRow(_PALETTE, "Open the actions for this task"),
                HelpRow(_ESC, "Step back, then return to Home"),
            ),
        ),
        HelpGroup(
            "Sections",
            (
                HelpRow(_ARROWS, "Move between section headers"),
                HelpRow(_ENTER, "Open the section, or enter its list"),
                HelpRow("Space / click", "Open or close a section"),
            ),
        ),
        HelpGroup(
            "Lists",
            (
                HelpRow(_ARROWS, "Select a row"),
                HelpRow("PgUp/PgDn", "Page through the rows"),
                HelpRow("End", "Jump to the latest and follow"),
                HelpRow(_ENTER, "Open the selected row"),
                HelpRow(_ESC, "Back to the section header"),
            ),
        ),
        HelpGroup(
            "Agent activity filter",
            (
                HelpRow(FILTER_KEY, "Open the filter"),
                HelpRow(CYCLE_ROLE_KEY, "Cycle the role"),
                HelpRow(CYCLE_TURN_KEY, "Cycle the turn"),
                HelpRow(CLEAR_KEY, "Clear the filter"),
                HelpRow(_ESC, "Close the filter"),
            ),
        ),
        HelpGroup(
            "Workspace section",
            (HelpRow(TASK_WINDOW_KEY, "Open the task's tmux window while its section is focused"),),
        ),
    )


def _escalation_groups() -> tuple[HelpGroup, ...]:
    return (
        HelpGroup(
            "Choose",
            (
                HelpRow(_ARROWS, "Choose an answer"),
                HelpRow(_ENTER, "Send it, or edit or expand the chosen row"),
                HelpRow("t", "Show or hide what was tried"),
                HelpRow(EXTRA_DETAILS_KEY, "Open extra details (when Details is open)"),
                HelpRow(_ESC, "Close without sending"),
            ),
        ),
        HelpGroup(
            "Write an answer",
            (
                HelpRow("Ctrl+S", "Send the typed answer"),
                HelpRow(_ESC, "Back to the choices"),
            ),
        ),
        HelpGroup("Cancel the task", (HelpRow(_ENTER, "Press Enter again to confirm"),)),
    )
