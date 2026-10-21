"""Rich renderables shared by operator CLI output."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from rich.cells import cell_len
from rich.console import Console, Group
from rich.style import Style
from rich.table import Table
from rich.text import Text

from shikiru.domain.escalation import escalation_reason_label
from shikiru.domain.reports import (
    SEVERITY_BLOCKER,
    ConsultAnswer,
    ImplementationReport,
    PlanReport,
    TriageReport,
    Verdict,
)
from shikiru.domain.scheduling import WaitReason
from shikiru.domain.steps import ACTING_ROLE, Step
from shikiru.domain.tasks import Hold, Status, Task
from shikiru.errors import NotInitialisedError, ShikiruError
from shikiru.operator.commands import AckStatus
from shikiru.operator.doctor import (
    PLUGIN_CHECK_PREFIX,
    PLUGIN_CHECK_SEPARATOR,
    PLUGINS_CHECK_NAME,
    PROBE_CHECK_PREFIX,
)
from shikiru.ui.age import format_duration
from shikiru.ui.compact import compact_count
from shikiru.ui.console import supports_osc8
from shikiru.ui.gauge import Gauge
from shikiru.ui.glyphs import (
    GLYPH_CONSULT,
    GLYPH_DONE,
    GLYPH_ENGINE,
    GLYPH_PENDING,
    GLYPH_STALLED,
    GLYPH_STOPPED,
    ROLE_GLYPHS,
    STAGE_IMPLEMENT,
    STAGE_MERGE,
    STAGE_PLAN,
    STAGE_TRIAGE,
    STAGE_VERIFY,
)
from shikiru.ui.section import (
    BOLD_PRIMARY,
    BOLD_STEEL,
    Section,
    WrappedGroups,
    labelled_rows,
    number,
    row_name,
)
from shikiru.ui.session import (
    escalation_count,
    session_engine_value,
    session_facts,
    session_identity,
)
from shikiru.ui.size import format_size
from shikiru.ui.status import status_visual
from shikiru.ui.tokens import (
    ALERT_COLOR,
    AMBER,
    PRIMARY_TEXT_COLOR,
    SAGE,
    SECONDARY_TEXT_COLOR,
    STEEL_BLUE,
    STYLE_BOLD,
    SUCCESS_COLOR,
)

if TYPE_CHECKING:
    from rich.console import RenderableType

    from shikiru.agents.contracts import QuotaWindow
    from shikiru.domain.escalation import Escalation
    from shikiru.operator.commands import Ack
    from shikiru.operator.doctor import DoctorCheck, DoctorReport
    from shikiru.operator.lifecycle import StartResult
    from shikiru.operator.queries import (
        EscalationView,
        HumanStatusView,
        StatusView,
        TaskDetail,
        TaskSummary,
        UsageView,
    )
    from shikiru.operator.sessions import SessionDiagnostic, SessionRow

STATUS_ROW_STACKED_BELOW_WIDTH = 60
SESSION_TABLE_MIN_WIDTH = 65
SESSION_TABLE_PADDING = 1
SESSION_TABLE_CELL_PADDING_WIDTH = 2
SESSION_TABLE_FIRST_COUNT_COLUMN = 2
SESSION_COUNT_LABELS = ("Running", "Queued", "Escalated")
SESSION_PATH_INDENT = 2
SESSION_COLUMN_LABEL = "Session"
SESSION_ENGINE_LABEL = "Engine"
STATUS_COST_FORMAT = ".2f"
STOPPED_RESULT_FIELD = "stopped"
STOPPING_RESULT_FIELD = "stopping"
STATUS_USAGE_COLUMN_GAP = 2
PLUGIN_AGGREGATE_PART_COUNT = 2
PLUGIN_CHILD_PART_COUNT = 3
DOCTOR_PROBE_PART_COUNT = 2
DOCTOR_CHECK_NAMES = {
    "tmux": "Tmux",
    "git": "Git",
    "repository": "Repository",
    "config": "Configuration",
    "db": "Database",
    "cow": "Copy-on-write",
    "engine-output": "Engine output",
    "plugins": "Agents",
    "config.plugins": "Agent configuration",
}
DOCTOR_PLUGIN_CHECK_NAMES = {"model_choices": "Model choices"}
STATUS_USAGE_TABLE_MIN_WIDTH = 80
STATUS_ESCALATION_NO_STEP_LABEL = "no step"
PRIMARY_QUOTA_SLOT = "primary"
SECONDARY_QUOTA_SLOT = "secondary"
QUOTA_SLOTS = (PRIMARY_QUOTA_SLOT, SECONDARY_QUOTA_SLOT)
STATUS_STEP_GLYPHS = {
    Step.TRIAGE: STAGE_TRIAGE,
    Step.PLAN: STAGE_PLAN,
    Step.IMPLEMENT: STAGE_IMPLEMENT,
    Step.CONSULT: GLYPH_CONSULT,
    Step.VERIFY: STAGE_VERIFY,
    Step.INTEGRATE: STAGE_MERGE,
}
STATUS_STEP_LABELS = {
    Step.TRIAGE: "Triaging",
    Step.PLAN: "Planning",
    Step.IMPLEMENT: "Implementing",
    Step.CONSULT: "Consulting",
    Step.VERIFY: "Verifying",
    Step.INTEGRATE: "Integrating",
}


def init_renderable(
    config_path: Path,
    *,
    console: Console,
    include_placeholder_warning: bool,
) -> RenderableType:
    """Render the config path and the applicable model-review warning."""
    absolute_path = config_path.resolve()
    config_text = _path_text(absolute_path, linked=supports_osc8(console))
    result = _outcome_line(GLYPH_DONE, "Initialized", SUCCESS_COLOR)
    result.append(" ")
    result.append_text(config_text)
    result.no_wrap = True
    if not include_placeholder_warning:
        return result
    warning = Text.assemble(
        (GLYPH_STALLED, f"{STYLE_BOLD} {AMBER}"),
        (" Generated model fields are placeholders; ", AMBER),
        ("edit role ladders and run ", PRIMARY_TEXT_COLOR),
        ("shikiru doctor", BOLD_STEEL),
        (" before start.", PRIMARY_TEXT_COLOR),
    )
    return Group(result, warning)


def start_renderable(result: StartResult) -> Text:
    """Render the engine outcome of `start`."""
    label = "Engine started" if result.started else "Engine already running"
    return Text(label, style=PRIMARY_TEXT_COLOR)


def error_renderable(error: ShikiruError) -> RenderableType:
    """Render a domain error with its cause and any known recovery action."""
    text = Text()
    text.append(GLYPH_STOPPED, style=f"{STYLE_BOLD} {ALERT_COLOR}")
    text.append(" ")
    text.append(str(error), style=PRIMARY_TEXT_COLOR)
    if isinstance(error, NotInitialisedError):
        return Group(
            text,
            Text.assemble(
                ("Initialize this repository with ", PRIMARY_TEXT_COLOR),
                ("shikiru init", BOLD_STEEL),
                (".", PRIMARY_TEXT_COLOR),
            ),
            Text.assemble(
                ("Select another project with ", PRIMARY_TEXT_COLOR),
                ("shikiru --project PATH COMMAND", BOLD_STEEL),
                (".", PRIMARY_TEXT_COLOR),
            ),
            Text.assemble(
                ("Run ", PRIMARY_TEXT_COLOR),
                ("shikiru --help", BOLD_STEEL),
                (" to see available commands.", PRIMARY_TEXT_COLOR),
            ),
        )
    return text


def status_renderable(
    view: HumanStatusView,
    *,
    width: int,
    uptime_seconds: float | None,
    quota_warn_pct: int,
    quota_alert_pct: int,
) -> RenderableType:
    """Render project or task status as open, width-wrapping rows."""
    status = view.status
    if status.task is not None:
        return _task_status_renderable(status.task, status.escalations, width=width)
    return _project_status_renderable(
        status,
        view.task_rows,
        width=width,
        uptime_seconds=uptime_seconds,
        quota_warn_pct=quota_warn_pct,
        quota_alert_pct=quota_alert_pct,
    )


def _project_status_renderable(
    view: StatusView,
    task_rows: tuple[TaskSummary, ...],
    *,
    width: int,
    uptime_seconds: float | None,
    quota_warn_pct: int,
    quota_alert_pct: int,
) -> RenderableType:
    engine_style = BOLD_STEEL if view.engine_up else SECONDARY_TEXT_COLOR
    engine = Text.assemble(
        (GLYPH_ENGINE, BOLD_STEEL if view.engine_up else SECONDARY_TEXT_COLOR),
        (" engine ", SECONDARY_TEXT_COLOR),
        ("up" if view.engine_up else "down", engine_style),
    )
    if view.engine_up and uptime_seconds is not None:
        engine.append(f" ({format_duration(uptime_seconds)})", style=PRIMARY_TEXT_COLOR)
    summary = [
        Text(view.project, style=BOLD_PRIMARY),
        engine,
        _fact("slots", f"{view.slots.active}/{view.slots.maximum}", BOLD_PRIMARY),
        _fact("queued", str(len(view.queued_tasks)), BOLD_PRIMARY),
        Text.assemble(
            ("escalations ", SECONDARY_TEXT_COLOR), escalation_count(len(view.escalations))
        ),
        _fact("space used", format_size(view.run_directories.size_bytes), PRIMARY_TEXT_COLOR),
    ]
    sections = _project_status_sections(
        view,
        task_rows,
        width=width,
        quota_warn_pct=quota_warn_pct,
        quota_alert_pct=quota_alert_pct,
    )
    body: list[RenderableType] = [WrappedGroups(tuple(summary))]
    for section in sections:
        body.extend((Text(""), section))
    return Group(*body)


def _project_status_sections(
    view: StatusView,
    task_rows: tuple[TaskSummary, ...],
    *,
    width: int,
    quota_warn_pct: int,
    quota_alert_pct: int,
) -> list[RenderableType]:
    blocks: list[RenderableType] = []
    if view.engine_output is not None:
        path_row = [
            (
                row_name("last engine output"),
                (Text(view.engine_output, style=SECONDARY_TEXT_COLOR, overflow="fold"),),
            )
        ]
        blocks.append(_status_rows(path_row, width=width))
    if task_rows:
        blocks.append(_task_section("Tasks", list(task_rows), width=width))
    if view.faults:
        blocks.append(_fault_section(view, width=width))
    if view.escalations:
        blocks.append(_escalation_section(view, width=width))
    if view.usage or view.quotas:
        blocks.append(
            _usage_section(
                view.usage,
                view.quotas,
                width=width,
                quota_warn_pct=quota_warn_pct,
                quota_alert_pct=quota_alert_pct,
            )
        )
    return blocks


def _task_status_renderable(
    detail: TaskDetail, escalations: list[EscalationView], *, width: int
) -> RenderableType:
    task = detail.task
    identity = Text.assemble(
        (task.id, BOLD_PRIMARY),
        (" · ", SECONDARY_TEXT_COLOR),
        (task.title, PRIMARY_TEXT_COLOR),
    )
    body: list[RenderableType] = [
        _status_text_rows([identity], width=width),
        WrappedGroups(_task_groups(task)),
    ]

    blockers = _task_blocker_rows(detail, escalations)
    if blockers:
        body.extend((Text(""), _status_rows(blockers, width=width)))
    report_rows = _report_rows(detail)
    if report_rows:
        body.extend((Text(""), _status_text_section("Reports", report_rows, width=width)))
    timeline_rows = _timeline_rows(detail)
    if timeline_rows:
        body.extend((Text(""), _status_text_section("Timeline", timeline_rows, width=width)))
    branch_rows = _branch_rows(detail)
    if branch_rows:
        body.extend((Text(""), _status_section("Branches", branch_rows, width=width)))
    session_rows = _session_rows(detail)
    if session_rows:
        body.extend((Text(""), _status_section("Sessions", session_rows, width=width)))
    if detail.evidence_errors:
        body.extend(
            (
                Text(""),
                _status_text_section(
                    "Evidence",
                    [Text(error, style=AMBER) for error in detail.evidence_errors],
                    width=width,
                ),
            )
        )
    return Group(*body)


def ack_renderable(ack: Ack, *, stopping: bool = False) -> RenderableType:
    """Render an acknowledgement and known lifecycle outcomes."""
    if stopping and ack.status is AckStatus.DONE:
        outcome = _stop_outcome(ack)
        if outcome is not None:
            return Text(outcome, style=PRIMARY_TEXT_COLOR)
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
    if ack.message:
        text.append(" · ", style=SECONDARY_TEXT_COLOR)
        text.append(ack.message, style=PRIMARY_TEXT_COLOR)
    return text


def doctor_renderable(report: DoctorReport, *, width: int) -> RenderableType:
    """Render each diagnostic with its complete detail and an aggregate result."""
    visible_checks = _doctor_display_checks(report.checks)
    wrap_console = Console(width=max(1, width), force_terminal=False, color_system=None)
    rows: list[RenderableType] = []
    for check in visible_checks:
        row = Text(overflow="fold")
        if check.ok:
            row.append(GLYPH_DONE, style=f"{STYLE_BOLD} {SUCCESS_COLOR}")
        else:
            row.append(GLYPH_STOPPED, style=f"{STYLE_BOLD} {ALERT_COLOR}")
        row.append(" ")
        row.append(_doctor_check_name(check.name), style=BOLD_PRIMARY)
        row.append(" · ", style=SECONDARY_TEXT_COLOR)
        row.append(check.detail, style=PRIMARY_TEXT_COLOR)
        rows.extend(row.wrap(wrap_console, max(1, width), overflow="fold"))
    if rows:
        rows.append(Text(""))
    if report.ok:
        result = _outcome_line(GLYPH_DONE, "Doctor passed", SUCCESS_COLOR)
        result.append(" · ", style=SECONDARY_TEXT_COLOR)
        result.append(f"{len(visible_checks)} checks", style=BOLD_PRIMARY)
    else:
        failed_checks = sum(not check.ok for check in visible_checks)
        result = _outcome_line(GLYPH_STOPPED, "Doctor failed", ALERT_COLOR)
        result.append(" · ", style=SECONDARY_TEXT_COLOR)
        result.append(
            f"{failed_checks} of {len(visible_checks)} checks failed",
            style=PRIMARY_TEXT_COLOR,
        )
    return Group(*rows, result)


def _doctor_display_checks(checks: list[DoctorCheck]) -> list[DoctorCheck]:
    failed_plugins = {
        parts[1]
        for check in checks
        if not check.ok
        and (parts := check.name.split(PLUGIN_CHECK_SEPARATOR))[0] == PLUGIN_CHECK_PREFIX
        and len(parts) >= PLUGIN_AGGREGATE_PART_COUNT
    }
    failed_plugins_with_details = {
        parts[1]
        for check in checks
        if not check.ok
        and (parts := check.name.split(PLUGIN_CHECK_SEPARATOR))[0] == PLUGIN_CHECK_PREFIX
        and len(parts) >= PLUGIN_CHILD_PART_COUNT
    }
    plugins_with_details = {
        parts[1]
        for check in checks
        if (parts := check.name.split(PLUGIN_CHECK_SEPARATOR))[0] == PLUGIN_CHECK_PREFIX
        and len(parts) >= PLUGIN_CHILD_PART_COUNT
    }
    return [
        check
        for check in checks
        if not (
            (check.name == PLUGINS_CHECK_NAME and failed_plugins)
            or (check.ok and check.name == PLUGINS_CHECK_NAME and plugins_with_details)
            or (
                not check.ok
                and (parts := check.name.split(PLUGIN_CHECK_SEPARATOR))[0] == PLUGIN_CHECK_PREFIX
                and len(parts) == PLUGIN_AGGREGATE_PART_COUNT
                and parts[1] in failed_plugins_with_details
            )
            or (
                check.ok
                and (parts := check.name.split(PLUGIN_CHECK_SEPARATOR))[0] == PLUGIN_CHECK_PREFIX
                and len(parts) == PLUGIN_AGGREGATE_PART_COUNT
                and parts[1] in plugins_with_details
            )
        )
    ]


def _doctor_check_name(name: str) -> str:
    if name in DOCTOR_CHECK_NAMES:
        return DOCTOR_CHECK_NAMES[name]
    prefix, *parts = name.split(PLUGIN_CHECK_SEPARATOR)
    if prefix == PLUGIN_CHECK_PREFIX:
        if not parts:
            return name
        return _doctor_plugin_check_name(parts)
    if prefix == PROBE_CHECK_PREFIX:
        return _doctor_probe_check_name(name, parts)
    return name


def _doctor_plugin_check_name(parts: list[str]) -> str:
    if not parts:
        return "Agent check"
    if len(parts) == 1:
        return f"Agent {parts[0]}"
    check_name = PLUGIN_CHECK_SEPARATOR.join(parts[1:])
    display_check = DOCTOR_PLUGIN_CHECK_NAMES.get(check_name, check_name)
    return f"Agent {parts[0]} · {display_check}"


def _doctor_probe_check_name(name: str, parts: list[str]) -> str:
    if len(parts) < DOCTOR_PROBE_PART_COUNT:
        return name
    return f"{parts[0]} probe · {' · '.join(parts[1:])}"


def session_rows_renderable(
    rows: list[SessionRow],
    *,
    host: str | None,
    console: Console,
    unavailable: bool = False,
) -> RenderableType:
    """Render each session's identity, project and current progress as an open row."""
    heading: list[RenderableType] = []
    if host is not None:
        heading.append(
            Text.assemble(
                ("Remote host ", SECONDARY_TEXT_COLOR),
                (host, BOLD_PRIMARY),
            )
        )
    if not rows:
        message = (
            "No usable Shikiru sessions found." if unavailable else "No Shikiru sessions found."
        )
        return Group(*heading, Text(message, style=SECONDARY_TEXT_COLOR))
    if console.width < SESSION_TABLE_MIN_WIDTH:
        return Group(
            *heading,
            *(_stacked_session(row, console=console, host=host) for row in rows),
        )
    return Group(*heading, _session_table(rows, console=console, host=host))


