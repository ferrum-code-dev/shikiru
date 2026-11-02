"""Read-only operator views over the state reader."""

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

from shikiru.agents.contracts import (
    COST_BASIS_UNKNOWN,
    CostBasis,
    QuotaWindow,
    Usage,
)
from shikiru.config.settings import MAX_TIMELINE_PAGE_SIZE, MAX_TIMELINE_SIGNAL_SCAN_ROWS
from shikiru.domain.commands import (
    Command,
    CommandStatus,
    HandBack,
    ResolutionKind,
    Resolve,
    Say,
    SetRung,
    Stop,
    Submit,
    TakeOver,
)
from shikiru.domain.engine_uptime import project_engine_uptime
from shikiru.domain.escalation import EscalationStatus
from shikiru.domain.escalation_evidence import parse_escalation_packet
from shikiru.domain.events import Event, EventKind
from shikiru.domain.faults import FaultStreak, open_fault_streaks
from shikiru.domain.reports import (
    FACTS_FILENAME,
    HELPER_REPORT_FILENAME,
    REPORT_FILENAME,
    REPORT_KIND_FILENAME,
    Option,
    Report,
    ReportKind,
)
from shikiru.domain.runs import Run, RunStatus, has_positive_token_evidence
from shikiru.domain.scheduling import dispatch_sort_key, occupied_slot_ids
from shikiru.domain.status_tasks import select_human_status_tasks
from shikiru.domain.steps import task_acting_role
from shikiru.domain.tasks import ACTIVE_STATUSES, Role, Status, Task
from shikiru.domain.timeline import (
    TIMELINE_ACTIVITY_CATEGORY,
    TIMELINE_ALL_CATEGORY,
    TIMELINE_CATEGORIES,
    TIMELINE_DECISION_CATEGORY,
    TIMELINE_DECISION_KINDS,
    TIMELINE_DEFAULT_CATEGORY,
    TIMELINE_ENGINE_CATEGORY,
    TIMELINE_JSON_INDENT,
    TIMELINE_SOURCE_AGENT,
    TIMELINE_SOURCE_ENGINE,
    TIMELINE_TRANSITION_CATEGORY,
    event_category,
)
from shikiru.domain.timeline_phrases import event_phrase
from shikiru.errors import ShikiruError
from shikiru.operator.timeline import (
    TIMELINE_READ_ERROR_PREFIX,
    SignalBatch,
    TimelineCursor,
    TimelineFilters,
    TimelineItem,
    decode_cursor,
    encode_cursor,
    event_category_is_requested,
    initial_cursor,
    read_signal_batch,
    signal_category_is_requested,
)
from shikiru.paths import (
    engine_log_path,
    engine_output_path,
    run_dir,
    runs_root,
    state_db_path,
    task_root,
    turns_dir,
)
from shikiru.store.database import format_timestamp
from shikiru.store.escalations import Escalation
from shikiru.telemetry.disk import directory_usage
from shikiru.view_log import ordered_turn_dirs, read_tail_lines, view_log_path
from shikiru.workspace.reports import (
    ENGINE_VERDICT_FILENAME,
    InvalidTurnReportError,
    load_engine_verdict,
    load_turn_report,
)

if TYPE_CHECKING:
    from shikiru.clock import Clock
    from shikiru.domain.escalation_evidence import EscalationPacket
    from shikiru.process import ProcessIdentity
    from shikiru.store.commands import StoredCommand
    from shikiru.store.reader import StateReader
    from shikiru.store.tasks import TaskRelationships

PEEK_TAIL_LINES = 60
TASK_DETAIL_EVENT_LIMIT = 40
ENGINE_UPTIME_EVENT_LIMIT = 2
ENGINE_LIFECYCLE_EVENTS = (EventKind.ENGINE_STARTED, EventKind.ENGINE_STOPPED)
ENGINE_REPORT_SOURCE = "engine"
AGENT_REPORT_SOURCE = "agent"
TASK_REPORT_ERROR = "could not read report evidence: {error}"
TASK_FACTS_ERROR = "could not read recorded git facts: {error}"
TASK_REPORT_TURN_MISSING = "No validated report or verdict is available for turn {turn}."
TASK_FACTS_TURN_MISSING = "No recorded git facts are available for turn {turn}."
TASK_ACTION_MISSING = "task no longer exists"
TASK_ACTION_TERMINAL = "task is terminal"
_COST_BASIS_BILLED = "billed"
_COST_BASIS_API_EQUIVALENT = "api_equivalent"
TASK_ACTION_ROLE_MISSING = "role pane is unavailable"
TASK_ACTION_TAKEN_OVER = "this role is already taken over"
TASK_ACTION_SESSION_MISSING = "this role has no live session to take over"
TASK_ACTION_NOT_TAKEN_OVER = "this role is not taken over"
TASK_ACTION_OTHER_ROLE_TAKEN_OVER = "hand back all taken-over roles before resuming"
TASK_ACTION_NO_HOLD = "task has no pause or ownership hold"
TASK_ACTION_NOT_QUEUED = "task can only be prioritized while queued"
TASK_ACTION_ROLES = (Role.LEADER, Role.IMPLEMENTER)
TASK_PANE_CONTEXT_STATUSES = (*ACTIVE_STATUSES, Status.QUEUED)
TASK_TARGET_SEARCH_CATEGORY = "all"
RECENT_COMMAND_HISTORY_LIMIT = 3
PROJECT_COMMAND_HISTORY_LIMIT = 50
ESCALATION_STATUS_MISSING = "task {task_id} is missing; {escalation_id} is stale."
ESCALATION_STATUS_STALE = "task {task_id} is {status}; {escalation_id} is stale."
ESCALATION_STATUS_RESOLVED = "{escalation_id} was resolved; task {task_id} is {status}."
ESCALATION_STATUS_NOT_OPEN = "escalation {escalation_id} is not open"


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


