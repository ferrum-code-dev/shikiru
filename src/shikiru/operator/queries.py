"""Read-only operator views over the state reader (06 §1)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from shikiru.agents.contracts import COST_BASIS_UNKNOWN, CostBasis, QuotaWindow, Usage
from shikiru.domain.events import Event, EventKind
from shikiru.domain.faults import FaultStreak, open_fault_streaks
from shikiru.domain.reports import FACTS_FILENAME, Report, ReportKind
from shikiru.domain.runs import Run, RunStatus
from shikiru.domain.scheduling import dispatch_sort_key, occupied_slot_ids
from shikiru.domain.tasks import ACTIVE_STATUSES, Role, Status, Task
from shikiru.errors import ShikiruError
from shikiru.paths import engine_output_path, run_dir, runs_root, task_root, turns_dir
from shikiru.store.escalations import Escalation
from shikiru.telemetry.disk import directory_usage
from shikiru.view_log import ordered_turn_dirs, read_tail_lines, view_log_path
from shikiru.workspace.reports import (
    InvalidTurnReportError,
    load_engine_verdict,
    load_turn_report,
)

if TYPE_CHECKING:
    from shikiru.clock import Clock
    from shikiru.store.reader import StateReader

PEEK_TAIL_LINES = 60
TASK_DETAIL_EVENT_LIMIT = 40
TASK_DETAIL_SIGNAL_LIMIT = 20
ENGINE_REPORT_SOURCE = "engine"
AGENT_REPORT_SOURCE = "agent"
ENGINE_SIGNAL_ERROR = "could not read recorded agent signals for {role}: {error}"
TASK_REPORT_ERROR = "could not read report evidence: {error}"
TASK_FACTS_ERROR = "could not read recorded git facts: {error}"
TASK_ACTION_MISSING = "task no longer exists"
TASK_ACTION_TERMINAL = "task is terminal"
TASK_ACTION_ROLE_MISSING = "role pane is unavailable"
TASK_ACTION_TAKEN_OVER = "this role is already taken over"
TASK_ACTION_SESSION_MISSING = "this role has no live session to take over"
TASK_ACTION_NOT_TAKEN_OVER = "this role is not taken over"
TASK_ACTION_OTHER_ROLE_TAKEN_OVER = "hand back all taken-over roles before resuming"
TASK_ACTION_NO_HOLD = "task has no pause or ownership hold"
TASK_ACTION_NOT_QUEUED = "task can only be prioritized while queued"
TASK_PANE_CONTEXT_STATUSES = (*ACTIVE_STATUSES, Status.QUEUED)


class OperatorTaskAction(StrEnum):
    """A task action whose availability is projected from current operator-visible state."""

    PRIORITIZE = "prioritize"
    PAUSE = "pause"
    PAUSE_NOW = "pause-now"
    RESUME = "resume"
    TAKE_OVER = "take-over"
    HAND_BACK = "hand-back"
    CANCEL = "cancel"
    RETRY = "retry"
    RUNG = "rung"
    MESSAGE = "message"
    PEEK = "peek"


@dataclass(frozen=True)
class TaskActionAvailability:
    """The current availability and any disabled reason for one task action."""

    action: OperatorTaskAction
    disabled_reason: str | None

    @property
    def enabled(self) -> bool:
        """Whether the current task state permits presenting this action as available."""
        return self.disabled_reason is None


class OperatorView(BaseModel):
    """Base model for JSON-safe operator views."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class TaskSummary(OperatorView):
    """The scheduling fields shown for one task in a queue or status view."""

    id: str
    title: str
    status: Status
    step: str | None
    priority: int
    wait_reason: str | None
    holds: frozenset[str]
    slot: int | None
    parent_id: str | None
    summary: str | None

    @classmethod
    def from_task(cls, task: Task) -> TaskSummary:
        return cls(
            id=task.id,
            title=task.title,
            status=task.status,
            step=task.step.value if task.step is not None else None,
            priority=task.priority,
            wait_reason=task.wait_reason,
            holds=frozenset(hold.value for hold in task.holds),
            slot=task.slot,
            parent_id=task.parent_id,
            summary=task.summary,
        )