def session_diagnostics_renderable(
    diagnostics: tuple[SessionDiagnostic, ...],
    *,
    remote_host: str | None = None,
) -> RenderableType:
    """Render unavailable local sessions with literal causes and repair commands."""
    rows: list[RenderableType] = []
    for diagnostic in diagnostics:
        identity = Text("Unavailable session ", style=SECONDARY_TEXT_COLOR)
        identity.append(diagnostic.session, style=BOLD_PRIMARY)
        path = Text("Project ", style=SECONDARY_TEXT_COLOR)
        path.append(diagnostic.project_dir or "path not recorded", style=PRIMARY_TEXT_COLOR)
        reason = Text("Reason ", style=SECONDARY_TEXT_COLOR)
        reason.append(diagnostic.reason, style=PRIMARY_TEXT_COLOR)
        rows.extend((identity, path, reason))
        rows.extend(
            Text.assemble(("Repair: ", SECONDARY_TEXT_COLOR), (command, BOLD_STEEL))
            for command in diagnostic.repair_commands
        )
        rows.append(Text(diagnostic.note, style=SECONDARY_TEXT_COLOR))
    if remote_host is not None:
        rows.append(
            Text(
                f"Run the repair commands above on SSH host {remote_host}.",
                style=SECONDARY_TEXT_COLOR,
            )
        )
    return Group(*rows)