@dataclass(frozen=True)
class TaskTargetSearchPage:
    """One bounded page of task targets and any selected target outside its search."""

    tasks: tuple[TaskSummary, ...]
    selected_task: TaskSummary | None
    matching_count: int
    project_count: int
    has_more: bool


class OperatorView(BaseModel):
    """Base model for JSON-safe operator views."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class CommandHistoryEntry(OperatorView):
    """One durable command with its stored lifecycle state and returned result."""

    id: int
    action: str
    target: str
    status: CommandStatus
    result: dict[str, object] | None
    timestamp: str


class CommandHistory(OperatorView):
    """A bounded newest-first command history and the engine's current liveness."""

    engine_up: bool
    entries: list[CommandHistoryEntry]
    offset: int
    limit: int
    total_count: int
    has_more: bool
    status: CommandStatus | None


class TaskTimelineEntry(OperatorView):
    """One engine event or normalized agent signal on a task's recorded timeline."""

    identity: str
    timestamp: str | None
    timestamp_error: str | None
    source: str
    category: str
    summary: str
    phrase: str
    glyph: str
    tone: str
    detail: str
    role: str | None
    turn: str | None
    event_id: int | None
    incomplete: bool


class TaskTimelinePage(OperatorView):
    """One bounded newest-first evidence page and its stable older-page cursor."""

    entries: list[TaskTimelineEntry]
    older_cursor: str | None
    has_older: bool
    filters: dict[str, str | None]


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


@dataclass(frozen=True)
class TaskExportFiles:
    """All retained validated report and facts files with source read errors."""

    reports: tuple[ReportRow, ...]
    facts: tuple[tuple[str, str], ...]
    errors: tuple[tuple[str, str], ...]


class TaskDetail(OperatorView):
    """A task with its runs and event timeline."""

    task: Task
    events: list[Event] = Field(default_factory=list)
    reports: list[ReportRow] = Field(default_factory=list)
    recorded_facts: str | None = None
    facts_turn: str | None = None
    evidence_errors: list[str] = Field(default_factory=list)
    branches: list[str] = Field(default_factory=list)
    sessions: list[Run] = Field(default_factory=list)


type TaskStaticFileEvidence = tuple[
    list[ReportRow],
    str | None,
    str | None,
    list[str],
]
type FileIdentity = tuple[str, int | None, int | None, int | None, str | None]
type TaskFileSignature = tuple[FileIdentity, ...]


@dataclass(frozen=True)
class TaskFileCache:
    task_id: str
    data_version: int
    roles: tuple[str, ...]
    signature: TaskFileSignature
    static_files: TaskStaticFileEvidence


@dataclass(frozen=True)
class PacketFileCache:
    modified_ns: int
    changed_ns: int
    size: int
    text: str


class EscalationResolutionView(OperatorView):
    """One resolution the engine accepts for an escalation, as its packet lists it."""

    kind: ResolutionKind
    consequences: str
    recommended: bool


class EscalationView(OperatorView):
    """An escalation row, its packet text and the decision fields parsed from that packet."""

    escalation: Escalation
    packet: str | None
    packet_error: str | None = None
    question: str | None = None
    agent_options: list[Option] = Field(default_factory=list)
    resolutions: list[EscalationResolutionView] = Field(default_factory=list)


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
    run_count: int = 0
    usage_reported_runs: int = 0
    legacy_token_runs: int = 0
    billed_cost_usd: float | None = None
    billed_cost_runs: int = 0
    legacy_billed_cost_runs: int = 0
    api_equivalent_cost_usd: float | None = None
    api_equivalent_cost_runs: int = 0
    legacy_api_equivalent_cost_runs: int = 0
    unknown_cost_runs: int = 0


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


@dataclass(frozen=True)
class HumanStatusView:
    """A status view with the bounded task rows selected for human project output."""

    status: StatusView
    task_rows: tuple[TaskSummary, ...]


StatusView.model_rebuild(_types_namespace={"datetime": datetime})


EngineState = Callable[[], bool]
EngineIdentity = Callable[[], "ProcessIdentity | None"]


class OperatorQueryError(ShikiruError):
    """A requested operator view could not be read at its boundary."""


class TaskNotFoundError(OperatorQueryError):
    """A requested task does not exist in the current project."""

    def __init__(self, task_id: str, project_root: Path) -> None:
        self.task_id = task_id
        self.project_root = project_root
        super().__init__(f"Task {task_id} was not found in this project.")


