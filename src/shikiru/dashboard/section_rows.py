"""Pure rows for the Events, Agent activity and Relationships lists."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from shikiru.dashboard.row_list import RowCells

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
_RELATION_BLOCKER = "blocker"
_RELATION_PARENT = "parent"
_RELATION_ROOT = "root"
_RELATION_CHILD = "child"
_RELATION_DEPENDENCY = "dependency"
_RELATION_DEPENDENT = "dependent"
_RELATION_REPLACEMENT = "replacement"


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
    """Return one row per distinct related task, labelled by its first relation to `view`."""
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
    rows: dict[str, RelatedRow] = {}
    for relation, reference in labelled:
        if reference.task_id != view.task_id and reference.task_id not in rows:
            rows[reference.task_id] = RelatedRow(
                reference.task_id,
                RowCells(
                    _LEAD_SEPARATOR.join((relation, reference.task_id)),
                    f"{reference.title} · {reference.status}",
                ),
            )
    return tuple(rows.values())