def session_options_renderable(
    rows: tuple[SessionRow, ...], *, host: str | None, console: Console
) -> RenderableType:
    """Render numbered, full-identity options for the terminal attach prompt."""
    values: list[RenderableType] = []
    if host is not None:
        values.append(Text.assemble(("Remote host ", SECONDARY_TEXT_COLOR), (host, BOLD_PRIMARY)))
    for index, row in enumerate(rows, start=1):
        identity = Text.assemble((f"{index}. ", BOLD_STEEL))
        identity.append_text(session_identity(row.session))
        path = _path_text(Path(row.project_dir), linked=host is None and supports_osc8(console))
        values.extend(
            (
                identity,
                *_wrap_text(path, width=console.width, indent=SESSION_PATH_INDENT),
                WrappedGroups(
                    session_facts(row.engine_state, row.running, row.queued, row.escalated)
                ),
            )
        )
    return Group(*values)


def _stacked_session(row: SessionRow, *, console: Console, host: str | None) -> RenderableType:
    path = _path_text(Path(row.project_dir), linked=host is None and supports_osc8(console))
    return Group(
        *_wrap_text(session_identity(row.session), width=console.width),
        *_wrap_text(path, width=console.width, indent=SESSION_PATH_INDENT),
        WrappedGroups(session_facts(row.engine_state, row.running, row.queued, row.escalated)),
    )


