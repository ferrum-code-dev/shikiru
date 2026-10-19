"""Rich renderables shared by operator CLI output."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal

from rich.console import Group
from rich.panel import Panel
from rich.style import Style
from rich.table import Table
from rich.text import Text

from shikiru.domain.reports import (
    ConsultAnswer,
    ImplementationReport,
    PlanReport,
    TriageReport,
    Verdict,
)
from shikiru.errors import NotInitialisedError, ShikiruError
from shikiru.operator.commands import AckStatus
from shikiru.operator.strip_views import task_strip_data
from shikiru.ui.age import format_age
from shikiru.ui.console import supports_osc8
from shikiru.ui.glyphs import (
    GLYPH_DONE,
    GLYPH_ENGINE,
    GLYPH_PENDING,
    GLYPH_STALLED,
    GLYPH_STOPPED,
)
from shikiru.ui.section import (
    BOLD_PRIMARY,
    BOLD_STEEL,
    WrappedGroups,
    grey,
    labelled_rows,
    number,
    row_name,
)
from shikiru.ui.size import format_size
from shikiru.ui.status import status_visual
from shikiru.ui.strip import TaskStrip
from shikiru.ui.tokens import (
    ALERT_COLOR,
    AMBER,
    ENGINE_COLOR,
    PRIMARY_TEXT_COLOR,
    SECONDARY_TEXT_COLOR,
    STYLE_BOLD,
    SUCCESS_COLOR,
)

if TYPE_CHECKING:
    from rich.console import Console, RenderableType

    from shikiru.agents.contracts import CostBasis
    from shikiru.config.settings import Settings
    from shikiru.operator.commands import Ack
    from shikiru.operator.doctor import DoctorReport
    from shikiru.operator.lifecycle import StartResult
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
DOCTOR_OK_LABEL = "ok"
DOCTOR_FAIL_LABEL = "fail"
DOCTOR_OUTCOME_LABEL_WIDTH = len(f"{GLYPH_STOPPED} {DOCTOR_FAIL_LABEL}")
NO_ROWS = "none"
NO_VALUE = "—"
STOPPED_RESULT_FIELD = "stopped"
STOPPING_RESULT_FIELD = "stopping"


def init_renderable(config_path: Path, *, console: Console) -> RenderableType:
    """Render `init --yes` with its config path and model-review warning."""
    absolute_path = config_path.resolve()
    config_text = _path_text(absolute_path, linked=supports_osc8(console))
    result = _outcome_line(GLYPH_DONE, "Initialized", SUCCESS_COLOR)
    result.append(" ")
    result.append_text(config_text)
    result.no_wrap = True
    warning = Text.assemble(
        (GLYPH_STALLED, f"{STYLE_BOLD} {AMBER}"),
        (" Generated model fields are placeholders; ", AMBER),
        ("edit role ladders and run ", PRIMARY_TEXT_COLOR),
        ("shikiru doctor", BOLD_STEEL),
        (" before start.", PRIMARY_TEXT_COLOR),
    )
    return Group(result, warning)


def start_renderable(result: StartResult) -> Text:
    """Render the known engine and session effects of `start`."""
    label = "Engine started" if result.started else "Engine already running"
    text = _outcome_line(GLYPH_DONE, label, SUCCESS_COLOR)
    text.append(" · Session ensured ", style=SECONDARY_TEXT_COLOR)
    text.append(result.session, style=BOLD_PRIMARY)
    return text


def error_renderable(error: ShikiruError) -> RenderableType:
    """Render a domain error with its cause and any known recovery action."""
    text = Text()
    text.append(GLYPH_STOPPED, style=f"{STYLE_BOLD} {ALERT_COLOR}")
    text.append(" ")
    text.append(str(error), style=PRIMARY_TEXT_COLOR)
    if isinstance(error, NotInitialisedError):
        return Group(text, grey("Run shikiru init in the repository root."))
    return text


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
    summary.append("  runs ", STYLE_BOLD)
    summary.append(
        f"{view.run_directories.directories} dirs, {format_size(view.run_directories.size_bytes)}"
    )
    if view.engine_output is not None:
        summary.append(f"\nlast engine output: {view.engine_output}")
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


def ack_renderable(
    ack: Ack, *, stopping: bool = False, kill_session: bool = False
) -> RenderableType:
    """Render an acknowledgement and any known stop effects."""
    match ack.status:
        case AckStatus.DONE:
            label, glyph, color = "Applied", GLYPH_DONE, SUCCESS_COLOR
        case AckStatus.QUEUED:
            label, glyph, color = "Pending", GLYPH_PENDING, AMBER
        case AckStatus.REJECTED:
            label, glyph, color = "Rejected", GLYPH_STOPPED, ALERT_COLOR
    text = _outcome_line(glyph, label, color)
    text.append(" · command ", style=SECONDARY_TEXT_COLOR)
    text.append(str(ack.id), style=BOLD_PRIMARY)
    stop_effect = _stop_effect(ack, kill_session) if stopping else None
    if stop_effect is not None:
        text.append(f" · {stop_effect}", style=SECONDARY_TEXT_COLOR)
    elif ack.message:
        text.append(" · ", style=SECONDARY_TEXT_COLOR)
        text.append(ack.message, style=PRIMARY_TEXT_COLOR)
    return text


def doctor_renderable(report: DoctorReport, *, width: int) -> RenderableType:
    """Render each diagnostic with its complete detail and an aggregate result."""
    rows: list[tuple[Text, tuple[Text, ...]]] = []
    for check in report.checks:
        if check.ok:
            outcome = _outcome_line(GLYPH_DONE, DOCTOR_OK_LABEL, SUCCESS_COLOR)
        else:
            outcome = _outcome_line(GLYPH_STOPPED, DOCTOR_FAIL_LABEL, ALERT_COLOR)
        rows.append(
            (
                outcome,
                (row_name(check.name), Text(check.detail, style=PRIMARY_TEXT_COLOR)),
            )
        )
    checks = labelled_rows(rows, width, DOCTOR_OUTCOME_LABEL_WIDTH)
    if report.ok:
        result = _outcome_line(GLYPH_DONE, "Doctor passed", SUCCESS_COLOR)
        result.append(" · ", style=SECONDARY_TEXT_COLOR)
        result.append(f"{len(report.checks)} checks", style=BOLD_PRIMARY)
    else:
        failed_checks = sum(not check.ok for check in report.checks)
        result = _outcome_line(GLYPH_STOPPED, "Doctor failed", ALERT_COLOR)
        result.append(" · ", style=SECONDARY_TEXT_COLOR)
        result.append(
            f"{failed_checks} of {len(report.checks)} checks failed",
            style=PRIMARY_TEXT_COLOR,
        )
    return Group(checks, result)


def session_rows_renderable(
    rows: list[SessionRow], *, host: str | None, console: Console
) -> RenderableType:
    """Render each session's identity, project and current progress as an open row."""
    heading = Text("Sessions", style=BOLD_STEEL)
    if host is not None:
        heading.append(" · remote host ", style=SECONDARY_TEXT_COLOR)
        heading.append(host, style=BOLD_PRIMARY)
    if not rows:
        return Group(heading, Text("No Shikiru sessions found.", style=SECONDARY_TEXT_COLOR))
    values: list[RenderableType] = [heading]
    for row in rows:
        path = _path_text(
            Path(row.project_dir),
            linked=host is None and supports_osc8(console),
        )
        path_text = Text("  ")
        path_text.append_text(path)
        facts = (
            _session_engine(row.engine_state),
            _session_count("running", row.running),
            _session_count("queued", row.queued),
            _session_count("escalated", row.escalated),
        )
        values.extend((row_name(row.session), path_text, WrappedGroups(facts)))
    return Group(*values)