class ReportRow(OperatorView):
    """A turn's validated report in a task detail view."""

    turn: str
    kind: ReportKind
    data: Report
    source: str = AGENT_REPORT_SOURCE


class TaskDetail(OperatorView):
    """A task with its runs and event timeline."""

    task: Task
    events: list[Event] = Field(default_factory=list)
    reports: list[ReportRow] = Field(default_factory=list)
    signals: dict[str, list[str]] = Field(default_factory=dict)
    recorded_facts: str | None = None
    facts_turn: str | None = None
    evidence_errors: list[str] = Field(default_factory=list)
    branches: list[str] = Field(default_factory=list)
    sessions: list[Run] = Field(default_factory=list)


class EscalationView(OperatorView):
    """An escalation row and its packet text."""

    escalation: Escalation
    packet: str


class PeekPane(OperatorView):
    """One role's last message, usage and most recent view log lines."""

    agent: str
    last_message: str | None
    usage: Usage
    view: list[str]


class PeekView(OperatorView):
    """The recorded state of one or more task roles, keyed by role."""

    task_id: str
    panes: dict[str, PeekPane]


class SlotView(OperatorView):
    """The engine's configured and currently occupied task slots."""

    active: int
    maximum: int


class UsageView(OperatorView):
    """Aggregated usage for one agent across recorded runs."""

    agent: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: float | None
    cost_basis: CostBasis | Literal["mixed"]


class RunDirectoryUsage(OperatorView):
    """How many task run directories are kept and the bytes they take."""

    directories: int
    size_bytes: int


class StatusView(OperatorView):
    """The status screen's engine, queue, task, escalation and usage views."""

    project: str
    engine_up: bool
    slots: SlotView
    active_tasks: list[TaskSummary]
    queued_tasks: list[TaskSummary]
    escalations: list[EscalationView]
    usage: list[UsageView]
    quotas: dict[str, list[QuotaWindow]]
    faults: list[FaultStreak]
    oldest_escalation_age_seconds: float | None
    run_directories: RunDirectoryUsage
    engine_output: str | None = None
    task: TaskDetail | None = None


StatusView.model_rebuild(_types_namespace={"datetime": datetime})


EngineState = Callable[[], bool]


class OperatorQueryError(ShikiruError):
    """A requested operator view could not be read at its boundary."""