def _session_table(rows: list[SessionRow], *, console: Console, host: str | None) -> RenderableType:
    widths = _session_table_widths(rows, console.width)
    values: list[RenderableType] = [_session_table_line(widths, _session_table_headers())]
    for index, row in enumerate(rows):
        if index:
            values.append(Text(""))
        cells = _session_table_cells(row)
        if cell_len(cells[0].plain) > widths[0]:
            values.extend(_wrap_text(cells[0], width=console.width))
            cells = (Text(""), *cells[1:])
        values.append(_session_table_line(widths, cells))
        path = _path_text(Path(row.project_dir), linked=host is None and supports_osc8(console))
        values.extend(_wrap_text(path, width=console.width, indent=SESSION_PATH_INDENT))
    return Group(*values)


def _session_table_widths(rows: list[SessionRow], width: int) -> tuple[int, ...]:
    headers = _session_table_headers()
    cells = tuple(_session_table_cells(row) for row in rows)
    columns = tuple(
        max(cell_len(header.plain), *(cell_len(row[index].plain) for row in cells))
        for index, header in enumerate(headers[1:], start=1)
    )
    padding_width = len(headers) * SESSION_TABLE_CELL_PADDING_WIDTH * SESSION_TABLE_PADDING
    session_width = max(1, width - sum(columns) - padding_width)
    return (session_width, *columns)