class OperatorQueries:
    """Provide read-only status, task, escalation and pane views to operators."""

    def __init__(
        self,
        reader: StateReader,
        *,
        project: str,
        project_root: Path,
        engine_up: EngineState,
        engine_identity: EngineIdentity | None = None,
        max_parallel: int,
        clock: Clock,
    ) -> None:
        """Use `reader` and `engine_up` for the operator's read boundaries."""
        self._reader = reader
        self._project = project
        self._project_root = project_root
        self._engine_up = engine_up
        self._engine_identity = engine_identity
        self._max_parallel = max_parallel
        self._clock = clock
        self._task_detail_file_cache: TaskFileCache | None = None
        self._escalation_packet_cache: dict[Path, PacketFileCache] = {}

    def status(self, task_id: str | None = None) -> StatusView:
        """Return the global status view, optionally including one task's detail."""
        tasks = self._reader.list_tasks()
        return self._build_status_view(tasks, task_id)

    def human_status(self, task_id: str | None = None) -> HumanStatusView:
        """Return status and bounded task rows from one project task snapshot."""
        tasks = self._reader.list_tasks()
        if task_id is not None and not any(task.id == task_id for task in tasks):
            raise TaskNotFoundError(task_id, self._project_root)
        status = self._build_status_view(tasks, task_id, human_task_detail=True)
        task_rows = (
            tuple(TaskSummary.from_task(task) for task in select_human_status_tasks(tasks))
            if status.task is None
            else ()
        )
        return HumanStatusView(status=status, task_rows=task_rows)

    def _build_status_view(
        self, tasks: list[Task], task_id: str | None, *, human_task_detail: bool = False
    ) -> StatusView:
        queued_tasks = sorted(
            (task for task in tasks if task.status is Status.QUEUED), key=dispatch_sort_key
        )
        summaries = [TaskSummary.from_task(task) for task in tasks]
        active = [summary for summary in summaries if summary.status in ACTIVE_STATUSES]
        queued = [TaskSummary.from_task(task) for task in queued_tasks]
        open_escalations = self.list_escalations()
        usage, quotas = self.usage_and_quotas()
        task_detail = (
            self._human_task_detail(task_id)
            if task_id is not None and human_task_detail
            else self.get_task(task_id)
            if task_id is not None
            else None
        )
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

    def _human_task_detail(self, task_id: str) -> TaskDetail | None:
        task = self._reader.get_task(task_id)
        if task is None:
            return None
        runs = self._reader.list_runs(task_id)
        turn_dirs, evidence_errors = self._task_turn_dirs(task_id)
        reports: list[ReportRow] = []
        recorded_facts = None
        facts_turn = None
        if turn_dirs is not None:
            for turn_dir in turn_dirs:
                turn_reports, turn_errors = _task_turn_reports(turn_dir)
                reports.extend(turn_reports)
                evidence_errors.extend(turn_errors)
            recorded_facts, facts_turn, fact_errors = _task_facts(turn_dirs)
            evidence_errors.extend(fact_errors)
        return TaskDetail(
            task=task,
            events=self._reader.events_since(0, task_id=task_id),
            reports=reports,
            recorded_facts=recorded_facts,
            facts_turn=facts_turn,
            evidence_errors=evidence_errors,
            branches=[task.repo.branch] if task.repo is not None else [],
            sessions=runs,
        )

    def engine_is_up(self) -> bool:
        """Return whether this project's engine currently holds its lock."""
        return self._engine_up()

    def current_engine_uptime_seconds(self) -> float | None:
        """Return current uptime only when the verified lock identity matches start evidence."""
        process_identity = self._engine_identity() if self._engine_identity is not None else None
        identity = (
            (process_identity.pid, process_identity.started_at)
            if process_identity is not None and process_identity.started_at is not None
            else None
        )
        projection = project_engine_uptime(
            self._reader.latest_events(ENGINE_UPTIME_EVENT_LIMIT, kinds=ENGINE_LIFECYCLE_EVENTS),
            now=self._clock.now(),
            running=process_identity is not None,
            identity=identity,
        )
        return projection.running_seconds

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

    def engine_log_location(self) -> str:
        """Return the path of the engine log, which holds the traceback of each logged fault."""
        return str(engine_log_path(self._project_root))

    def state_database_location(self) -> str:
        """Return the path of the state database, which records each engine fault event."""
        return str(state_db_path(self._project_root))

    def oldest_escalation_age_seconds(self) -> float | None:
        """Return the age of the oldest unresolved escalation, or `None` when none is open."""
        created = [item.created_at for item in self._reader.list_open_escalations()]
        return (self._clock.now() - min(created)).total_seconds() if created else None

    def usage_and_quotas(self) -> tuple[list[UsageView], dict[str, list[QuotaWindow]]]:
        """Return shared usage totals and provider-reported quota windows."""
        runs = self._reader.list_all_runs()
        agents = sorted({run.agent for run in runs} | set(self._reader.list_quota_agents()))
        return aggregate_usage(runs), self._reader.list_quotas(agents)

    def task_usage(self, task_id: str) -> list[UsageView]:
        """Return agent-reported usage totals for one task's sessions."""
        return aggregate_usage(self._reader.list_runs(task_id))

    def task_runs(self, task_id: str) -> tuple[Run, ...]:
        """Return every persisted session row for one task in recorded order."""
        return tuple(self._reader.list_runs(task_id))

    def task_relationships(
        self,
        task_id: str,
        *,
        offset: int = 0,
    ) -> TaskRelationships | None:
        """Return one bounded page of stored relationships for the selected task."""
        return self._reader.task_relationships(task_id, offset=offset)

    async def task_export_files(self, task_id: str) -> TaskExportFiles:
        """Load all retained validated reports and facts away from the UI event loop."""
        return await asyncio.to_thread(self._task_all_export_files, task_id)

    def list_tasks(self, *, statuses: Sequence[Status] | None = None) -> list[TaskSummary]:
        """Return task summaries ordered by their allocated task sequence."""
        return [TaskSummary.from_task(task) for task in self._reader.list_tasks(statuses=statuses)]

    def command_history(
        self,
        *,
        expanded: bool = False,
        limit: int | None = None,
        offset: int = 0,
        status: CommandStatus | None = None,
    ) -> CommandHistory:
        """Return one bounded newest-first page, preserving stored command outcomes."""
        default_limit = PROJECT_COMMAND_HISTORY_LIMIT if expanded else RECENT_COMMAND_HISTORY_LIMIT
        page_limit = default_limit if limit is None else limit
        page = self._reader.command_history(limit=page_limit, offset=offset, status=status)
        return CommandHistory(
            engine_up=self._engine_up(),
            entries=[self._command_entry(row) for row in page.entries],
            offset=page.offset,
            limit=page.limit,
            total_count=page.total_count,
            has_more=page.has_more,
            status=status,
        )

    def command_status(self, command_id: int) -> CommandHistoryEntry | None:
        """Read one durable command status by id without creating or acknowledging it."""
        row = self._reader.command_status(command_id)
        return self._command_entry(row) if row is not None else None

    def pending_command(self, command: Command) -> CommandHistoryEntry | None:
        """Find an identical pending command without writing or acknowledging it."""
        row = self._reader.pending_command(command)
        return self._command_entry(row) if row is not None else None

    def _command_entry(self, row: StoredCommand) -> CommandHistoryEntry:
        return CommandHistoryEntry(
            id=row.id,
            action=row.command.kind.value.replace("_", " ").capitalize(),
            target=_command_target(row.command, self._project),
            status=row.status,
            result=row.result,
            timestamp=row.ts,
        )

    def search_task_targets(
        self,
        search: str,
        *,
        limit: int,
        offset: int,
        selected_task_id: str | None = None,
    ) -> TaskTargetSearchPage:
        """Read a bounded ID/title target page and retain a selected target outside the match."""
        page = self._reader.task_browser_page(
            category=TASK_TARGET_SEARCH_CATEGORY,
            search=search,
            limit=limit,
            offset=offset,
        )
        tasks = tuple(TaskSummary.from_task(task) for task in page.tasks)
        selected_task = None
        if selected_task_id is not None and all(task.id != selected_task_id for task in tasks):
            task = self._reader.get_task(selected_task_id)
            if task is not None:
                selected_task = TaskSummary.from_task(task)
        return TaskTargetSearchPage(
            tasks=tasks,
            selected_task=selected_task,
            matching_count=page.matching_count,
            project_count=page.project_count,
            has_more=offset + len(tasks) < page.matching_count,
        )

    def task_action_role(self, task_id: str) -> Role:
        """Return the role at this task's current or first scheduled step."""
        task = self._reader.get_task(task_id)
        if task is None:
            return Role.LEADER
        return task_acting_role(task)

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
        roles = tuple(dict.fromkeys(run.role.value for run in runs))
        data_version = self._reader.data_version()
        cached = self._task_detail_file_cache
        signature = cached.signature if cached is not None else ()
        if cached is not None and cached.task_id == task_id and cached.roles == roles:
            if cached.data_version != data_version:
                signature = await asyncio.to_thread(self._task_file_signature, task_id)
            if signature == cached.signature:
                static_files = cached.static_files
                self._task_detail_file_cache = TaskFileCache(
                    task_id,
                    data_version,
                    roles,
                    signature,
                    static_files,
                )
            else:
                signature, static_files = await asyncio.to_thread(
                    self._task_static_files_with_signature, task_id
                )
                self._task_detail_file_cache = TaskFileCache(
                    task_id,
                    data_version,
                    roles,
                    signature,
                    static_files,
                )
        else:
            signature, static_files = await asyncio.to_thread(
                self._task_static_files_with_signature, task_id
            )
            self._task_detail_file_cache = TaskFileCache(
                task_id,
                data_version,
                roles,
                signature,
                static_files,
            )
        reports, recorded_facts, facts_turn, evidence_errors = static_files
        return TaskDetail(
            task=task,
            events=events,
            reports=reports,
            recorded_facts=recorded_facts,
            facts_turn=facts_turn,
            evidence_errors=evidence_errors,
            branches=[task.repo.branch] if task.repo is not None else [],
            sessions=runs,
        )

    async def task_timeline(
        self,
        task_id: str,
        *,
        limit: int,
        cursor: str | None = None,
        role: str | None = None,
        turn: str | None = None,
        category: str = TIMELINE_DEFAULT_CATEGORY,
        scan_limit: int,
    ) -> TaskTimelinePage | None:
        """Read a bounded, stable page across task events and normalized signal files."""
        if not 0 < limit <= MAX_TIMELINE_PAGE_SIZE:
            message = f"task evidence page size must be between 1 and {MAX_TIMELINE_PAGE_SIZE}"
            raise ValueError(message)
        if not 0 < scan_limit <= MAX_TIMELINE_SIGNAL_SCAN_ROWS:
            message = (
                "task evidence source scan size must be between 1 and "
                f"{MAX_TIMELINE_SIGNAL_SCAN_ROWS}"
            )
            raise ValueError(message)
        if category not in TIMELINE_CATEGORIES:
            message = f"unknown task evidence category: {category}"
            raise ValueError(message)
        task = self._reader.get_task(task_id)
        if task is None:
            return None
        filters = TimelineFilters(role=role, turn=turn, category=category)
        roles = _timeline_roles(self._reader.list_runs(task_id), role)
        paths = {item: view_log_path(run_dir(self._project_root, task_id), item) for item in roles}
        current = await self._timeline_cursor(cursor, filters, roles, paths)
        if cursor is not None:
            roles = tuple(sorted(current.signal_before))
        event_rows = self._timeline_events(
            task_id, limit, current, role=role, turn=turn, category=category
        )
        signal_batches = await self._timeline_signals(
            paths=paths,
            roles=roles,
            limit=limit,
            cursor=current,
            filters=filters,
            scan_limit=scan_limit,
        )
        return _build_timeline_page(
            limit=limit,
            current=current,
            filters=filters,
            event_rows=event_rows,
            signal_batches=signal_batches,
        )

    async def _timeline_cursor(
        self,
        token: str | None,
        filters: TimelineFilters,
        roles: Sequence[str],
        paths: dict[str, Path],
    ) -> TimelineCursor:
        if token is None:
            return await asyncio.to_thread(initial_cursor, paths)
        return decode_cursor(token, filters=filters, roles=roles)

    def _timeline_events(
        self,
        task_id: str,
        limit: int,
        cursor: TimelineCursor,
        *,
        role: str | None,
        turn: str | None,
        category: str,
    ) -> list[Event]:
        if not event_category_is_requested(category):
            return []
        return self._reader.task_timeline_events(
            task_id,
            limit=limit + 1,
            before=cursor.event_before,
            kinds=_timeline_event_kinds(category),
            role=role,
            turn=turn,
        )

    async def _timeline_signals(
        self,
        *,
        paths: dict[str, Path],
        roles: Sequence[str],
        limit: int,
        cursor: TimelineCursor,
        filters: TimelineFilters,
        scan_limit: int,
    ) -> dict[str, SignalBatch]:
        if not signal_category_is_requested(filters.category):
            return {}
        loaded = await asyncio.gather(
            *(
                asyncio.to_thread(
                    read_signal_batch,
                    paths[item],
                    role=item,
                    before_offset=cursor.signal_before[item],
                    filters=filters,
                    limit=limit + 1,
                    scan_limit=scan_limit,
                )
                for item in roles
            )
        )
        return dict(zip(roles, loaded, strict=True))

    def get_task_state(self, task_id: str) -> Task | None:
        """Return current task state without loading report or signal files."""
        return self._reader.get_task(task_id)

    def task_action_availability(
        self, task_id: str, role: Role | None
    ) -> tuple[TaskActionAvailability, ...]:
        """Project task command availability and disabled reasons from current state."""
        task = self._reader.get_task(task_id)
        if task is None:
            return tuple(
                TaskActionAvailability(action, TASK_ACTION_MISSING) for action in OperatorTaskAction
            )
        runs = self._reader.list_runs(task_id)
        roles = (role,) if role is not None else TASK_ACTION_ROLES
        role_states = tuple(self._task_action_reasons(task, runs, candidate) for candidate in roles)
        if role is None:
            return tuple(
                TaskActionAvailability(
                    action,
                    next(
                        (states[action] for states in role_states if states[action] is None),
                        role_states[0][action],
                    ),
                )
                for action in OperatorTaskAction
            )
        return tuple(
            TaskActionAvailability(action, role_states[0][action]) for action in OperatorTaskAction
        )

    def _task_action_reasons(
        self, task: Task, runs: Sequence[Run], role: Role
    ) -> dict[OperatorTaskAction, str | None]:
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
        return {
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
        """Return every durable open decision with packet text or its read error."""
        escalations = self._reader.list_open_escalations()
        escalations.sort(key=lambda item: (item.created_at, item.id))
        packet_paths = {self._packet_path(item) for item in escalations}
        self._escalation_packet_cache = {
            path: cached
            for path, cached in self._escalation_packet_cache.items()
            if path in packet_paths
        }
        return [self._escalation_view(escalation) for escalation in escalations]

    def get_escalation(self, escalation_id: str) -> EscalationView | None:
        """Return one escalation and packet text, or `None` when absent."""
        escalation = self._reader.get_escalation(escalation_id)
        if escalation is None:
            return None
        return self._escalation_view(escalation)

    def escalation_resolution_disabled_reason(self, escalation_id: str) -> str | None:
        """Project whether current durable escalation and task state allow resolution."""
        escalation = self._reader.get_escalation(escalation_id)
        if escalation is None:
            return ESCALATION_STATUS_NOT_OPEN.format(escalation_id=escalation_id)
        task = self._reader.get_task(escalation.task_id)
        task_state = task.status.value if task is not None else "missing"
        if escalation.status is not EscalationStatus.OPEN:
            return ESCALATION_STATUS_RESOLVED.format(
                escalation_id=escalation.id,
                task_id=escalation.task_id,
                status=task_state,
            )
        if task is None:
            return ESCALATION_STATUS_MISSING.format(
                task_id=escalation.task_id,
                escalation_id=escalation.id,
            )
        if task.status is not Status.ESCALATED:
            return ESCALATION_STATUS_STALE.format(
                task_id=escalation.task_id,
                status=task_state,
                escalation_id=escalation.id,
            )
        return None

    def _escalation_view(self, escalation: Escalation) -> EscalationView:
        try:
            packet = self._packet_text(escalation)
        except OperatorQueryError as error:
            return EscalationView(
                escalation=escalation,
                packet=None,
                packet_error=str(error),
            )
        parsed = parse_escalation_packet(packet)
        request = parsed.agent_request
        return EscalationView(
            escalation=escalation,
            packet=packet,
            question=_escalation_question(parsed),
            agent_options=request.options if request is not None else [],
            resolutions=_escalation_resolutions(parsed, escalation),
        )

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
        packet_path = self._packet_path(escalation)
        try:
            info = packet_path.stat()
        except FileNotFoundError:
            message = f"escalation packet is missing: {packet_path}"
            raise OperatorQueryError(message) from None
        except OSError as error:
            message = f"cannot inspect escalation packet {packet_path}: {error}"
            raise OperatorQueryError(message) from error
        cached = self._escalation_packet_cache.get(packet_path)
        if (
            cached is not None
            and cached.modified_ns == info.st_mtime_ns
            and cached.changed_ns == info.st_ctime_ns
            and cached.size == info.st_size
        ):
            return cached.text
        try:
            packet = packet_path.read_text()
        except (OSError, UnicodeError) as error:
            message = f"cannot read escalation packet {packet_path}: {error}"
            raise OperatorQueryError(message) from error
        self._escalation_packet_cache[packet_path] = PacketFileCache(
            info.st_mtime_ns,
            info.st_ctime_ns,
            info.st_size,
            packet,
        )
        return packet

    def _packet_path(self, escalation: Escalation) -> Path:
        packet_path = Path(escalation.packet_path)
        return packet_path if packet_path.is_absolute() else self._project_root / packet_path

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

    def _task_static_files_with_signature(
        self,
        task_id: str,
    ) -> tuple[TaskFileSignature, TaskStaticFileEvidence]:
        return self._task_file_signature(task_id), self._task_static_files(task_id)

    def _task_file_signature(self, task_id: str) -> TaskFileSignature:
        task_turns_dir = turns_dir(self._project_root, task_id)
        turn_dirs, errors = self._task_turn_dirs(task_id)
        if turn_dirs is None:
            return ((str(task_turns_dir), None, None, None, "; ".join(errors)),)
        filenames = (
            FACTS_FILENAME,
            REPORT_KIND_FILENAME,
            REPORT_FILENAME,
            HELPER_REPORT_FILENAME,
            ENGINE_VERDICT_FILENAME,
        )
        signature: list[FileIdentity] = []
        for turn_dir in turn_dirs:
            for filename in filenames:
                path = turn_dir / filename
                try:
                    info = path.stat()
                except FileNotFoundError:
                    signature.append((str(path), None, None, None, None))
                except OSError as error:
                    signature.append((str(path), None, None, None, str(error)))
                else:
                    signature.append(
                        (
                            str(path),
                            info.st_mtime_ns,
                            info.st_ctime_ns,
                            info.st_size,
                            None,
                        )
                    )
        return tuple(signature)

    def _task_static_files(self, task_id: str) -> TaskStaticFileEvidence:
        turn_dirs, errors = self._task_turn_dirs(task_id)
        if turn_dirs is None:
            return [], None, None, errors
        reports, report_errors = self._task_reports(turn_dirs)
        recorded_facts, facts_turn, fact_errors = _task_facts(turn_dirs)
        return reports, recorded_facts, facts_turn, [*errors, *report_errors, *fact_errors]

    def _task_all_export_files(self, task_id: str) -> TaskExportFiles:
        turn_dirs, directory_errors = self._task_turn_dirs(task_id)
        errors = [("reports", error) for error in directory_errors]
        if turn_dirs is None:
            return TaskExportFiles(reports=(), facts=(), errors=tuple(errors))
        reports: list[ReportRow] = []
        facts: list[tuple[str, str]] = []
        missing_report_turns: list[str] = []
        missing_fact_turns: list[str] = []
        for turn_dir in turn_dirs:
            turn_reports, report_errors = _task_turn_reports(turn_dir)
            reports.extend(turn_reports)
            errors.extend(("reports", error) for error in report_errors)
            if not turn_reports and not report_errors:
                missing_report_turns.append(turn_dir.name)
            facts_path = turn_dir / FACTS_FILENAME
            try:
                facts_content = facts_path.read_text()
            except FileNotFoundError:
                missing_fact_turns.append(turn_dir.name)
                continue
            except (OSError, UnicodeError) as error:
                errors.append(("facts", TASK_FACTS_ERROR.format(error=error)))
            else:
                facts.append((turn_dir.name, facts_content))
        if reports:
            errors.extend(
                ("reports", TASK_REPORT_TURN_MISSING.format(turn=turn))
                for turn in missing_report_turns
            )
        if facts:
            errors.extend(
                ("facts", TASK_FACTS_TURN_MISSING.format(turn=turn)) for turn in missing_fact_turns
            )
        return TaskExportFiles(
            reports=tuple(reports),
            facts=tuple(facts),
            errors=tuple(errors),
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


def _timeline_roles(runs: Sequence[Run], role: str | None) -> tuple[str, ...]:
    if role is not None:
        return (Role(role).value,)
    return tuple(sorted({run.role.value for run in runs}))


def _build_timeline_page(
    *,
    limit: int,
    current: TimelineCursor,
    filters: TimelineFilters,
    event_rows: Sequence[Event],
    signal_batches: dict[str, SignalBatch],
) -> TaskTimelinePage:
    streams: dict[str, list[TimelineItem]] = {}
    if event_rows:
        streams[TIMELINE_SOURCE_ENGINE] = [_event_timeline_item(event) for event in event_rows]
    streams.update(
        {
            f"{TIMELINE_SOURCE_AGENT}:{role}": list(batch.items)
            for role, batch in signal_batches.items()
            if batch.items
        }
    )
    merged = _merge_timeline_streams(streams, limit + 1)
    entries = merged[:limit]
    has_older = len(merged) > limit or any(batch.has_more for batch in signal_batches.values())
    older_cursor = (
        _next_timeline_cursor(current, entries, signal_batches, filters) if has_older else None
    )
    return TaskTimelinePage(
        entries=[_timeline_entry(item) for item in entries],
        older_cursor=older_cursor,
        has_older=has_older,
        filters={"role": filters.role, "turn": filters.turn, "category": filters.category},
    )


def _next_timeline_cursor(
    current: TimelineCursor,
    entries: Sequence[TimelineItem],
    signal_batches: dict[str, SignalBatch],
    filters: TimelineFilters,
) -> str:
    next_event = current.event_before
    next_signals = dict(current.signal_before)
    consumed_roles: set[str] = set()
    for item in entries:
        if item.event_id is not None and item.timestamp is not None:
            next_event = (format_timestamp(item.timestamp), item.event_id)
        elif item.role is not None:
            consumed_roles.add(item.role)
            next_signals[item.role] = (
                -1
                if item.identity.startswith(("missing:", TIMELINE_READ_ERROR_PREFIX))
                else item.signal_offset
            )
    for role, batch in signal_batches.items():
        if role not in consumed_roles:
            next_signals[role] = batch.replay_offset if batch.items else batch.scan_offset
    return encode_cursor(
        TimelineCursor(event_before=next_event, signal_before=next_signals), filters
    )


def _timeline_event_kinds(category: str) -> tuple[EventKind, ...] | None:
    if category in {
        TIMELINE_ACTIVITY_CATEGORY,
        TIMELINE_ALL_CATEGORY,
        TIMELINE_ENGINE_CATEGORY,
    }:
        return None
    if category == TIMELINE_DECISION_CATEGORY:
        return tuple(EventKind(kind) for kind in sorted(TIMELINE_DECISION_KINDS))
    if category == TIMELINE_TRANSITION_CATEGORY:
        return tuple(kind for kind in EventKind if kind.value not in TIMELINE_DECISION_KINDS)
    return ()


def _event_timeline_item(event: Event) -> TimelineItem:
    role = event.data.get("role")
    turn = event.data.get("turn")
    return TimelineItem(
        identity=f"engine:{event.id}",
        timestamp=event.ts,
        source=TIMELINE_SOURCE_ENGINE,
        category=event_category(event.kind.value),
        summary=event.message,
        phrase=event_phrase(event),
        detail=json.dumps(
            event.data, ensure_ascii=False, sort_keys=True, indent=TIMELINE_JSON_INDENT
        ),
        role=role if isinstance(role, str) else None,
        turn=turn if isinstance(turn, str) else None,
        event_id=event.id,
    )


def _merge_timeline_streams(
    streams: dict[str, list[TimelineItem]], limit: int
) -> list[TimelineItem]:
    offsets = dict.fromkeys(streams, 0)
    merged: list[TimelineItem] = []
    while len(merged) < limit:
        available = [
            (source, items[offsets[source]])
            for source, items in streams.items()
            if offsets[source] < len(items)
        ]
        if not available:
            break
        dated = [item for _, item in available if item.timestamp is not None]
        if dated:
            newest = max(item.timestamp for item in dated if item.timestamp is not None)
            tied = [pair for pair in available if pair[1].timestamp == newest]
        else:
            tied = available
        source, item = min(tied, key=_timeline_tie_break)
        merged.append(item)
        offsets[source] += 1
    return merged


def _timeline_tie_break(pair: tuple[str, TimelineItem]) -> tuple[int, str, int]:
    source, item = pair
    source_rank = 0 if item.source == TIMELINE_SOURCE_ENGINE else 1
    stable_position = -(item.event_id or item.signal_offset or 0)
    return source_rank, item.role or source, stable_position


def _timeline_entry(item: TimelineItem) -> TaskTimelineEntry:
    return TaskTimelineEntry(
        identity=item.identity,
        timestamp=item.timestamp.isoformat() if item.timestamp is not None else None,
        timestamp_error=item.timestamp_error,
        source=item.source,
        category=item.category,
        summary=item.summary,
        phrase=item.phrase.text,
        glyph=item.phrase.glyph,
        tone=item.phrase.tone.value,
        detail=item.detail,
        role=item.role,
        turn=item.turn,
        event_id=item.event_id,
        incomplete=item.incomplete,
    )


def _command_target(command: Command, project: str) -> str:
    if isinstance(command, Submit):
        if command.task_id is None:
            message = "stored submit command has no allocated task id"
            raise ValueError(message)
        return command.task_id
    if isinstance(command, Resolve):
        return command.escalation_id
    if isinstance(command, Stop):
        return project
    if isinstance(command, (Say, SetRung, TakeOver, HandBack)):
        return f"{command.task_id} · {command.role.value}"
    return command.task_id


def aggregate_usage(runs: Sequence[Run]) -> list[UsageView]:
    """Aggregate recorded runs with the shared token and cost provenance rules."""
    by_agent: dict[str, list[Run]] = {}
    for run in runs:
        by_agent.setdefault(run.agent, []).append(run)
    views: list[UsageView] = []
    for agent in sorted(by_agent):
        agent_runs = by_agent[agent]
        reported_runs = [run for run in agent_runs if run.usage_reported is True]
        legacy_token_runs = [
            run
            for run in agent_runs
            if run.usage_reported is None and has_positive_token_evidence(run)
        ]
        token_runs = [*reported_runs, *legacy_token_runs]
        cost_bases: set[CostBasis] = {
            run.usage.cost_basis for run in agent_runs if _has_priced_cost_evidence(run)
        }
        billed_runs = [
            run
            for run in agent_runs
            if _has_priced_cost_evidence(run) and run.usage.cost_basis == _COST_BASIS_BILLED
        ]
        api_equivalent_runs = [
            run
            for run in agent_runs
            if _has_priced_cost_evidence(run) and run.usage.cost_basis == _COST_BASIS_API_EQUIVALENT
        ]
        priced_run_count = len(billed_runs) + len(api_equivalent_runs)
        unknown_cost_runs = len(agent_runs) - priced_run_count
        cost_basis: CostBasis | Literal["mixed"]
        if unknown_cost_runs:
            cost_usd = None
            cost_basis = COST_BASIS_UNKNOWN
        elif len(cost_bases) > 1:
            cost_usd = None
            cost_basis = "mixed"
        else:
            cost_usd = _priced_total(billed_runs or api_equivalent_runs)
            cost_basis = next(iter(cost_bases)) if cost_bases else COST_BASIS_UNKNOWN
        views.append(
            UsageView(
                agent=agent,
                input_tokens=sum(run.usage.input_tokens for run in token_runs),
                output_tokens=sum(run.usage.output_tokens for run in token_runs),
                cache_read_tokens=sum(run.usage.cache_read_tokens for run in token_runs),
                cache_write_tokens=sum(run.usage.cache_write_tokens for run in token_runs),
                cost_usd=cost_usd,
                cost_basis=cost_basis,
                run_count=len(agent_runs),
                usage_reported_runs=len(reported_runs),
                legacy_token_runs=len(legacy_token_runs),
                billed_cost_usd=_priced_total(billed_runs),
                billed_cost_runs=len(billed_runs),
                legacy_billed_cost_runs=sum(run.usage_reported is None for run in billed_runs),
                api_equivalent_cost_usd=_priced_total(api_equivalent_runs),
                api_equivalent_cost_runs=len(api_equivalent_runs),
                legacy_api_equivalent_cost_runs=sum(
                    run.usage_reported is None for run in api_equivalent_runs
                ),
                unknown_cost_runs=unknown_cost_runs,
            )
        )
    return views


def _has_priced_cost_evidence(run: Run) -> bool:
    return (
        run.usage_reported is not False
        and run.usage.cost_usd is not None
        and run.usage.cost_basis in (_COST_BASIS_BILLED, _COST_BASIS_API_EQUIVALENT)
    )


def _priced_total(runs: Sequence[Run]) -> float | None:
    if not runs:
        return None
    return sum((run.usage.cost_usd for run in runs if run.usage.cost_usd is not None), 0.0)


def _escalation_question(parsed: EscalationPacket) -> str | None:
    """Prefer the agent's question, as the dashboard does; restored packets hold the headline."""
    request = parsed.agent_request
    if request is not None and request.question:
        return request.question
    return parsed.question


def _escalation_resolutions(
    parsed: EscalationPacket, escalation: Escalation
) -> list[EscalationResolutionView]:
    recommended = {option.label for option in escalation.options if option.recommended}
    return [
        EscalationResolutionView(
            kind=resolution.kind,
            consequences=resolution.consequences,
            recommended=resolution.kind in recommended,
        )
        for resolution in parsed.resolutions
    ]