def _path_text(path: Path, *, linked: bool) -> Text:
    text = Text(str(path), style=SECONDARY_TEXT_COLOR, overflow="fold")
    if linked:
        text.stylize(Style(link=path.as_uri()))
    return text


def _session_count(label: str, count: int) -> Text:
    return Text.assemble((f"{label} ", SECONDARY_TEXT_COLOR), number(str(count)))


def _session_engine(engine_state: Literal["up", "down"]) -> Text:
    engine = Text.assemble((GLYPH_ENGINE, BOLD_STEEL), (" Engine ", SECONDARY_TEXT_COLOR))
    match engine_state:
        case "up":
            engine.append(engine_state, style=f"{STYLE_BOLD} {SUCCESS_COLOR}")
        case "down":
            engine.append(engine_state, style=f"{STYLE_BOLD} {ALERT_COLOR}")
    return engine


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


def _outcome_line(glyph: str, label: str, color: str) -> Text:
    text = Text()
    text.append(glyph, style=f"{STYLE_BOLD} {color}")
    text.append(" ")
    text.append(label, style=f"{STYLE_BOLD} {color}")
    return text


def _stop_effect(ack: Ack, kill_session: bool) -> str | None:
    if ack.status is not AckStatus.DONE or ack.result is None:
        return None
    if ack.result.get(STOPPED_RESULT_FIELD) is False:
        engine_effect = "Engine already stopped"
    elif ack.result.get(STOPPING_RESULT_FIELD) is True:
        engine_effect = "Engine stopped"
    else:
        return None
    session_effect = "session closed" if kill_session else "session unchanged"
    return f"{engine_effect} · {session_effect}"


def _stack(blocks: list[RenderableType]) -> RenderableType:
    return Group(*blocks)