def _session_table_headers() -> tuple[Text, ...]:
    return (
        Text(SESSION_COLUMN_LABEL, style=SECONDARY_TEXT_COLOR),
        Text(SESSION_ENGINE_LABEL, style=SECONDARY_TEXT_COLOR),
        *(Text(label, style=SECONDARY_TEXT_COLOR) for label in SESSION_COUNT_LABELS),
    )


def _session_table_cells(row: SessionRow) -> tuple[Text, ...]:
    return (
        session_identity(row.session),
        session_engine_value(row.engine_state),
        number(str(row.running)),
        number(str(row.queued)),
        escalation_count(row.escalated),
    )


def _session_table_line(widths: tuple[int, ...], cells: tuple[Text, ...]) -> Table:
    table = Table.grid(padding=(0, SESSION_TABLE_PADDING), expand=True)
    for index, width in enumerate(widths):
        table.add_column(
            width=width,
            overflow="fold",
            justify="right" if index >= SESSION_TABLE_FIRST_COUNT_COLUMN else "left",
        )
    table.add_row(*cells)
    return table


def _path_text(path: Path, *, linked: bool) -> Text:
    text = Text(str(path), style=SECONDARY_TEXT_COLOR, overflow="fold")
    if linked:
        text.stylize(Style(link=path.as_uri()))
    return text


def _wrap_text(text: Text, *, width: int, indent: int = 0) -> list[Text]:
    line_width = max(1, width - indent)
    console = Console(width=line_width, force_terminal=False, color_system=None)
    lines = text.wrap(console, line_width, overflow="fold")
    if indent:
        indented: list[Text] = []
        for line in lines:
            leading = Text(" " * indent, style=SECONDARY_TEXT_COLOR)
            leading.append_text(line)
            indented.append(leading)
        return indented
    return list(lines)


def _status_section(
    title: str, rows: list[tuple[Text, tuple[Text, ...]]], *, width: int
) -> Section:
    return _section(title, _status_rows(rows, width=width))


def _status_text_section(title: str, rows: list[Text], *, width: int) -> Section:
    return _section(title, _status_text_rows(rows, width=width))


def _section(title: str, body: RenderableType) -> Section:
    return Section(title, body, ruled=False, title_style=BOLD_STEEL)


def _status_rows(rows: list[tuple[Text, tuple[Text, ...]]], *, width: int) -> RenderableType:
    return labelled_rows(
        rows, width, _task_label_width(rows), stack_below=STATUS_ROW_STACKED_BELOW_WIDTH
    )