class OperatorQueries:
    """Provide read-only status, task, escalation and pane views to operators."""

    def __init__(
        self,
        reader: StateReader,
        *,
        project: str,
        project_root: Path,
        engine_up: EngineState,
        max_parallel: int,
        clock: Clock,
    ) -> None:
        """Use `reader` and `engine_up` for the operator's read boundaries."""
        self._reader = reader
        self._project = project
        self._project_root = project_root
        self._engine_up = engine_up
        self._max_parallel = max_parallel
        self._clock = clock

    def status(self, task_id: str | None = None) -> StatusView:
        """Return the global status view, optionally including one task's detail."""
        tasks = self._reader.list_tasks()
        queued_tasks = sorted(
            (task for task in tasks if task.status is Status.QUEUED), key=dispatch_sort_key
        )
        summaries = [TaskSummary.from_task(task) for task in tasks]
        active = [summary for summary in summaries if summary.status in ACTIVE_STATUSES]
        queued = [TaskSummary.from_task(task) for task in queued_tasks]
        open_escalations = self.list_escalations()
        usage, quotas = self.usage_and_quotas()
        task_detail = self.get_task(task_id) if task_id is not None else None
        return StatusView(
            project=self._project,
            engine_up=self._engine_up(),
            slots=SlotView(active=len(occupied_slot_ids(tasks)), maximum=self._max_parallel),
            active_tasks=active,
            queued_tasks=queued,
            escalations=open_escalations,
            usage=usage,
            quotas=quotas,
            faults=self.open_faults(),
            oldest_escalation_age_seconds=self.oldest_escalation_age_seconds(),
            run_directories=self.run_directory_usage(),
            engine_output=self._engine_output(),
            task=task_detail,
        )

    def _engine_output(self) -> str | None:
        path = engine_output_path(self._project_root)
        if self._engine_up() or not path.is_file() or path.stat().st_size == 0:
            return None
        return str(path)

    def run_directory_usage(self) -> RunDirectoryUsage:
        """Return the number and total size of the task run directories under the project."""
        usage = directory_usage(runs_root(self._project_root))
        return RunDirectoryUsage(directories=usage.directories, size_bytes=usage.size_bytes)

    def open_faults(self) -> list[FaultStreak]:
        """Return the engine fault streaks recorded and not yet recovered."""
        return open_fault_streaks(
            self._reader.events_since(0, kinds=[EventKind.ENGINE_FAULT, EventKind.ENGINE_RECOVERED])
        )

    def oldest_escalation_age_seconds(self) -> float | None:
        """Return the age of the oldest unresolved escalation, or `None` when none is open."""
        created = [item.created_at for item in self._reader.list_open_escalations()]
        return (self._clock.now() - min(created)).total_seconds() if created else None

    def usage_and_quotas(self) -> tuple[list[UsageView], dict[str, list[QuotaWindow]]]:
        """Return shared usage totals and provider-reported quota windows."""
        runs = self._reader.list_all_runs()
        agents = sorted({run.agent for run in runs} | set(self._reader.list_quota_agents()))
        return _aggregate_usage(runs), self._reader.list_quotas(agents)

    def task_usage(self, task_id: str) -> list[UsageView]:
        """Return agent-reported usage totals for one task's sessions."""
        return _aggregate_usage(self._reader.list_runs(task_id))

    def list_tasks(self, *, statuses: Sequence[Status] | None = None) -> list[TaskSummary]:
        """Return task summaries ordered by their allocated task sequence."""
        return [TaskSummary.from_task(task) for task in self._reader.list_tasks(statuses=statuses)]

    def get_task(self, task_id: str) -> TaskDetail | None:
        """Return one task, its runs and its event timeline, or `None` when absent."""
        task = self._reader.get_task(task_id)
        if task is None:
            return None
        runs = self._reader.list_runs(task_id)
        return TaskDetail(
            task=task,
            events=self._reader.events_since(0, task_id=task_id),
            reports=self._reports(task_id),
            branches=[task.repo.branch] if task.repo is not None else [],
            sessions=runs,
        )

    async def get_task_detail(self, task_id: str) -> TaskDetail | None:
        """Read bounded task events and load its file evidence away from the UI loop."""
        task = self._reader.get_task(task_id)
        if task is None:
            return None
        runs = self._reader.list_runs(task_id)
        events = self._reader.latest_events(TASK_DETAIL_EVENT_LIMIT, task_id=task_id)
        files = await asyncio.to_thread(
            self._task_files,
            task_id,
            tuple(dict.fromkeys(run.role.value for run in runs)),
        )
        reports, signals, recorded_facts, facts_turn, evidence_errors = files
        return TaskDetail(
            task=task,
            events=events,
            reports=reports,
            signals=signals,
            recorded_facts=recorded_facts,
            facts_turn=facts_turn,
            evidence_errors=evidence_errors,
            branches=[task.repo.branch] if task.repo is not None else [],
            sessions=runs,
        )

    def get_task_state(self, task_id: str) -> Task | None:
        """Return current task state without loading report or signal files."""
        return self._reader.get_task(task_id)

    def task_action_availability(
        self, task_id: str, role: Role
    ) -> tuple[TaskActionAvailability, ...]:
        """Project task command availability and disabled reasons from current state."""
        task = self._reader.get_task(task_id)
        if task is None:
            return tuple(
                TaskActionAvailability(action, TASK_ACTION_MISSING) for action in OperatorTaskAction
            )
        runs = self._reader.list_runs(task_id)
        role_is_taken_over = any(
            run.role is role and run.status is RunStatus.TAKEN_OVER for run in runs
        )
        any_role_taken_over = any(run.status is RunStatus.TAKEN_OVER for run in runs)
        role_has_live_session = any(
            run.role is role and run.status is RunStatus.LIVE and run.session_id is not None
            for run in runs
        )
        terminal = task.status in (Status.DONE, Status.CANCELLED)
        pane_missing = role not in task.panes
        reasons = {
            OperatorTaskAction.PRIORITIZE: (
                None if task.status is Status.QUEUED else TASK_ACTION_NOT_QUEUED
            ),
            OperatorTaskAction.PAUSE: TASK_ACTION_TERMINAL if terminal else None,
            OperatorTaskAction.PAUSE_NOW: TASK_ACTION_TERMINAL if terminal else None,
            OperatorTaskAction.RESUME: (
                TASK_ACTION_TERMINAL
                if terminal
                else TASK_ACTION_OTHER_ROLE_TAKEN_OVER
                if any_role_taken_over
                else TASK_ACTION_NO_HOLD
                if not task.holds
                else None
            ),
            OperatorTaskAction.TAKE_OVER: (
                TASK_ACTION_TAKEN_OVER
                if role_is_taken_over
                else TASK_ACTION_TERMINAL
                if terminal
                else TASK_ACTION_ROLE_MISSING
                if pane_missing
                else TASK_ACTION_SESSION_MISSING
                if not role_has_live_session
                else None
            ),
            OperatorTaskAction.HAND_BACK: (
                TASK_ACTION_NOT_TAKEN_OVER
                if not role_is_taken_over
                else TASK_ACTION_ROLE_MISSING
                if pane_missing
                else None
            ),
            OperatorTaskAction.CANCEL: TASK_ACTION_TERMINAL if terminal else None,
            OperatorTaskAction.RETRY: TASK_ACTION_TERMINAL if terminal else None,
            OperatorTaskAction.RUNG: TASK_ACTION_TERMINAL if terminal else None,
            OperatorTaskAction.MESSAGE: TASK_ACTION_TERMINAL if terminal else None,
            OperatorTaskAction.PEEK: None,
        }
        return tuple(
            TaskActionAvailability(action, reasons[action]) for action in OperatorTaskAction
        )

    def task_role_for_pane(self, pane_id: str) -> tuple[str, Role] | None:
        """Return the recorded task and role whose pane matches an invoking tmux pane."""
        for task in self._reader.list_tasks(statuses=TASK_PANE_CONTEXT_STATUSES):
            for role, recorded_pane in task.panes.items():
                if recorded_pane == pane_id:
                    return task.id, role
        return None

    def task_worktree_path(self, task_id: str) -> Path:
        """Return the recorded layout location used for this task's worktree."""
        return task_root(self._project_root, task_id)

    def list_escalations(self) -> list[EscalationView]:
        """Return all currently open escalation packets."""
        return [
            EscalationView(escalation=escalation, packet=self._packet_text(escalation))
            for escalation in self._reader.list_open_escalations()
        ]

    def get_escalation(self, escalation_id: str) -> EscalationView | None:
        """Return one escalation and packet text, or `None` when absent."""
        escalation = self._reader.get_escalation(escalation_id)
        if escalation is None:
            return None
        return EscalationView(escalation=escalation, packet=self._packet_text(escalation))

    def peek_session(self, task_id: str, role: Role | None = None) -> PeekView | None:
        """Return the last message, usage and recent view lines of one or every task role."""
        task = self._reader.get_task(task_id)
        if task is None:
            return None
        latest = {run.role: run for run in reversed(self._reader.list_runs(task_id))}
        roles = [role] if role is not None else list(latest)
        if not roles:
            message = f"{task_id} has no agent sessions"
            raise OperatorQueryError(message)
        panes: dict[str, PeekPane] = {}
        for peek_role in roles:
            run = latest.get(peek_role)
            if run is None:
                message = f"{task_id} has no {peek_role.value} session"
                raise OperatorQueryError(message)
            panes[peek_role.value] = PeekPane(
                agent=run.agent,
                last_message=run.last_message,
                usage=run.usage,
                view=_view_tail(view_log_path(run_dir(self._project_root, task_id), run.role)),
            )
        return PeekView(task_id=task_id, panes=panes)

    def _packet_text(self, escalation: Escalation) -> str:
        packet_path = Path(escalation.packet_path)
        if not packet_path.is_absolute():
            packet_path = self._project_root / packet_path
        if not packet_path.is_file():
            message = f"escalation packet is missing: {packet_path}"
            raise OperatorQueryError(message)
        try:
            return packet_path.read_text()
        except OSError as error:
            message = f"cannot read escalation packet {packet_path}: {error}"
            raise OperatorQueryError(message) from error

    def _reports(self, task_id: str) -> list[ReportRow]:
        task_turns_dir = turns_dir(self._project_root, task_id)
        if not task_turns_dir.is_dir():
            return []
        reports: list[ReportRow] = []
        for turn_dir in ordered_turn_dirs(task_turns_dir):
            loaded = load_turn_report(turn_dir)
            if loaded is not None:
                reports.append(
                    ReportRow(
                        turn=turn_dir.name,
                        kind=loaded.kind,
                        data=loaded.report,
                    )
                )
        return reports

    def _task_files(
        self,
        task_id: str,
        roles: tuple[str, ...],
    ) -> tuple[list[ReportRow], dict[str, list[str]], str | None, str | None, list[str]]:
        turn_dirs, errors = self._task_turn_dirs(task_id)
        signals, signal_errors = self._task_signals(task_id, roles)
        if turn_dirs is None:
            return [], signals, None, None, [*errors, *signal_errors]
        reports, report_errors = self._task_reports(turn_dirs)
        recorded_facts, facts_turn, fact_errors = _task_facts(turn_dirs)
        return (
            reports,
            signals,
            recorded_facts,
            facts_turn,
            [*errors, *report_errors, *fact_errors, *signal_errors],
        )

    def _task_turn_dirs(self, task_id: str) -> tuple[list[Path] | None, list[str]]:
        task_turns_dir = turns_dir(self._project_root, task_id)
        try:
            task_turns_dir.stat()
        except FileNotFoundError:
            return None, []
        except OSError as error:
            return None, [TASK_REPORT_ERROR.format(error=error)]
        if not task_turns_dir.is_dir():
            return None, [TASK_REPORT_ERROR.format(error=f"not a directory: {task_turns_dir}")]
        try:
            return ordered_turn_dirs(task_turns_dir), []
        except (OSError, ValueError) as error:
            return None, [TASK_REPORT_ERROR.format(error=error)]

    def _task_reports(self, turn_dirs: Sequence[Path]) -> tuple[list[ReportRow], list[str]]:
        latest_agent: ReportRow | None = None
        latest_verdict: ReportRow | None = None
        latest_triage: ReportRow | None = None
        errors: list[str] = []
        for turn_dir in reversed(turn_dirs):
            turn_reports, turn_errors = _task_turn_reports(turn_dir)
            errors.extend(turn_errors)
            for report in turn_reports:
                if report.source == AGENT_REPORT_SOURCE and latest_agent is None:
                    latest_agent = report
                if report.kind is ReportKind.TRIAGE and latest_triage is None:
                    latest_triage = report
            if latest_verdict is None:
                latest_verdict = next(
                    (
                        report
                        for report in turn_reports
                        if report.source == ENGINE_REPORT_SOURCE
                        and report.kind is ReportKind.VERDICT
                    ),
                    None,
                ) or next(
                    (
                        report
                        for report in turn_reports
                        if report.source == AGENT_REPORT_SOURCE
                        and report.kind is ReportKind.VERDICT
                    ),
                    None,
                )
            if (
                latest_agent is not None
                and latest_verdict is not None
                and latest_triage is not None
            ):
                break
        turn_order = {turn_dir.name: index for index, turn_dir in enumerate(turn_dirs)}
        reports = {
            (report.turn, report.kind, report.source): report
            for report in (latest_agent, latest_verdict, latest_triage)
            if report is not None
        }
        return sorted(reports.values(), key=lambda report: turn_order[report.turn]), errors

    def _task_signals(
        self,
        task_id: str,
        roles: tuple[str, ...],
    ) -> tuple[dict[str, list[str]], list[str]]:
        signals: dict[str, list[str]] = {}
        errors: list[str] = []
        for role in roles:
            signal_path = view_log_path(run_dir(self._project_root, task_id), role)
            try:
                lines = _signal_tail(signal_path)
            except (OSError, UnicodeError, ValueError, TypeError) as error:
                errors.append(ENGINE_SIGNAL_ERROR.format(role=role, error=error))
            else:
                if lines:
                    signals[role] = lines
        return signals, errors


