"""Rich renderables shared by operator CLI output."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from shikiru.ui.glyphs import GLYPH_ENGINE
from shikiru.ui.status import status_visual
from shikiru.ui.tokens import ALERT_COLOR, ENGINE_COLOR, STYLE_BOLD

if TYPE_CHECKING:
    from rich.console import RenderableType

    from shikiru.operator.commands import Ack
    from shikiru.operator.queries import StatusView, TaskDetail, TaskSummary

STATUS_PANEL_TITLE = "Shikiru status"
ACTIVE_TABLE_TITLE = "Active tasks"
QUEUE_TABLE_TITLE = "Queue"
ESCALATIONS_TABLE_TITLE = "Open escalations"
USAGE_TABLE_TITLE = "Usage"
QUOTA_TABLE_TITLE = "Quota windows"
REPORTS_TABLE_TITLE = "Reports and verdicts"
ACK_PANEL_TITLE = "Command"
NO_ROWS = "none"
NO_VALUE = "—"


def status_renderable(view: StatusView) -> RenderableType:
    """Render an operator status model as Rich panels and tables."""
    engine_style = f"{STYLE_BOLD} {ENGINE_COLOR if view.engine_up else ALERT_COLOR}"
    summary = Text.assemble(
        (f"{GLYPH_ENGINE} engine ", f"{STYLE_BOLD} {ENGINE_COLOR}"),
        ("up" if view.engine_up else "down", engine_style),
        ("  slots ", STYLE_BOLD),
        f"{view.slots.active}/{view.slots.maximum}",
        ("  queued ", STYLE_BOLD),
        str(len(view.queued_tasks)),
        ("  escalations ", STYLE_BOLD),
        str(len(view.escalations)),
    )
    blocks: list[RenderableType] = [Panel(summary, title=STATUS_PANEL_TITLE)]
    blocks.append(_tasks_table(view.active_tasks, ACTIVE_TABLE_TITLE, include_priority=False))
    blocks.append(_tasks_table(view.queued_tasks, QUEUE_TABLE_TITLE, include_priority=True))
    if view.escalations:
        blocks.append(_escalations_table(view))
    if view.usage:
        blocks.append(_usage_table(view))
    if view.quotas:
        blocks.append(_quota_table(view))
    if view.task is not None:
        blocks.append(task_detail_renderable(view.task))
    return _stack(blocks)


def task_detail_renderable(detail: TaskDetail) -> RenderableType:
    """Render one task's timeline, reports, branches and sessions."""
    table = Table(title=f"{detail.task.id} · {detail.task.title}")
    table.add_column("Time")
    table.add_column("Event")
    table.add_column("Message")
    for event in detail.events:
        table.add_row(event.ts.isoformat(), event.kind.value, event.message)
    if not detail.events:
        table.add_row(NO_VALUE, NO_VALUE, NO_ROWS)
    branches = ", ".join(detail.branches) if detail.branches else NO_ROWS
    sessions = ", ".join(
        f"{run.role.value}:{run.status.value} · {run.agent}/{run.model}" for run in detail.sessions
    )
    return _stack(
        [
            table,
            _reports_table(detail),
            Panel(
                "\n".join(
                    (
                        f"status: {detail.task.status.value}",
                        f"branches: {branches}",
                        f"sessions: {sessions or NO_ROWS}",
                    )
                ),
                title=detail.task.id,
            ),
        ]
    )


def ack_renderable(ack: Ack) -> RenderableType:
    """Render a command acknowledgement."""
    text = f"{ack.status.value} · command {ack.id}"
    if ack.message:
        text = f"{text}\n{ack.message}"
    return Panel(text, title=ACK_PANEL_TITLE)


def _tasks_table(tasks: list[TaskSummary], title: str, *, include_priority: bool) -> Table:
    table = Table(title=title)
    table.add_column("Task")
    table.add_column("Status")
    table.add_column("Step")
    table.add_column("Wait")
    table.add_column("Priority" if include_priority else "Slot")
    for task in tasks:
        visual = status_visual(task.status)
        status = Text(f"{visual.glyph} {task.status.value}", style=visual.style)
        row = [task.id, status, task.step or NO_VALUE, task.wait_reason or NO_VALUE]
        row.append(str(task.priority) if include_priority else _slot(task))
        table.add_row(*row)
    if not tasks:
        table.add_row(NO_ROWS, NO_VALUE, NO_VALUE, NO_VALUE, NO_VALUE)
    return table


def _escalations_table(view: StatusView) -> Table:
    table = Table(title=ESCALATIONS_TABLE_TITLE)
    table.add_column("Id")
    table.add_column("Task")
    table.add_column("Reason")
    table.add_column("Headline")
    for item in view.escalations:
        table.add_row(
            item.escalation.id,
            item.escalation.task_id,
            item.escalation.reason.value,
            item.escalation.headline,
        )
    return table


def _usage_table(view: StatusView) -> Table:
    table = Table(title=USAGE_TABLE_TITLE)
    table.add_column("Agent")
    table.add_column("Input")
    table.add_column("Output")
    table.add_column("Cost")
    table.add_column("Basis")
    for usage in view.usage:
        cost = NO_VALUE if usage.cost_usd is None else f"${usage.cost_usd:.2f}"
        table.add_row(
            usage.agent,
            str(usage.input_tokens),
            str(usage.output_tokens),
            cost,
            _cost_basis(usage.cost_basis),
        )
    return table


def _quota_table(view: StatusView) -> Table:
    table = Table(title=QUOTA_TABLE_TITLE)
    table.add_column("Agent")
    table.add_column("Window")
    table.add_column("Used")
    table.add_column("Resets")
    table.add_column("Observed")
    for agent, windows in sorted(view.quotas.items()):
        for window in windows:
            resets = window.resets_at.isoformat() if window.resets_at is not None else NO_VALUE
            table.add_row(
                agent,
                window.name,
                f"{window.used_pct:.0f}%",
                resets,
                window.observed_at.isoformat(),
            )
    return table


def _reports_table(detail: TaskDetail) -> Table:
    table = Table(title=REPORTS_TABLE_TITLE)
    table.add_column("Turn")
    table.add_column("Kind")
    table.add_column("Summary")
    for report in detail.reports:
        table.add_row(
            str(report.get("turn", NO_VALUE)),
            str(report.get("kind", NO_VALUE)),
            _report_summary(report),
        )
    if not detail.reports:
        table.add_row(NO_ROWS, NO_VALUE, NO_VALUE)
    return table


def _report_summary(report: dict[str, object]) -> str:
    raw_data = report.get("data")
    if not isinstance(raw_data, dict):
        return NO_VALUE
    data = cast("dict[str, object]", raw_data)
    summary = data.get("summary")
    decision = data.get("decision")
    title = data.get("title")
    pieces = [
        str(value) for value in (decision, summary or title) if isinstance(value, str) and value
    ]
    if pieces:
        return ": ".join(pieces)
    for key in ("status", "question", "answer"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    return NO_VALUE


def _cost_basis(cost_basis: str) -> str:
    return {
        "api_equivalent": "API-equivalent",
        "billed": "billed",
        "mixed": "mixed",
        "unknown": NO_VALUE,
    }.get(cost_basis, cost_basis)


def _slot(task: TaskSummary) -> str:
    return str(task.slot) if task.slot is not None else NO_VALUE


def _stack(blocks: list[RenderableType]) -> RenderableType:
    return Group(*blocks)