def _status_text_rows(rows: list[Text], *, width: int) -> RenderableType:
    table = Table.grid(padding=0)
    table.add_column(width=width, overflow="fold")
    for row in rows:
        table.add_row(row)
    return table


def _fact(label: str, value: str, value_style: str = PRIMARY_TEXT_COLOR) -> Text:
    return Text.assemble((f"{label} ", SECONDARY_TEXT_COLOR), (value, value_style))


def _task_groups(task: TaskSummary | Task) -> tuple[Text, ...]:
    visual = status_visual(task.status)
    status_style = f"{STYLE_BOLD} {AMBER}" if task.status is Status.ESCALATED else visual.style
    groups = [Text(f"{visual.glyph} {task.status.value}", style=status_style)]
    if task.step is not None:
        step = task.step if isinstance(task.step, Step) else Step(task.step)
        role = ACTING_ROLE[step]
        groups.extend(
            (
                Text(f"{STATUS_STEP_GLYPHS[step]} {step.value.title()}", style=BOLD_STEEL),
                Text(f"{ROLE_GLYPHS[role]} {role.value.title()}", style=BOLD_STEEL),
            )
        )
    if task.slot is not None:
        groups.append(_fact("slot", str(task.slot), BOLD_PRIMARY))
    if task.wait_reason is not None:
        groups.append(_fact("wait", task.wait_reason, AMBER))
    if task.holds:
        holds = ", ".join(sorted(task.holds))
        groups.append(_fact("holds", holds, AMBER))
    if task.status is Status.QUEUED:
        groups.append(_fact("priority", str(task.priority), BOLD_PRIMARY))
    return tuple(groups)


def _fault_section(view: StatusView, *, width: int) -> Section:
    rows: list[tuple[Text, tuple[Text, ...]]] = []
    for fault in view.faults:
        scope = fault.scope if fault.task_id is None else f"{fault.scope} · {fault.task_id}"
        recoverable = "yes" if fault.recoverable else "no"
        rows.append(
            (
                row_name(scope),
                (
                    _fact("error", fault.error),
                    _fact("first", fault.first_seen.isoformat()),
                    _fact("last", fault.last_seen.isoformat()),
                    _fact("repeats", str(fault.repeat), BOLD_PRIMARY),
                    _fact("recoverable", recoverable, BOLD_PRIMARY),
                ),
            )
        )
    return _status_section("Faults", rows, width=width)


def _escalation_section(view: StatusView, *, width: int) -> Section:
    rows: list[Text] = []
    for item in view.escalations:
        escalation = item.escalation
        row = Text(overflow="fold")
        row.append(escalation.id, style=f"{STYLE_BOLD} {AMBER}")
        row.append(" · ", style=SECONDARY_TEXT_COLOR)
        row.append(escalation.task_id, style=BOLD_STEEL)
        row.append(" · ", style=SECONDARY_TEXT_COLOR)
        row.append(_status_escalation_headline(escalation), style=PRIMARY_TEXT_COLOR)
        rows.append(row)
    return _section("Escalations", _status_text_rows(rows, width=width))


def _status_escalation_headline(escalation: Escalation) -> str:
    step = escalation.step.value if escalation.step is not None else STATUS_ESCALATION_NO_STEP_LABEL
    prefix = f"{escalation.task_id} · {step} · {escalation.reason.value}: "
    return escalation.headline.removeprefix(prefix)


def _usage_section(
    usages: list[UsageView],
    quotas: dict[str, list[QuotaWindow]],
    *,
    width: int,
    quota_warn_pct: int,
    quota_alert_pct: int,
) -> Section:
    usage_by_agent = {usage.agent: usage for usage in usages}
    agents = sorted(usage_by_agent.keys() | quotas.keys())
    if width < STATUS_USAGE_TABLE_MIN_WIDTH:
        rows = [
            (
                row_name(agent),
                _usage_groups(
                    usage_by_agent.get(agent),
                    quotas.get(agent, []),
                    quota_warn_pct=quota_warn_pct,
                    quota_alert_pct=quota_alert_pct,
                ),
            )
            for agent in agents
        ]
        return _status_section("Usage", rows, width=width)

    table = Table.grid(expand=False, padding=(0, STATUS_USAGE_COLUMN_GAP))
    headers = ("Agent", "Runs", "Input", "Output", "Costs", "Quota used")
    for index, header in enumerate(headers):
        table.add_column(
            header,
            justify="left" if index in (0, len(headers) - 1) else "right",
            overflow="fold",
        )
    table.add_row(*(Text(header, style=SECONDARY_TEXT_COLOR) for header in headers))
    for agent in agents:
        usage = usage_by_agent.get(agent)
        table.add_row(
            Text(agent, style=BOLD_PRIMARY),
            Text(str(usage.run_count if usage is not None else 0), style=BOLD_PRIMARY),
            _token_total(usage, input_tokens=True),
            _token_total(usage, input_tokens=False),
            _cost_total(usage),
            _quota_cell(
                quotas.get(agent, []),
                quota_warn_pct=quota_warn_pct,
                quota_alert_pct=quota_alert_pct,
            ),
        )
    return _section("Usage", table)