def _task_turn_reports(turn_dir: Path) -> tuple[list[ReportRow], list[str]]:
    reports: list[ReportRow] = []
    errors: list[str] = []
    try:
        loaded = load_turn_report(turn_dir)
    except (InvalidTurnReportError, OSError, ValueError) as error:
        errors.append(str(error))
    else:
        if loaded is not None:
            reports.append(
                ReportRow(
                    turn=turn_dir.name,
                    kind=loaded.kind,
                    data=loaded.report,
                    source=AGENT_REPORT_SOURCE,
                )
            )
    try:
        verdict = load_engine_verdict(turn_dir)
    except (InvalidTurnReportError, OSError, ValueError) as error:
        errors.append(str(error))
    else:
        if verdict is not None:
            reports.append(
                ReportRow(
                    turn=turn_dir.name,
                    kind=ReportKind.VERDICT,
                    data=verdict,
                    source=ENGINE_REPORT_SOURCE,
                )
            )
    return reports, errors


def _task_facts(turn_dirs: Sequence[Path]) -> tuple[str | None, str | None, list[str]]:
    for turn_dir in reversed(turn_dirs):
        facts_path = turn_dir / FACTS_FILENAME
        try:
            facts = facts_path.read_text()
        except FileNotFoundError:
            continue
        except (OSError, UnicodeError) as error:
            return None, None, [TASK_FACTS_ERROR.format(error=error)]
        return facts, turn_dir.name, []
    return None, None, []


