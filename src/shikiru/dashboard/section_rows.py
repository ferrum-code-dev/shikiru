"""Pure rows for the Events, Agent activity and Relationships lists."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from rich.text import Text

from shikiru.dashboard.row_list import RowCells
from shikiru.domain.tasks import Status
from shikiru.ui.tokens import ALERT_COLOR, AMBER, SECONDARY_TEXT_COLOR, STEEL_BLUE, SUCCESS_COLOR

if TYPE_CHECKING:
    from shikiru.dashboard.contract import (
        TaskDetailView,
        TaskReferenceView,
        TaskTimelineEntryView,
    )

NO_TIME = "--:--:--"
_CLOCK_FORMAT = "%H:%M:%S"
_LEAD_SEPARATOR = "  "
_FIRST_LINE = 0
_RELATION_BLOCKER = "Blocked by"
_RELATION_PARENT = "Parent"
_RELATION_ROOT = "Root"
_RELATION_CHILD = "Child"
_RELATION_DEPENDENCY = "Depends on"
_RELATION_DEPENDENT = "Blocks"
_RELATION_REPLACEMENT = "Replaced by"
_COLUMN_GAP = "  "
_STATE_GLYPHS = {
    Status.DONE.value: "\N{CHECK MARK}",
    Status.RUNNING.value: "\N{BLACK CIRCLE}",
    Status.INTEGRATING.value: "\N{BLACK CIRCLE}",
    Status.ESCALATED.value: "!",
    Status.CANCELLED.value: "\N{BALLOT X}",
    Status.QUEUED.value: "\N{MIDDLE DOT}",
}
_STATE_COLORS = {
    Status.DONE.value: SUCCESS_COLOR,
    Status.RUNNING.value: STEEL_BLUE,
    Status.INTEGRATING.value: STEEL_BLUE,
    Status.ESCALATED.value: AMBER,
    Status.CANCELLED.value: ALERT_COLOR,
    Status.QUEUED.value: SECONDARY_TEXT_COLOR,
}
_PROBLEM_COLORS = {Status.ESCALATED.value: AMBER, Status.CANCELLED.value: ALERT_COLOR}


@dataclass(frozen=True)
class RelatedRow:
    """A related task and the row that opens it."""

    task_id: str
    cells: RowCells


def timeline_cells(entry: TaskTimelineEntryView) -> RowCells:
    """Return an entry's row: clock time and role in grey, then its first summary line."""
    clock = (
        datetime.fromisoformat(entry.timestamp).strftime(_CLOCK_FORMAT)
        if entry.timestamp
        else NO_TIME
    )
    lead = _LEAD_SEPARATOR.join((clock, entry.role or entry.source))
    summary = entry.summary.splitlines()
    return RowCells(lead, summary[_FIRST_LINE] if summary else "")


def relation_rows(view: TaskDetailView) -> tuple[RelatedRow, ...]:
    """Return one aligned row per distinct related task, labelled by its first relation."""
    relationships = view.relationships
    labelled: list[tuple[str, TaskReferenceView]] = []
    if view.blocker is not None:
        labelled.extend((_RELATION_BLOCKER, item) for item in view.blocker.related_tasks)
    if relationships is not None:
        labelled.extend(
            [
                (_RELATION_PARENT, relationships.family_parent),
                (_RELATION_ROOT, relationships.family_root),
                *((_RELATION_CHILD, item) for item in relationships.family_children),
                *((_RELATION_BLOCKER, item) for item in relationships.blocking_dependencies),
                *((_RELATION_DEPENDENCY, item) for item in relationships.dependencies),
                *((_RELATION_DEPENDENT, item) for item in relationships.dependents),
                *((_RELATION_REPLACEMENT, item) for item in relationships.replacements),
            ]
        )
    first: dict[str, tuple[str, TaskReferenceView]] = {}
    for relation, reference in labelled:
        if reference.task_id != view.task_id and reference.task_id not in first:
            first[reference.task_id] = (relation, reference)
    relation_width = max((len(relation) for relation, _ in first.values()), default=0)
    id_width = max((len(task_id) for task_id in first), default=0)
    title_width = max((len(reference.title) for _, reference in first.values()), default=0)
    state_width = max(
        (len(_state_label(reference.status)) for _, reference in first.values()), default=0
    )
    return tuple(
        RelatedRow(
            task_id,
            RowCells(
                relation.ljust(relation_width),
                Text(
                    f"{task_id.ljust(id_width)}{_COLUMN_GAP}{reference.title.ljust(title_width)}",
                    style=_PROBLEM_COLORS.get(reference.status, "")
                    if relation == _RELATION_BLOCKER
                    else "",
                ),
                Text(
                    _state_label(reference.status).ljust(state_width),
                    style=_STATE_COLORS.get(reference.status, SECONDARY_TEXT_COLOR),
                ),
            ),
        )
        for task_id, (relation, reference) in first.items()
    )


def _state_label(status: str) -> str:
    return f"{_STATE_GLYPHS.get(status, _STATE_GLYPHS[Status.QUEUED.value])} {status}"


def blocker_evidence(view: TaskDetailView) -> Text | None:
    """Return the recorded blocker lines in amber, or dusty rose once a blocker is cancelled."""
    if view.blocker is None or not view.blocker.lines:
        return None
    blockers = list(view.blocker.related_tasks)
    if view.relationships is not None:
        blockers.extend(view.relationships.blocking_dependencies)
    cancelled = any(item.status == Status.CANCELLED.value for item in blockers)
    return Text("\n".join(view.blocker.lines), style=ALERT_COLOR if cancelled else AMBER)