def _usage_groups(
    usage: UsageView | None,
    windows: list[QuotaWindow],
    *,
    quota_warn_pct: int,
    quota_alert_pct: int,
) -> tuple[Text, ...]:
    return (
        _fact("Runs", str(usage.run_count if usage is not None else 0), BOLD_PRIMARY),
        _labeled_value("Input", _token_total(usage, input_tokens=True)),
        _labeled_value("Output", _token_total(usage, input_tokens=False)),
        _labeled_value("Costs", _cost_total(usage)),
        _labeled_value(
            "Quota used",
            _quota_cell(
                windows,
                quota_warn_pct=quota_warn_pct,
                quota_alert_pct=quota_alert_pct,
            ),
        ),
    )


def _labeled_value(label: str, value: Text) -> Text:
    result = Text(label, style=SECONDARY_TEXT_COLOR)
    result.append(" ", style=SECONDARY_TEXT_COLOR)
    result.append_text(value)
    return result


def _token_total(usage: UsageView | None, *, input_tokens: bool) -> Text:
    if usage is None or usage.run_count == 0:
        return Text("unknown", style=SECONDARY_TEXT_COLOR)
    known_runs = usage.usage_reported_runs + usage.legacy_token_runs
    if known_runs != usage.run_count:
        return Text("unknown", style=SECONDARY_TEXT_COLOR)
    tokens = usage.input_tokens if input_tokens else usage.output_tokens
    return Text(compact_count(tokens), style=BOLD_STEEL)


def _cost_total(usage: UsageView | None) -> Text:
    if (
        usage is None
        or usage.run_count == 0
        or usage.billed_cost_runs != usage.run_count
        or usage.billed_cost_usd is None
    ):
        return Text("unknown", style=SECONDARY_TEXT_COLOR)
    return Text(f"${usage.billed_cost_usd:{STATUS_COST_FORMAT}}", style=SAGE)


def _quota_cell(windows: list[QuotaWindow], *, quota_warn_pct: int, quota_alert_pct: int) -> Text:
    if not windows:
        return Text("unknown", style=SECONDARY_TEXT_COLOR)
    by_name = {window.name: window for window in windows}
    canonical = [by_name.get(slot) for slot in QUOTA_SLOTS]
    other_windows = [window for window in windows if window.name not in QUOTA_SLOTS]
    if not any(canonical):
        canonical = []
        other_windows = sorted(windows, key=lambda item: item.name)
    cell = Text()
    for index, window in enumerate(canonical):
        if index:
            cell.append(" / ", style=SECONDARY_TEXT_COLOR)
        cell.append_text(
            Text("unknown", style=SECONDARY_TEXT_COLOR)
            if window is None
            else Gauge("", window.used_pct, quota_warn_pct, quota_alert_pct).render()
        )
    for window in other_windows:
        if cell:
            cell.append(" · ", style=SECONDARY_TEXT_COLOR)
        cell.append_text(
            Gauge(
                window.name.replace("_", " "),
                window.used_pct,
                quota_warn_pct,
                quota_alert_pct,
            ).render()
        )
    return cell


def _task_blocker_rows(
    detail: TaskDetail, escalations: list[EscalationView]
) -> list[tuple[Text, tuple[Text, ...]]]:
    task = detail.task
    rows: list[tuple[Text, tuple[Text, ...]]] = []
    if task.status_reason is not None:
        rows.append((row_name("Outcome"), (Text(task.status_reason, style=SECONDARY_TEXT_COLOR),)))
    rows.extend(
        (
            row_name("Escalation"),
            (
                Text(item.escalation.id, style=BOLD_PRIMARY),
                Text(escalation_reason_label(item.escalation.reason), style=AMBER),
                Text(item.escalation.headline, style=PRIMARY_TEXT_COLOR, overflow="fold"),
            ),
        )
        for item in escalations
        if item.escalation.task_id == task.id
    )
    return rows


def _report_rows(detail: TaskDetail) -> list[Text]:
    rows: list[Text] = []
    for report in detail.reports:
        rows.append(
            Text.assemble(
                (report.turn, BOLD_PRIMARY),
                (" · ", SECONDARY_TEXT_COLOR),
                (report.kind.value, BOLD_STEEL),
                (" · ", SECONDARY_TEXT_COLOR),
                (_report_summary(report.data), PRIMARY_TEXT_COLOR),
            )
        )
        if isinstance(report.data, Verdict):
            rows.extend(
                Text.assemble(
                    (
                        f"  {issue.severity} · ",
                        ALERT_COLOR if issue.severity == SEVERITY_BLOCKER else AMBER,
                    ),
                    (issue.description, PRIMARY_TEXT_COLOR),
                )
                for issue in report.data.issues
            )
            rows.extend(
                Text.assemble(("  residual risk · ", AMBER), (risk, PRIMARY_TEXT_COLOR))
                for risk in report.data.residual_risks
            )
    return rows


