"""Rich renderables shared by operator CLI output."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import TYPE_CHECKING

from rich.cells import cell_len
from rich.console import Console, Group
from rich.style import Style
from rich.table import Table
from rich.text import Text

from shikiru.domain.escalation import escalation_reason_label
from shikiru.domain.events import EventKind
from shikiru.domain.reports import (
    SEVERITY_BLOCKER,
    ConsultAnswer,
    ImplementationReport,
    PlanReport,
    TriageReport,
    Verdict,
)
from shikiru.domain.runs import RunStatus
from shikiru.domain.scheduling import WaitReason
from shikiru.domain.steps import Step
from shikiru.domain.tasks import Hold, Status, Task
from shikiru.errors import (
    CommandFailed,
    CommandRejectedError,
    CommandTimeoutError,
    ConfigError,
    DatabaseError,
    DetachedProcessError,
    HeadlessOutputError,
    InvariantError,
    NotInitialisedError,
    ProcessReplacementError,
    ProjectNotRepositoryRootError,
    RepositoryHasNoCommitsError,
    ShikiruError,
    StaleTaskBranchError,
    UsageError,
)
from shikiru.operator.commands import AckStatus
from shikiru.operator.doctor import (
    PLUGIN_CHECK_PREFIX,
    PLUGIN_CHECK_SEPARATOR,
    PLUGINS_CHECK_NAME,
    PROBE_CHECK_PREFIX,
)
from shikiru.operator.queries import TaskNotFoundError
from shikiru.ui.age import format_duration
from shikiru.ui.compact import compact_count
from shikiru.ui.console import supports_osc8
from shikiru.ui.gauge import Gauge
from shikiru.ui.glyphs import (
    GLYPH_DONE,
    GLYPH_ENGINE,
    GLYPH_PENDING,
    GLYPH_STOPPED,
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
from shikiru.paths import project_config_path

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
_ERROR_SUMMARIES: dict[type[ShikiruError], str] = {
    InvariantError: "Shikiru encountered an internal invariant failure",
    UsageError: "The command arguments are invalid",
    CommandRejectedError: "The engine rejected the command",
    StaleTaskBranchError: "The existing task branch does not match the task base",
    ConfigError: "Configuration is missing or invalid",
    HeadlessOutputError: "An agent returned invalid structured output",
    DatabaseError: "The project state database operation failed",
    CommandFailed: "A command exited unsuccessfully",
    CommandTimeoutError: "A command timed out",
    DetachedProcessError: "A background process could not be launched",
    ProcessReplacementError: "The terminal handoff could not be started",
}
STATUS_USAGE_COLUMN_PADDING = 2
STATUS_PAUSED_LABEL = "Paused"
STATUS_OPERATOR_CONTROL_LABEL = "Operator control"
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
STATUS_USAGE_HEADERS = ("Agent", "Runs", "Input", "Output", "Costs", "Quota used 5h/7d")
ESCALATION_EVENT_HEADLINE_SEPARATORS = (" · ", ": ")
QUOTA_PERIODS = ("5h", "7d")
QUOTA_PROVIDER_SLOTS = ("primary", "secondary")
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
    if not include_placeholder_warning:
        result = _outcome_line(GLYPH_DONE, "Initialized", SUCCESS_COLOR)
        result.append(" ")
        result.append_text(config_text)
        result.no_wrap = True
        return result
    next_step = (
        (
            "Models",
            "Generated model fields are placeholders; review role ladders before start.",
            None,
        ),
        ("Doctor", "Verify the project environment before start.", "shikiru doctor"),
    )
    return _outcome_renderable(
        "Project initialized",
        GLYPH_DONE,
        SUCCESS_COLOR,
        details=(("Config", config_text),),
        next_step=next_step,
    )


def start_renderable(result: StartResult) -> Text:
    """Render the engine outcome of `start`."""
    label = "Engine started" if result.started else "Engine already running"
    return _outcome_line_for_cli(label, GLYPH_DONE, SUCCESS_COLOR)


def error_renderable(error: ShikiruError) -> RenderableType:
    """Render a domain error with its cause and any known recovery action."""
    if isinstance(error, ProjectNotRepositoryRootError):
        return _repository_root_error_renderable(error)
    if isinstance(error, TaskNotFoundError):
        project = shlex.quote(str(error.project_root))
        return _outcome_renderable(
            f"Task {error.task_id} was not found",
            GLYPH_STOPPED,
            ALERT_COLOR,
            details=(("Cause", str(error)),),
            repair=(
                (
                    "Project status",
                    "Show recent tasks and select an existing task ID.",
                    f"shikiru --project {project} status",
                ),
            ),
        )
    if isinstance(error, RepositoryHasNoCommitsError):
        project = shlex.quote(str(error.project_root))
        return _outcome_renderable(
            "Git repository has no commits",
            GLYPH_STOPPED,
            ALERT_COLOR,
            details=(
                ("Project", str(error.project_root)),
                ("Cause", "HEAD does not identify a commit."),
            ),
            repair=(
                (
                    "First commit",
                    "Create an initial commit without staging project files.",
                    f"git -C {project} commit --allow-empty -m 'Initial commit'",
                ),
                (
                    "Initialize",
                    "Run initialization after the first commit exists.",
                    f"shikiru --project {project} init --yes",
                ),
            ),
        )
    if isinstance(error, NotInitialisedError):
        project_root = error.project_root
        missing_path = error.missing_path
        project = shlex.quote(str(project_root)) if project_root is not None else None
        missing_config = project_root is not None and missing_path == project_config_path(
            project_root
        )
        summary = (
            "Project configuration is missing" if missing_config else "Project state is missing"
        )
        details = tuple(
            (label, str(value))
            for label, value in (("Project", project_root), ("Missing", missing_path))
            if value is not None
        )
        if project_root is None or missing_path is None:
            details += (("Cause", str(error)),)
        initialize = f"shikiru --project {project} init" if project is not None else "shikiru init"
        return _outcome_renderable(
            summary if project_root is not None else "Project is not initialized",
            GLYPH_STOPPED,
            ALERT_COLOR,
            details=details,
            repair=(
                (
                    "Initialize",
                    "Create fresh Shikiru state for this repository.",
                    initialize,
                ),
                (
                    "Project root",
                    "Run this command for a different project root.",
                    "shikiru --project PATH COMMAND",
                ),
                ("Help", "Review available commands.", "shikiru --help"),
            ),
        )
    return _outcome_renderable(
        _error_summary(error),
        GLYPH_STOPPED,
        ALERT_COLOR,
        details=(("Cause", str(error)),),
    )


def _error_summary(error: ShikiruError) -> str:
    return _ERROR_SUMMARIES.get(type(error), str(error)) or "Shikiru reported an unspecified error"


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
    engine_style = f"{STYLE_BOLD} {SUCCESS_COLOR}" if view.engine_up else SECONDARY_TEXT_COLOR
    engine = Text.assemble(
        (GLYPH_ENGINE, engine_style),
        (" up" if view.engine_up else " down", engine_style),
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
    state, state_color = _task_identity_state(task)
    identity = Text.assemble(
        (task.id, BOLD_PRIMARY),
        (" · ", SECONDARY_TEXT_COLOR),
        (task.title, PRIMARY_TEXT_COLOR),
        (" · ", SECONDARY_TEXT_COLOR),
        (state, f"{STYLE_BOLD} {state_color}"),
    )
    body: list[RenderableType] = [_status_text_rows([identity], width=width)]

    blockers = _task_blocker_rows(detail, escalations)
    if blockers:
        body.extend((Text(""), _status_section("Attention", blockers, width=width)))
    progress_rows = _task_progress_rows(task, state)
    if progress_rows:
        body.extend((Text(""), _status_section("Progress", progress_rows, width=width)))
    report_rows = _latest_report_rows(detail)
    if report_rows:
        body.extend(
            (
                Text(""),
                _status_text_section(
                    f"Latest report · 1 of {len(detail.reports)}", report_rows, width=width
                ),
            )
        )
    timeline_rows = _latest_event_rows(detail, escalations)
    if timeline_rows:
        body.extend(
            (
                Text(""),
                _status_text_section(
                    f"Latest event · 1 of {len(detail.events)}", timeline_rows, width=width
                ),
            )
        )
    branch_rows = _branch_rows(detail)
    if branch_rows:
        body.extend((Text(""), _status_text_section("Branches", branch_rows, width=width)))
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
            return _outcome_renderable(outcome, GLYPH_DONE, SUCCESS_COLOR)
        return _outcome_renderable(
            "Stop command acknowledged",
            GLYPH_DONE,
            SUCCESS_COLOR,
            details=_ack_details(ack, None),
        )
    task_id = _ack_task_id(ack)
    match ack.status:
        case AckStatus.DONE:
            next_step = (
                (
                    (
                        "Progress",
                        "Follow the accepted task in project status.",
                        f"shikiru status {task_id}",
                    ),
                )
                if task_id is not None
                else ()
            )
            return _outcome_renderable(
                "Task created" if task_id is not None else "Command applied",
                GLYPH_DONE,
                SUCCESS_COLOR,
                details=_ack_details(ack, task_id),
                next_step=next_step,
            )
        case AckStatus.QUEUED:
            return _outcome_renderable(
                "Task submission remains pending"
                if task_id is not None
                else "Command remains pending",
                GLYPH_PENDING,
                AMBER,
                details=_ack_details(ack, task_id),
                next_step=(
                    ("Status", "Check whether the engine handled this request.", "shikiru status"),
                ),
            )
        case AckStatus.REJECTED:
            return _outcome_renderable(
                "Task submission was rejected" if task_id is not None else "Command was rejected",
                GLYPH_STOPPED,
                ALERT_COLOR,
                details=_ack_details(ack, task_id),
            )


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
    if not rows and unavailable:
        return Group()
    partial = bool(rows and unavailable)
    lines: list[RenderableType] = [
        _outcome_line_for_cli(
            "Session discovery is partial" if partial else "Shikiru sessions discovered",
            GLYPH_PENDING if partial else GLYPH_DONE,
            AMBER if partial else SUCCESS_COLOR,
        )
    ]
    if not rows:
        lines[0] = _outcome_line_for_cli("No Shikiru sessions found", GLYPH_DONE, SUCCESS_COLOR)
    if rows:
        _append_outcome_heading(lines, "Details")
        if host is not None:
            lines.append(_outcome_detail_line("Host", host))
    elif host is not None:
        _append_outcome_section(lines, "Details", (("Host", host),))
    if rows:
        if console.width < SESSION_TABLE_MIN_WIDTH:
            lines.extend(_stacked_session(row, console=console, host=host) for row in rows)
        else:
            lines.append(_session_table(rows, console=console, host=host))
    return Group(*lines)


def session_diagnostics_renderable(
    diagnostics: tuple[SessionDiagnostic, ...],
    *,
    remote_host: str | None = None,
) -> RenderableType:
    """Render unavailable local sessions with literal causes and repair commands."""
    rows: list[RenderableType] = []
    for diagnostic in diagnostics:
        details: list[tuple[str, str]] = []
        if remote_host is not None:
            details.append(("Host", remote_host))
        details.append(("Session", diagnostic.session))
        if diagnostic.session_id is not None:
            details.append(("Tmux ID", diagnostic.session_id))
        details.extend(
            (
                ("Project", diagnostic.project_dir or "path not recorded"),
                ("Cause", diagnostic.reason),
                ("Note", diagnostic.note),
            )
        )
        outcome = _outcome_renderable(
            f"Project state is unavailable for {diagnostic.session}",
            GLYPH_STOPPED,
            ALERT_COLOR,
            details=tuple(details),
            repair=tuple(
                (repair.label, repair.instruction, repair.command) for repair in diagnostic.repairs
            ),
        )
        rows.extend((Text(""), outcome))
    if remote_host is not None and diagnostics:
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
    values: list[RenderableType] = [
        _outcome_line_for_cli(
            "Choose a session from the discovered list", GLYPH_DONE, SUCCESS_COLOR
        )
    ]
    _append_outcome_heading(values, "Details")
    if host is not None:
        values.append(_outcome_detail_line("Host", host))
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

    table = Table.grid(expand=False, padding=(0, STATUS_USAGE_COLUMN_PADDING))
    headers = STATUS_USAGE_HEADERS
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
            "Quota used 5h/7d",
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
    cell = Text()
    reported_periods = [period for period in QUOTA_PERIODS if period in by_name]
    if reported_periods:
        for index, period in enumerate(QUOTA_PERIODS):
            if index:
                cell.append(" / ", style=SECONDARY_TEXT_COLOR)
            window = by_name.get(period)
            cell.append_text(
                Text("unknown", style=SECONDARY_TEXT_COLOR)
                if window is None
                else Gauge("", window.used_pct, quota_warn_pct, quota_alert_pct).render()
            )
        other_windows = [window for window in windows if window.name not in QUOTA_PERIODS]
    else:
        order = {name: index for index, name in enumerate(QUOTA_PROVIDER_SLOTS)}
        other_windows = sorted(
            windows, key=lambda window: (order.get(window.name, len(order)), window.name)
        )
    for window in other_windows:
        if cell:
            cell.append(" · " if reported_periods else " / ", style=SECONDARY_TEXT_COLOR)
        cell.append_text(
            Gauge(window.name, window.used_pct, quota_warn_pct, quota_alert_pct).render()
        )
    return cell


def _task_blocker_rows(
    detail: TaskDetail, escalations: list[EscalationView]
) -> list[tuple[Text, tuple[Text, ...]]]:
    task = detail.task
    rows: list[tuple[Text, tuple[Text, ...]]] = []
    rows.extend(
        (
            row_name("Escalation"),
            _escalation_attention_values(item.escalation),
        )
        for item in escalations
        if item.escalation.task_id == task.id
    )
    if task.status_reason is not None:
        rows.append((row_name("Outcome"), (Text(task.status_reason, style=SECONDARY_TEXT_COLOR),)))
    return rows


def _escalation_attention_values(escalation: Escalation) -> tuple[Text, ...]:
    values = [
        Text(escalation.id, style=BOLD_PRIMARY),
        Text(escalation_reason_label(escalation.reason), style=AMBER),
    ]
    headline = _status_escalation_headline(escalation)
    values.append(Text(headline, style=PRIMARY_TEXT_COLOR, overflow="fold"))
    return tuple(values)


def _task_progress_rows(task: Task, state: str) -> list[tuple[Text, tuple[Text, ...]]]:
    rows: list[tuple[Text, tuple[Text, ...]]] = []
    if task.step is not None:
        stage = STATUS_STEP_LABELS[Step(task.step)]
        if stage != state:
            rows.append((row_name("Recorded step"), (Text(stage, style=BOLD_STEEL),)))
    holds = sorted(
        hold
        for hold in task.holds
        if not (
            (hold == Hold.PAUSED.value and state == STATUS_PAUSED_LABEL)
            or (hold == Hold.INTERVENTION.value and state == STATUS_OPERATOR_CONTROL_LABEL)
        )
    )
    if holds:
        rows.append((row_name("Holds"), (Text(", ".join(holds), style=AMBER),)))
    return rows


def _task_identity_state(task: Task) -> tuple[str, str]:
    if task.status is Status.ESCALATED:
        return "Escalated", AMBER
    if task.status is Status.CANCELLED:
        return "Cancelled", SECONDARY_TEXT_COLOR
    return _task_state(task)


def _latest_report_rows(detail: TaskDetail) -> list[Text]:
    if not detail.reports:
        return []
    report = detail.reports[-1]
    rows = [
        Text.assemble(
            (report.turn, BOLD_PRIMARY),
            (" · ", SECONDARY_TEXT_COLOR),
            (report.kind.value, BOLD_STEEL),
            (" · ", SECONDARY_TEXT_COLOR),
            (_report_summary(report.data), PRIMARY_TEXT_COLOR),
        )
    ]
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


def _latest_event_rows(detail: TaskDetail, escalations: list[EscalationView]) -> list[Text]:
    if not detail.events:
        return []
    event = detail.events[-1]
    message = event.message
    if event.kind is EventKind.ESCALATION:
        duplicate_headline = next(
            (
                item.escalation.headline
                for item in escalations
                if item.escalation.task_id == event.task_id
                and item.escalation.headline
                and (
                    event.message == item.escalation.headline
                    or any(
                        event.message.startswith(item.escalation.headline + separator)
                        for separator in ESCALATION_EVENT_HEADLINE_SEPARATORS
                    )
                )
            ),
            None,
        )
        if duplicate_headline is not None:
            message = event.message.removeprefix(duplicate_headline)
            for separator in ESCALATION_EVENT_HEADLINE_SEPARATORS:
                message = message.removeprefix(separator)
    row = Text.assemble(
        (event.ts.isoformat(), SECONDARY_TEXT_COLOR),
        (" · ", SECONDARY_TEXT_COLOR),
        (event.kind.value, BOLD_STEEL),
    )
    if message:
        row.append(" · ", style=SECONDARY_TEXT_COLOR)
        row.append(message, style=PRIMARY_TEXT_COLOR)
    return [row]


def _branch_rows(detail: TaskDetail) -> list[Text]:
    branches = list(detail.branches)
    repo = detail.task.repo
    if repo is not None and repo.branch not in branches:
        branches.append(repo.branch)
    rows: list[Text] = []
    for branch in branches:
        row = Text(overflow="fold")
        row.append("branch ", style=SECONDARY_TEXT_COLOR)
        row.append(branch, style=PRIMARY_TEXT_COLOR)
        if repo is not None and branch == repo.branch:
            row.append(f" → {repo.base}", style=PRIMARY_TEXT_COLOR)
            rows.append(row)
            rows.append(
                Text.assemble(
                    _fact("base ref", repo.base_ref),
                    (" · ", SECONDARY_TEXT_COLOR),
                    _fact("base sha", repo.base_sha),
                )
            )
        else:
            rows.append(row)
    return rows


def _session_rows(detail: TaskDetail) -> list[tuple[Text, tuple[Text, ...]]]:
    rows: list[tuple[Text, tuple[Text, ...]]] = []
    for run in detail.sessions:
        label = run.role.value.title()
        status_style = {
            RunStatus.LIVE: SAGE,
            RunStatus.TAKEN_OVER: AMBER,
            RunStatus.CLOSED: SECONDARY_TEXT_COLOR,
        }[run.status]
        facts = [
            Text(f"{run.agent}/{run.model}", style=PRIMARY_TEXT_COLOR, overflow="fold"),
            Text(run.status.value, style=status_style),
        ]
        if run.session_id is not None:
            facts.append(_fact("session ID", run.session_id))
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


def _task_state(task: TaskSummary | Task) -> tuple[str, str]:
    if task.status is Status.DONE:
        return "Done", SAGE
    if Hold.PAUSED.value in task.holds or task.wait_reason == WaitReason.PAUSED.value:
        return STATUS_PAUSED_LABEL, AMBER
    if Hold.INTERVENTION.value in task.holds:
        return STATUS_OPERATOR_CONTROL_LABEL, AMBER
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


def _outcome_line_for_cli(summary: str, glyph: str, color: str) -> Text:
    text = Text()
    text.append(glyph, style=f"{STYLE_BOLD} {color}")
    text.append(" ")
    text.append(summary, style=PRIMARY_TEXT_COLOR)
    return text


def _outcome_renderable(
    summary: str,
    glyph: str,
    color: str,
    *,
    details: tuple[tuple[str, str | Text], ...] = (),
    repair: tuple[tuple[str, str, str | None], ...] = (),
    next_step: tuple[tuple[str, str, str | None], ...] = (),
) -> RenderableType:
    lines: list[RenderableType] = [_outcome_line_for_cli(summary, glyph, color)]
    _append_outcome_section(lines, "Details", details)
    _append_outcome_actions(lines, "Repair", repair)
    _append_outcome_actions(lines, "Next step", next_step)
    return Group(*lines)


def _append_outcome_section(
    lines: list[RenderableType], title: str, rows: tuple[tuple[str, str | Text], ...]
) -> None:
    if not rows:
        return
    _append_outcome_heading(lines, title)
    for label, value in rows:
        lines.append(_outcome_detail_line(label, value))


def _append_outcome_heading(lines: list[RenderableType], title: str) -> None:
    lines.append(Text(""))
    lines.append(Text(title, style=f"{STYLE_BOLD} {STEEL_BLUE}"))


def _outcome_detail_line(label: str, value: str | Text) -> Text:
    row = Text("  ")
    row.append(label, style=SECONDARY_TEXT_COLOR)
    row.append(": ", style=SECONDARY_TEXT_COLOR)
    if isinstance(value, Text):
        row.append_text(value)
    else:
        row.append(value, style=PRIMARY_TEXT_COLOR)
    return row


def _append_outcome_actions(
    lines: list[RenderableType],
    title: str,
    rows: tuple[tuple[str, str, str | None], ...],
) -> None:
    if not rows:
        return
    lines.append(Text(""))
    lines.append(Text(title, style=f"{STYLE_BOLD} {STEEL_BLUE}"))
    for label, instruction, command in rows:
        row = Text("  ")
        row.append(label, style=SECONDARY_TEXT_COLOR)
        row.append(": ", style=SECONDARY_TEXT_COLOR)
        row.append(instruction, style=PRIMARY_TEXT_COLOR)
        if command is not None:
            row.append(" · ", style=SECONDARY_TEXT_COLOR)
            row.append(command, style=BOLD_STEEL)
        lines.append(row)


def _repository_root_error_renderable(
    error: ProjectNotRepositoryRootError,
) -> RenderableType:
    project_path = shlex.quote(str(error.project_root))
    if error.repository_root is None:
        return _outcome_renderable(
            "Project directory is not inside a Git repository",
            GLYPH_STOPPED,
            ALERT_COLOR,
            details=(
                ("Project", str(error.project_root)),
                ("Cause", "No Git worktree contains the selected directory."),
            ),
            repair=(
                (
                    "Repository",
                    "Initialize Git in this directory.",
                    f"git -C {project_path} init",
                ),
                (
                    "First commit",
                    "Create an empty initial commit.",
                    f"git -C {project_path} commit --allow-empty -m 'Initial commit'",
                ),
            ),
        )
    repository_path = shlex.quote(str(error.repository_root))
    return _outcome_renderable(
        "Selected project directory is below the Git repository root",
        GLYPH_STOPPED,
        ALERT_COLOR,
        details=(
            ("Project", str(error.project_root)),
            ("Repository root", str(error.repository_root)),
        ),
        repair=(
            (
                "Initialize",
                "Run initialization from the repository root.",
                f"shikiru --project {repository_path} init --yes",
            ),
        ),
    )


def _ack_task_id(ack: Ack) -> str | None:
    if ack.submitted_task_id is not None:
        return ack.submitted_task_id
    result = ack.result
    task_id = result.get("task_id") if result is not None else None
    return task_id if isinstance(task_id, str) and task_id else None


def _ack_details(ack: Ack, task_id: str | None) -> tuple[tuple[str, str], ...]:
    rows = [("Command", f"C-{ack.id}")]
    if task_id is not None:
        rows.append(("Task ID", task_id))
    message = ack.message
    if message is None and ack.result is not None:
        error = ack.result.get("error")
        message = str(error) if error is not None else None
    if message is not None:
        label = "Cause" if ack.status is AckStatus.REJECTED else "State"
        rows.append((label, message))
    return tuple(rows)


def _stop_outcome(ack: Ack) -> str | None:
    if ack.status is not AckStatus.DONE or ack.result is None:
        return None
    if ack.result.get(STOPPED_RESULT_FIELD) is False:
        return "Engine already stopped"
    if ack.result.get(STOPPING_RESULT_FIELD) is True:
        return "Engine stopped"
    return None
