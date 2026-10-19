"""Rich renderables shared by operator CLI output."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal

from rich.cells import cell_len
from rich.console import Group
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
from shikiru.domain.steps import ACTING_ROLE, Step
from shikiru.domain.tasks import Status
from shikiru.errors import NotInitialisedError, ShikiruError
from shikiru.operator.commands import AckStatus
from shikiru.ui.age import format_age
from shikiru.ui.compact import compact_count
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
    Section,
    WrappedGroups,
    grey,
    labelled_rows,
    number,
    row_name,
)
from shikiru.ui.size import format_size
from shikiru.ui.status import status_visual
from shikiru.ui.tokens import (
    ALERT_COLOR,
    AMBER,
    ENGINE_COLOR,
    PRIMARY_TEXT_COLOR,
    SAGE,
    SECONDARY_TEXT_COLOR,
    STYLE_BOLD,
    SUCCESS_COLOR,
)

if TYPE_CHECKING:
    from rich.console import Console, RenderableType

    from shikiru.operator.commands import Ack
    from shikiru.operator.doctor import DoctorReport
    from shikiru.operator.lifecycle import StartResult
    from shikiru.operator.queries import (
        EscalationView,
        StatusView,
        TaskDetail,
        TaskSummary,
        UsageView,
    )
    from shikiru.operator.sessions import SessionRow

STATUS_ROW_STACKED_BELOW_WIDTH = 60
DATETIME_TIMESPEC_SECONDS = "seconds"
STATUS_COST_FORMAT = ".2f"
DOCTOR_OK_LABEL = "ok"
DOCTOR_FAIL_LABEL = "fail"
DOCTOR_OUTCOME_LABEL_WIDTH = len(f"{GLYPH_STOPPED} {DOCTOR_FAIL_LABEL}")
STOPPED_RESULT_FIELD = "stopped"
STOPPING_RESULT_FIELD = "stopping"


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


def status_renderable(view: StatusView, *, width: int) -> RenderableType:
    """Render project or task status as open, width-wrapping rows."""
    if view.task is not None:
        return _task_status_renderable(view.task, view.escalations, width=width)

    return _project_status_renderable(view, width=width)


def _project_status_renderable(view: StatusView, *, width: int) -> RenderableType:
    engine_style = f"{STYLE_BOLD} {ENGINE_COLOR if view.engine_up else ALERT_COLOR}"
    summary = [
        Text(view.project, style=BOLD_PRIMARY),
        Text.assemble(
            (f"{GLYPH_ENGINE} engine ", f"{STYLE_BOLD} {ENGINE_COLOR}"),
            ("up" if view.engine_up else "down", engine_style),
        ),
        _fact("slots", f"{view.slots.active}/{view.slots.maximum}", BOLD_PRIMARY),
        _fact("queued", str(len(view.queued_tasks)), BOLD_PRIMARY),
        _fact("escalations", str(len(view.escalations)), BOLD_PRIMARY),
    ]
    if view.oldest_escalation_age_seconds is not None:
        summary.append(
            _fact("oldest", format_age(view.oldest_escalation_age_seconds), BOLD_PRIMARY)
        )
    summary.append(_fact("run dirs", str(view.run_directories.directories), BOLD_PRIMARY))
    summary.append(_fact("size", format_size(view.run_directories.size_bytes)))
    return Group(WrappedGroups(tuple(summary)), *_project_status_sections(view, width=width))


def _project_status_sections(view: StatusView, *, width: int) -> list[RenderableType]:
    blocks: list[RenderableType] = []
    if view.engine_output is not None:
        path_row = [
            (
                row_name("last engine output"),
                (Text(view.engine_output, style=SECONDARY_TEXT_COLOR, overflow="fold"),),
            )
        ]
        blocks.append(_status_rows(path_row, width=width))
    if view.active_tasks:
        blocks.append(_task_section("Tasks", view.active_tasks, width=width))
    if view.queued_tasks:
        blocks.append(_task_section("Queue", view.queued_tasks, width=width))
    if view.faults:
        blocks.append(_fault_section(view, width=width))
    if view.escalations:
        blocks.append(_escalation_section(view, width=width))
    if view.usage:
        blocks.append(_status_section("Usage", _usage_rows(view.usage), width=width))
    quota_rows = _quota_rows(view)
    if quota_rows:
        blocks.append(_status_section("Quota", quota_rows, width=width))
    return blocks


def _task_status_renderable(
    detail: TaskDetail, escalations: list[EscalationView], *, width: int
) -> RenderableType:
    task = detail.task
    identity = Text.assemble(
        (task.id, BOLD_PRIMARY), (" · ", SECONDARY_TEXT_COLOR), (task.title, PRIMARY_TEXT_COLOR)
    )
    visual = status_visual(task.status)
    state = Text(f"{visual.glyph} {task.status.value}", style=visual.style)
    groups = [state]
    if task.step is not None:
        role = ACTING_ROLE[task.step]
        groups.extend(
            (
                _fact("step", task.step.value, BOLD_STEEL),
                _fact("role", role.value.title(), BOLD_STEEL),
            )
        )
    body: list[RenderableType] = [
        _status_text_rows([identity], width=width),
        WrappedGroups(tuple(groups)),
    ]

    blockers = _task_blocker_rows(detail, escalations)
    if blockers:
        body.append(_status_rows(blockers, width=width))
    report_rows = _report_rows(detail)
    if report_rows:
        body.append(_status_text_section("Reports", report_rows, width=width))
    timeline_rows = _timeline_rows(detail)
    if timeline_rows:
        body.append(_status_text_section("Timeline", timeline_rows, width=width))
    branch_rows = _branch_rows(detail)
    if branch_rows:
        body.append(_status_section("Branches", branch_rows, width=width))
    session_rows = _session_rows(detail)
    if session_rows:
        body.append(_status_section("Sessions", session_rows, width=width))
    if detail.evidence_errors:
        body.append(
            _status_text_section(
                "Evidence",
                [Text(error, style=AMBER) for error in detail.evidence_errors],
                width=width,
            )
        )
    return Group(*body)


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


def _task_section(title: str, tasks: list[TaskSummary], *, width: int) -> Section:
    rows = [(row_name(task.id), _task_groups(task)) for task in tasks]
    return _status_section(title, rows, width=width)


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


def _task_groups(task: TaskSummary) -> tuple[Text, ...]:
    visual = status_visual(task.status)
    groups = [Text(f"{visual.glyph} {task.status.value}", style=visual.style)]
    groups.append(Text(task.title, style=PRIMARY_TEXT_COLOR, overflow="fold"))
    if task.step is not None:
        step = Step(task.step)
        role = ACTING_ROLE[step]
        groups.extend(
            (_fact("step", step.value, BOLD_STEEL), _fact("role", role.value.title(), BOLD_STEEL))
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
    rows = [
        (
            row_name(item.escalation.id),
            (
                _fact("task", item.escalation.task_id, BOLD_PRIMARY),
                Text(escalation_reason_label(item.escalation.reason), style=AMBER),
                Text(item.escalation.headline, style=PRIMARY_TEXT_COLOR, overflow="fold"),
            ),
        )
        for item in view.escalations
    ]
    return _status_section("Escalations", rows, width=width)


def _usage_rows(usages: list[UsageView]) -> list[tuple[Text, tuple[Text, ...]]]:
    return [(row_name(usage.agent), _usage_groups(usage)) for usage in usages]


def _usage_groups(usage: UsageView) -> tuple[Text, ...]:
    run_noun = "run" if usage.run_count == 1 else "runs"
    groups = [
        Text.assemble((str(usage.run_count), BOLD_PRIMARY), (f" {run_noun}", SECONDARY_TEXT_COLOR))
    ]
    known_token_runs = usage.usage_reported_runs + usage.legacy_token_runs
    unknown_token_runs = usage.run_count - known_token_runs
    if known_token_runs:
        groups.extend(
            (
                _fact("in", compact_count(usage.input_tokens), BOLD_STEEL),
                _fact("out", compact_count(usage.output_tokens), BOLD_STEEL),
            )
        )
        if usage.usage_reported_runs:
            groups.append(_fact("reported runs", str(usage.usage_reported_runs)))
        if usage.legacy_token_runs:
            groups.append(_fact("legacy token runs", str(usage.legacy_token_runs), AMBER))
    if unknown_token_runs:
        groups.append(_fact("runs without token data", str(unknown_token_runs)))
    if usage.billed_cost_runs and usage.billed_cost_usd is not None:
        groups.append(_cost_basis_group(usage.billed_cost_usd, "billed", SAGE))
    if usage.api_equivalent_cost_runs and usage.api_equivalent_cost_usd is not None:
        groups.append(
            _cost_basis_group(usage.api_equivalent_cost_usd, "API-equivalent estimate", AMBER)
        )
    if usage.legacy_billed_cost_runs:
        groups.append(_fact("legacy billed runs", str(usage.legacy_billed_cost_runs), AMBER))
    if usage.legacy_api_equivalent_cost_runs:
        groups.append(
            _fact("legacy estimate runs", str(usage.legacy_api_equivalent_cost_runs), AMBER)
        )
    if usage.unknown_cost_runs:
        groups.append(_fact("runs without cost", str(usage.unknown_cost_runs)))
    return tuple(groups)


def _cost_basis_group(amount: float, basis: str, style: str) -> Text:
    return Text.assemble(
        (f"${amount:{STATUS_COST_FORMAT}}", style),
        (f" {basis}", SECONDARY_TEXT_COLOR),
    )


def _quota_rows(view: StatusView) -> list[tuple[Text, tuple[Text, ...]]]:
    rows: list[tuple[Text, tuple[Text, ...]]] = []
    for agent, windows in sorted(view.quotas.items()):
        for window in windows:
            reset = (
                window.resets_at.isoformat(timespec=DATETIME_TIMESPEC_SECONDS)
                if window.resets_at is not None
                else "unknown"
            )
            rows.append(
                (
                    row_name(f"{agent} · {window.name}"),
                    (
                        _fact("used", f"{window.used_pct:g}%", BOLD_PRIMARY),
                        _fact("reset", reset),
                        _fact(
                            "observed",
                            window.observed_at.isoformat(timespec=DATETIME_TIMESPEC_SECONDS),
                        ),
                    ),
                )
            )
    return rows


def _task_blocker_rows(
    detail: TaskDetail, escalations: list[EscalationView]
) -> list[tuple[Text, tuple[Text, ...]]]:
    task = detail.task
    rows: list[tuple[Text, tuple[Text, ...]]] = []
    if task.holds:
        holds = ", ".join(sorted(hold.value for hold in task.holds))
        rows.append((row_name("Holds"), (Text(holds, style=AMBER),)))
    if task.wait_reason is not None:
        rows.append((row_name("Waiting"), (Text(task.wait_reason, style=AMBER, overflow="fold"),)))
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