def _timeline_rows(detail: TaskDetail) -> list[Text]:
    return [
        Text.assemble(
            (event.ts.isoformat(), SECONDARY_TEXT_COLOR),
            (" · ", SECONDARY_TEXT_COLOR),
            (event.kind.value, BOLD_STEEL),
            (" · ", SECONDARY_TEXT_COLOR),
            (event.message, PRIMARY_TEXT_COLOR),
        )
        for event in detail.events
    ]


def _branch_rows(detail: TaskDetail) -> list[tuple[Text, tuple[Text, ...]]]:
    branches = list(detail.branches)
    repo = detail.task.repo
    if repo is not None and repo.branch not in branches:
        branches.append(repo.branch)
    rows: list[tuple[Text, tuple[Text, ...]]] = []
    for branch in branches:
        facts: list[Text] = []
        if repo is not None and branch == repo.branch:
            facts.extend(
                (
                    _fact("base", repo.base),
                    _fact("base ref", repo.base_ref),
                    _fact("base sha", repo.base_sha),
                )
            )
        rows.append((row_name(branch), tuple(facts)))
    return rows


def _session_rows(detail: TaskDetail) -> list[tuple[Text, tuple[Text, ...]]]:
    rows: list[tuple[Text, tuple[Text, ...]]] = []
    for run in detail.sessions:
        label = run.role.value.title()
        session_id = run.session_id if run.session_id is not None else "session id unavailable"
        facts = [
            Text(run.status.value, style=BOLD_STEEL),
            Text(f"{run.agent}/{run.model}", style=PRIMARY_TEXT_COLOR, overflow="fold"),
            _fact("session", session_id),
        ]
        if run.effort is not None:
            facts.append(_fact("effort", run.effort))
        rows.append((row_name(label), tuple(facts)))
    return rows


def _task_label_width(rows: list[tuple[Text, tuple[Text, ...]]]) -> int:
    return max((cell_len(label.plain) for label, _ in rows), default=0)


def _task_section(title: str, tasks: list[TaskSummary], *, width: int) -> Section:
    rows: list[Text] = []
    for task in tasks:
        row = Text(overflow="fold")
        row.append(task.id, style=BOLD_PRIMARY)
        row.append(" · ", style=SECONDARY_TEXT_COLOR)
        row.append(task.title, style=PRIMARY_TEXT_COLOR)
        row.append(" · ", style=SECONDARY_TEXT_COLOR)
        state, state_color = _task_state(task)
        row.append(state, style=state_color)
        rows.append(row)
    return Section(
        title,
        _status_text_rows(rows, width=width),
        ruled=False,
        title_style=BOLD_STEEL,
    )


def _task_state(task: TaskSummary) -> tuple[str, str]:
    if task.status is Status.DONE:
        return "Done", SAGE
    if Hold.PAUSED.value in task.holds or task.wait_reason == WaitReason.PAUSED.value:
        return "Paused", AMBER
    if Hold.INTERVENTION.value in task.holds:
        return "Operator control", AMBER
    if task.wait_reason is not None:
        return f"Waiting on {task.wait_reason.replace('_', ' ')}", AMBER
    if task.status is Status.RUNNING and task.step is not None:
        step = task.step if isinstance(task.step, Step) else Step(task.step)
        return STATUS_STEP_LABELS[step], STEEL_BLUE
    return {
        Status.QUEUED: ("Queued", SECONDARY_TEXT_COLOR),
        Status.RUNNING: ("Running", STEEL_BLUE),
        Status.INTEGRATING: ("Integrating", STEEL_BLUE),
        Status.ESCALATED: ("Escalated", AMBER),
        Status.DONE: ("Done", SAGE),
        Status.CANCELLED: ("Cancelled", SECONDARY_TEXT_COLOR),
    }[task.status]


def _report_summary(
    report: TriageReport | ImplementationReport | PlanReport | ConsultAnswer | Verdict,
) -> str:
    match report:
        case TriageReport(title=title):
            return title
        case ImplementationReport(status=status, summary=summary):
            return f"{status}: {summary}" if summary else status
        case PlanReport(summary=summary):
            return summary
        case ConsultAnswer(decision=decision, answer=answer):
            return f"{decision}: {answer}" if answer else decision
        case Verdict(decision=decision, summary=summary):
            return f"{decision}: {summary}" if summary else decision


def _outcome_line(glyph: str, label: str, color: str) -> Text:
    text = Text()
    text.append(glyph, style=f"{STYLE_BOLD} {color}")
    text.append(" ")
    text.append(label, style=f"{STYLE_BOLD} {color}")
    return text


def _stop_outcome(ack: Ack) -> str | None:
    if ack.status is not AckStatus.DONE or ack.result is None:
        return None
    if ack.result.get(STOPPED_RESULT_FIELD) is False:
        return "Engine already stopped"
    if ack.result.get(STOPPING_RESULT_FIELD) is True:
        return "Engine stopped"
    return None