def _view_tail(path: Path) -> list[str]:
    if not path.is_file():
        return []
    try:
        return read_tail_lines(path, PEEK_TAIL_LINES)
    except OSError as error:
        message = f"cannot read view log {path}: {error}"
        raise OperatorQueryError(message) from error


def _signal_tail(path: Path) -> list[str]:
    try:
        lines = read_tail_lines(path, TASK_DETAIL_SIGNAL_LIMIT)
    except FileNotFoundError:
        return []
    signals: list[str] = []
    for line in lines:
        row = json.loads(line)
        if not isinstance(row, dict):
            message = "recorded signal row is not an object"
            raise TypeError(message)
        signals.append(json.dumps(row, ensure_ascii=False, sort_keys=True))
    return signals


def _aggregate_usage(runs: Sequence[Run]) -> list[UsageView]:
    by_agent: dict[str, list[Run]] = {}
    for run in runs:
        by_agent.setdefault(run.agent, []).append(run)
    views: list[UsageView] = []
    for agent in sorted(by_agent):
        agent_runs = by_agent[agent]
        known_costs = [run.usage.cost_usd for run in agent_runs]
        cost_bases: set[CostBasis] = {
            run.usage.cost_basis for run in agent_runs if run.usage.cost_usd is not None
        }
        cost_basis: CostBasis | Literal["mixed"]
        if any(
            cost is None or run.usage.cost_basis == COST_BASIS_UNKNOWN
            for cost, run in zip(known_costs, agent_runs, strict=True)
        ):
            cost_usd = None
            cost_basis = "unknown"
        elif len(cost_bases) > 1:
            cost_usd = None
            cost_basis = "mixed"
        else:
            cost_usd = sum((cost for cost in known_costs if cost is not None), 0.0)
            cost_basis = next(iter(cost_bases)) if cost_bases else "unknown"
        views.append(
            UsageView(
                agent=agent,
                input_tokens=sum(run.usage.input_tokens for run in agent_runs),
                output_tokens=sum(run.usage.output_tokens for run in agent_runs),
                cache_read_tokens=sum(run.usage.cache_read_tokens for run in agent_runs),
                cache_write_tokens=sum(run.usage.cache_write_tokens for run in agent_runs),
                cost_usd=cost_usd,
                cost_basis=cost_basis,
            )
        )
    return views
