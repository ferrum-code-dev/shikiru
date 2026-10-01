"""Rich renderables shared by operator CLI output."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from shikiru.domain.reports import (
    ConsultAnswer,
    ImplementationReport,
    PlanReport,
    TriageReport,
    Verdict,
)
from shikiru.operator.strip_views import task_strip_data
from shikiru.ui.age import format_age
from shikiru.ui.glyphs import GLYPH_ENGINE
from shikiru.ui.status import status_visual
from shikiru.ui.strip import TaskStrip
from shikiru.ui.tokens import ALERT_COLOR, ENGINE_COLOR, STYLE_BOLD

if TYPE_CHECKING:
    from rich.console import RenderableType

    from shikiru.agents.contracts import CostBasis
    from shikiru.config.settings import Settings
    from shikiru.operator.commands import Ack
    from shikiru.operator.doctor import DoctorReport
    from shikiru.operator.queries import StatusView, TaskDetail, TaskSummary
    from shikiru.operator.sessions import SessionRow

STATUS_PANEL_TITLE = "Shikiru status"
ACTIVE_TABLE_TITLE = "Active tasks"
QUEUE_TABLE_TITLE = "Queue"
ESCALATIONS_TABLE_TITLE = "Open escalations"
FAULTS_TABLE_TITLE = "Engine faults"
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
USAGE_TABLE_TITLE = "Usage"
QUOTA_TABLE_TITLE = "Quota windows"
REPORTS_TABLE_TITLE = "Reports and verdicts"
ACK_PANEL_TITLE = "Command"
DOCTOR_PANEL_TITLE = "Shikiru doctor"
DOCTOR_OK_LABEL = "ok"
DOCTOR_FAIL_LABEL = "fail"
NO_ROWS = "none"
NO_VALUE = "—"
SESSIONS_TABLE_TITLE = "Shikiru sessions"


def status_renderable(view: StatusView, *, settings: Settings, width: int) -> RenderableType:
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
    if view.oldest_escalation_age_seconds is not None:
        summary.append("  oldest ", STYLE_BOLD)
        summary.append(format_age(view.oldest_escalation_age_seconds))
    blocks: list[RenderableType] = [Panel(summary, title=STATUS_PANEL_TITLE)]
    blocks.append(_tasks_table(view.active_tasks, ACTIVE_TABLE_TITLE, include_priority=False))
    blocks.append(_tasks_table(view.queued_tasks, QUEUE_TABLE_TITLE, include_priority=True))
    if view.faults:
        blocks.append(_faults_table(view))
    if view.escalations:
        blocks.append(_escalations_table(view))
    if view.usage:
        blocks.append(_usage_table(view))
    if view.quotas:
        blocks.append(_quota_table(view))
    if view.task is not None:
        blocks.append(task_detail_renderable(view.task, settings=settings, width=width))
    return _stack(blocks)


def task_detail_renderable(detail: TaskDetail, *, settings: Settings, width: int) -> RenderableType:
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
            TaskStrip(task_strip_data(detail.task, settings)).render(width),
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


def doctor_renderable(report: DoctorReport) -> Table:
    """Render environment diagnostics as a compact check table."""
    table = Table(title=DOCTOR_PANEL_TITLE)
    table.add_column("Status")
    table.add_column("Check")
    table.add_column("Detail")
    for check in report.checks:
        table.add_row(
            DOCTOR_OK_LABEL if check.ok else DOCTOR_FAIL_LABEL,
            check.name,
            check.detail,
        )
    return table


def session_rows_renderable(rows: list[SessionRow]) -> Table:
    """Render session locations and progress counts as a Rich table."""
    table = Table(title=SESSIONS_TABLE_TITLE)
    table.add_column("Session", overflow="fold")
    table.add_column("Project directory", overflow="fold")
    table.add_column("Engine")
    table.add_column("Running")
    table.add_column("Queued")
    table.add_column("Escalated")
    for row in rows:
        table.add_row(
            Text(row.session, overflow="fold"),
            Text(row.project_dir, overflow="fold"),
            row.engine_state,
            str(row.running),
            str(row.queued),
            str(row.escalated),
        )
    if not rows:
        table.add_row(NO_ROWS, NO_VALUE, NO_VALUE, NO_VALUE, NO_VALUE, NO_VALUE)
    return table


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


def _faults_table(view: StatusView) -> Table:
    table = Table(title=FAULTS_TABLE_TITLE)
    for column in ("Scope", "Task", "Error", "First seen", "Last seen", "Repeats", "Recoverable"):
        table.add_column(column)
    for fault in view.faults:
        table.add_row(
            fault.scope,
            fault.task_id or NO_VALUE,
            fault.error,
            fault.first_seen.strftime(TIMESTAMP_FORMAT),
            fault.last_seen.strftime(TIMESTAMP_FORMAT),
            str(fault.repeat),
            "yes" if fault.recoverable else "no",
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
        table.add_row(report.turn, report.kind.value, _report_summary(report.data))
    if not detail.reports:
        table.add_row(NO_ROWS, NO_VALUE, NO_VALUE)
    return table


def _report_summary(
    report: TriageReport | ImplementationReport | PlanReport | ConsultAnswer | Verdict,
) -> str:
    match report:
        case TriageReport(title=title):
            return title or NO_VALUE
        case ImplementationReport(status=status, summary=summary):
            return summary or status
        case PlanReport(summary=summary):
            return summary or NO_VALUE
        case ConsultAnswer(decision=decision):
            return decision
        case Verdict(decision=decision, summary=summary):
            return f"{decision}: {summary}" if summary else decision


def _cost_basis(cost_basis: CostBasis | Literal["mixed"]) -> str:
    match cost_basis:
        case "api_equivalent":
            return "API-equivalent"
        case "billed":
            return "billed"
        case "unknown":
            return NO_VALUE
        case "mixed":
            return "mixed"


def _slot(task: TaskSummary) -> str:
    return str(task.slot) if task.slot is not None else NO_VALUE


def _stack(blocks: list[RenderableType]) -> RenderableType:
    return Group(*blocks)
