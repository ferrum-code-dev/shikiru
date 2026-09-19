"""Rich renderables shared by operator CLI output."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

if TYPE_CHECKING:
    from rich.console import RenderableType

    from shikiru.operator.commands import Ack
    from shikiru.operator.queries import StatusView, TaskDetail

STATUS_PANEL_TITLE = "Shikiru status"
TASKS_TABLE_TITLE = "Tasks"
ESCALATIONS_TABLE_TITLE = "Open escalations"
USAGE_TABLE_TITLE = "Usage"
ACK_PANEL_TITLE = "Command"


def status_renderable(view: StatusView) -> RenderableType:
    """Render an operator status model as Rich panels and tables."""
    summary = Text.assemble(
        ("engine ", "bold"),
        ("up" if view.engine_up else "down", "green" if view.engine_up else "red"),
        ("  slots ", "bold"),
        f"{view.slots.active}/{view.slots.maximum}",
        ("  queued ", "bold"),
        str(len(view.queued_tasks)),
        ("  escalations ", "bold"),
        str(len(view.escalations)),
    )
    blocks: list[RenderableType] = [Panel(summary, title=STATUS_PANEL_TITLE)]
    blocks.append(_tasks_table(view))
    if view.escalations:
        blocks.append(_escalations_table(view))
    if view.usage:
        blocks.append(_usage_table(view))
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
        table.add_row("", "", "no events")
    report_text = ", ".join(str(report["kind"]) for report in detail.reports)
    branches = ", ".join(detail.branches) if detail.branches else "none"
    sessions = ", ".join(f"{run.role.value}:{run.status.value}" for run in detail.sessions)
    return _stack(
        [
            table,
            Panel(
                "\n".join(
                    (
                        f"status: {detail.task.status.value}",
                        f"branches: {branches}",
                        f"sessions: {sessions or 'none'}",
                        f"reports: {report_text or 'none'}",
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


def _tasks_table(view: StatusView) -> Table:
    table = Table(title=TASKS_TABLE_TITLE)
    table.add_column("Task")
    table.add_column("Status")
    table.add_column("Step")
    table.add_column("Wait")
    for task in view.tasks:
        table.add_row(task.id, task.status.value, task.step or "", task.wait_reason or "")
    if not view.tasks:
        table.add_row("", "", "", "no tasks")
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
    for usage in view.usage:
        cost = "unknown" if usage.cost_usd is None else f"${usage.cost_usd:.2f}"
        table.add_row(
            usage.agent,
            str(usage.input_tokens),
            str(usage.output_tokens),
            cost,
        )
    return table


def _stack(blocks: list[RenderableType]) -> RenderableType:
    return Group(*blocks)
