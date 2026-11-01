"""The Blockers rows of the briefing's Problems group: each cause, next step and occupants."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.cells import cell_len
from rich.text import Text

from shikiru.clock import to_local
from shikiru.domain.quota_waits import quota_clock_text
from shikiru.domain.scheduling import WaitReason
from shikiru.domain.tasks import Status
from shikiru.ui.glyphs import GLYPH_WARNING
from shikiru.ui.section import Row, grey
from shikiru.ui.tokens import ALERT_COLOR, AMBER, SECONDARY_TEXT_COLOR, STYLE_BOLD

if TYPE_CHECKING:
    from datetime import datetime

    from shikiru.dashboard.contract import TaskBlockerView, TaskDetailView, TaskReferenceView

BLOCKERS_LABEL = "Blockers"
_BOLD_AMBER = f"{STYLE_BOLD} {AMBER}"
_BOLD_ALERT = f"{STYLE_BOLD} {ALERT_COLOR}"
_COLUMN_GAP = "  "


def blocker_rows(view: TaskDetailView, now: datetime) -> list[Row]:
    """Return the Blockers rows: each blocker's warning headline, then its grey detail lines.

    The label sits on the first row only, and every line lines up under the value column. A quota
    wait's clock is worded for `now`, so the countdown moves with the clock. A blocker waiting on a
    cancelled task turns the alert colour.
    """
    rows: list[Row] = []
    for index, blocker in enumerate(view.blockers):
        alert = _waits_on_cancelled(view, blocker)
        label = grey(BLOCKERS_LABEL) if index == 0 else Text()
        rows.append((label, (_headline(blocker, now, alert),)))
        rows.extend((Text(), (grey(line),)) for line in blocker.details)
        rows.extend((Text(), (line,)) for line in _occupant_lines(blocker.related_tasks))
    return rows


def _headline(blocker: TaskBlockerView, now: datetime, alert: bool) -> Text:
    cause = blocker.headline
    if blocker.quota_wait is not None:
        quota = blocker.quota_wait
        description, remaining = quota_clock_text(quota.agent, to_local(quota.until), to_local(now))
        cause = f"{description} (in {remaining})"
    return Text.assemble(
        (GLYPH_WARNING, _BOLD_ALERT if alert else _BOLD_AMBER),
        (f" {cause}", ALERT_COLOR if alert else ""),
    )


def _occupant_lines(tasks: tuple[TaskReferenceView, ...]) -> list[Text]:
    id_width = max((cell_len(task.task_id) for task in tasks), default=0)
    title_width = max((cell_len(task.title) for task in tasks), default=0)
    return [
        Text(
            f"{_pad_cells(task.task_id, id_width)}{_COLUMN_GAP}"
            f"{_pad_cells(task.title, title_width)}{_COLUMN_GAP}{task.status}",
            style=ALERT_COLOR if task.status == Status.CANCELLED.value else SECONDARY_TEXT_COLOR,
        )
        for task in tasks
    ]


def _pad_cells(text: str, width: int) -> str:
    return f"{text}{' ' * (width - cell_len(text))}"


def _waits_on_cancelled(view: TaskDetailView, blocker: TaskBlockerView) -> bool:
    if blocker.kind == WaitReason.DEPENDENCY.value:
        if view.relationships is None:
            return False
        return _any_cancelled(view.relationships.blocking_dependencies)
    return _any_cancelled(blocker.related_tasks)


def _any_cancelled(tasks: tuple[TaskReferenceView, ...]) -> bool:
    return any(task.status == Status.CANCELLED.value for task in tasks)
