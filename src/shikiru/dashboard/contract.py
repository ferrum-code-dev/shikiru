"""Stable view models and plugin protocols for dashboard widgets (06 §5.4)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Literal, Protocol

from shikiru.agents.contracts import (
    BILLING_MODE_UNKNOWN,
    BILLING_SOURCE_UNKNOWN,
    COST_BASIS_UNKNOWN,
)

if TYPE_CHECKING:
    from datetime import datetime

    from textual.screen import ModalScreen
    from textual.widget import Widget

    from shikiru.agents.contracts import BillingMode, BillingSource, CostBasis
    from shikiru.clock import Clock
    from shikiru.domain.briefing import (
        VerdictCounts,
    )
    from shikiru.domain.decisions import TaskDecisions
    from shikiru.domain.levels import UsageLevel
    from shikiru.domain.results import TaskResults
    from shikiru.domain.tasks import EscalationReason, Role
    from shikiru.ui.strip import TaskStripData

from shikiru.domain.timeline import (
    TIMELINE_ACTIVITY_CATEGORY,
    TIMELINE_AGENT_CATEGORY,
    TIMELINE_ALL_CATEGORY,
    TIMELINE_DECISION_CATEGORY,
    TIMELINE_ENGINE_CATEGORY,
    TIMELINE_MESSAGE_CATEGORY,
    TIMELINE_SIGNAL_CATEGORY,
    TIMELINE_SOURCE_AGENT,
    TIMELINE_SOURCE_ENGINE,
    TIMELINE_TOOL_CATEGORY,
    TIMELINE_TRANSITION_CATEGORY,
    TIMELINE_USAGE_CATEGORY,
)

WIDGET_ENTRY_POINT_GROUP = "shikiru.widgets"
FOCUS_FAULTS_VIEW = "widget-faults"
FOCUS_PROJECT_DIAGNOSTICS_VIEW = "project-diagnostics"
TASK_STATUS_QUEUED = "queued"
TASK_TERMINAL_STATUSES = frozenset({"done", "cancelled"})


class TaskTimelineCategory(StrEnum):
    """A timeline category exposed by the dashboard contract."""

    ACTIVITY = TIMELINE_ACTIVITY_CATEGORY
    AGENT = TIMELINE_AGENT_CATEGORY
    ALL = TIMELINE_ALL_CATEGORY
    DECISION = TIMELINE_DECISION_CATEGORY
    ENGINE = TIMELINE_ENGINE_CATEGORY
    MESSAGE = TIMELINE_MESSAGE_CATEGORY
    SIGNAL = TIMELINE_SIGNAL_CATEGORY
    TOOL = TIMELINE_TOOL_CATEGORY
    TRANSITION = TIMELINE_TRANSITION_CATEGORY
    USAGE = TIMELINE_USAGE_CATEGORY


class TaskTimelineSource(StrEnum):
    """A persisted evidence source exposed by the dashboard contract."""

    AGENT = TIMELINE_SOURCE_AGENT
    ENGINE = TIMELINE_SOURCE_ENGINE


DEFAULT_TASK_TIMELINE_REFRESH_SECONDS = 1
DEFAULT_NOW_MAX_LINES = 6
DEFAULT_NOW_COMPACT_LINES = 2
OnChange = Callable[[], Awaitable[None]]
OnChangeRegistration = Callable[[OnChange], Awaitable[None]]
MotionRegistration = Callable[[OnChange], Callable[[], None]]


@dataclass(frozen=True)
class PaletteDraft:
    """One transient contextual form draft retained while its caller is visible."""

    task_id: str
    action: str
    guidance: str
    role: str | None = None
    role_explicit: bool = False


class TaskPaletteRoute(StrEnum):
    """A contextual palette destination opened in the task or project workspace."""

    INSPECT = "inspect"
    EVIDENCE = "evidence"
    USAGE = "usage"
    COMMANDS = "commands"
    DIAGNOSTICS = "diagnostics"


@dataclass(frozen=True)
class ExtensionEntry:
    """An extension widget listed in the palette, opened through its contextual view."""

    choice_id: str
    label: str


@dataclass(frozen=True)
class TaskRouteChoice:
    """A task destination chosen from the Actions list, with the task it was chosen for."""

    task_id: str
    route: TaskPaletteRoute


@dataclass(frozen=True)
class ProjectRouteChoice:
    """A project destination chosen from the Actions list."""

    route: TaskPaletteRoute


PaletteReturn = TaskRouteChoice | ProjectRouteChoice | ExtensionEntry


class PaletteAction(StrEnum):
    """An operator action the palette popups can open."""

    SUBMIT = "submit"
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
    STOP = "stop"
    EXPORT = "export"


@dataclass(frozen=True)
class PaletteRequest:
    """Optional task, role and action context for the shared operator palette."""

    action: str | None = None
    task_id: str | None = None
    role: str | None = None
    drafts: tuple[PaletteDraft, ...] = ()


OpenPalette = Callable[[str], Awaitable[None]]
OpenPaletteContext = Callable[[PaletteRequest], Awaitable[None]]
OpenResolution = Callable[[str], Awaitable[None]]
ResolutionScreenFactory = Callable[[str], "ModalScreen[bool]"]
SelectTaskContext = Callable[[str | None], None]
OpenTaskDetail = Callable[[str], Awaitable[None]]
OpenTaskDetailView = Callable[[str, str], Awaitable[None]]
NavigateTaskWindow = Callable[[str], Awaitable[str]]
NavigateHome = Callable[[], Awaitable[str]]
OpenContextualView = Callable[[str], Awaitable[None]]
ActivateAttentionDecision = Callable[[], Awaitable[bool]]


class TaskBrowserFilter(StrEnum):
    """A task browser category (Workspace §5.1) or one exact task status."""

    ACTIVE = "active"
    WAITING = "waiting"
    HISTORY = "history"
    ALL = "all"
    RUNNING = "running"
    INTEGRATING = "integrating"
    ESCALATED = "escalated"
    QUEUED = "queued"
    DONE = "done"
    CANCELLED = "cancelled"


class WidgetPlacement(StrEnum):
    """A compatible optional placement for a configured dashboard widget."""

    IDENTITY = "identity"
    TASK_LIST = "task_list"
    ATTENTION = "attention"
    PROJECT_USAGE = "project_usage"
    RESOURCES = "resources"
    ACTIVITY = "activity"
    DETAILED_USAGE = "detailed_usage"
    DIAGNOSTICS = "diagnostics"
    WORKFLOW = "workflow"
    EXTENSIONS = "extensions"


class CommandFeedbackStatus(StrEnum):
    """Operator-facing names for stored command lifecycle states."""

    PENDING = "Pending"
    APPLIED = "Applied"
    REJECTED = "Rejected"


class DiagnosticSampleState(StrEnum):
    """An explicit state for a secondary diagnostic observation."""

    AVAILABLE = "available"
    EMPTY = "empty"
    UNAVAILABLE = "unavailable"
    STALE = "stale"
    FAILED = "failed"


class TaskHistoryGap(StrEnum):
    """A semantic reason that retained task turn history is incomplete."""

    TRUNCATED = "history_truncated"
    UNLINKED_TURN = "unlinked_turn"
    MISSING_HISTORY = "missing_history"
    UNENDED_TURN = "unended_turn"
    MISSING_TURN_START = "missing_turn_start"
    MISSING_RECORDED_EFFORT = "missing_recorded_effort"
    MISSING_USAGE_DELTA = "missing_usage_delta"
    INCOMPLETE_IDENTITY = "incomplete_turn_identity"
    INCONSISTENT_IDENTITY = "inconsistent_turn_identity"
    READ_FAILED = "history_read_failed"


@dataclass(frozen=True)
class RepoStatus:
    """The project checkout's state, as shown by the `header` widget (06 §5.4)."""

    branch: str | None
    ahead: int | None
    behind: int | None
    dirty: int | None
    sampled_ago: str | None = None
    root: str | None = None
    added_lines: int | None = None
    deleted_lines: int | None = None
    binary_entries: int | None = None
    untracked_paths: int | None = None
    state: DiagnosticSampleState = DiagnosticSampleState.AVAILABLE
    error: str | None = None


@dataclass(frozen=True)
class EngineStatusView:
    """Whether the engine runs, and how long the live or previous run lasted."""

    up: bool
    uptime_seconds: float | None = None
    previous_run_seconds: float | None = None


@dataclass(frozen=True)
class HeaderView:
    """Project and engine summary rendered by the header widget."""

    project: str
    engine_up: bool
    occupied_slots: int
    maximum_slots: int
    repo: RepoStatus
    alerts: tuple[str, ...]
    integration_mode: str = ""
    engine_uptime_seconds: float | None = None
    previous_engine_run_seconds: float | None = None


@dataclass(frozen=True)
class RecordedUsageView:
    """Recorded project or task usage with token, billing and reporting coverage."""

    session_count: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    reported_sessions: int
    legacy_token_sessions: int
    unknown_token_sessions: int
    billed_cost_usd: float | None
    billed_cost_sessions: int
    api_billed_cost_usd: float | None
    api_billed_cost_sessions: int
    api_cost_unreported_sessions: int
    api_equivalent_cost_usd: float | None
    api_equivalent_cost_sessions: int
    unknown_billing_sessions: int
    subscription_sessions: int
    subscription_agents: tuple[str, ...]


@dataclass(frozen=True)
class WorkspaceSummaryView:
    """Task and attention counts for the home summary controls."""

    attention_groups: int
    decisions: int
    stalls: int
    faults: int
    running: int
    waiting: int
    engine_up: bool | None = None
    held_tasks: int | None = None
    holds: tuple[str, ...] | None = None
    operator_owned: tuple[str, ...] | None = None
    held_task_states: tuple[HeldTaskStateView, ...] | None = None

    @property
    def attention(self) -> int:
        """Count grouped attention rows, which may overlap waiting tasks."""
        return self.attention_groups


@dataclass(frozen=True)
class HeldTaskStateView:
    """The observed holds and operator-owned roles for one task."""

    task_id: str
    holds: tuple[str, ...]
    operator_owned_roles: tuple[str, ...]


@dataclass(frozen=True)
class QueueRow:
    """One queued task row, already ordered by the scheduler's dispatch rules."""

    task_id: str
    title: str
    priority: int
    dependencies: tuple[str, ...]
    wait_reason: str | None


@dataclass(frozen=True)
class TaskBrowserRow:
    """One task row with its identity and state prepared for the selectable browser."""

    task_id: str
    title: str
    status: str
    step: str | None
    iteration: int
    acting_role: str | None
    priority: int
    dependencies: tuple[str, ...]
    wait_reason: str | None
    holds: tuple[str, ...]
    age: str
    group_label: str | None
    open_decision: bool
    diagnosed_stall: bool
    requeued_age: str | None = None
    agent_processing: bool = False


@dataclass(frozen=True)
class TaskBrowserView:
    """A bounded page of matching tasks and the selected task's retained context."""

    filter: TaskBrowserFilter
    search: str
    rows: tuple[TaskBrowserRow, ...]
    selected_task: TaskBrowserRow | None
    matching_count: int
    filtered_count: int
    project_count: int
    has_more: bool


@dataclass(frozen=True)
class TaskReferenceView:
    """A related task's identity and recorded workflow state."""

    task_id: str
    title: str
    status: str
    status_reason: str | None
    mode: str | None


@dataclass(frozen=True)
class TaskTestView:
    """An agent-reported test entry with its source made explicit."""

    name: str
    result: str
    details: str
    provenance: str


@dataclass(frozen=True)
class TaskReportView:
    """One validated agent report or engine verdict for task detail."""

    turn: str
    kind: str
    role: str
    provenance: str
    summary: str
    decision: str | None
    question: str
    issues: tuple[str, ...]
    risks: tuple[str, ...]
    tests: tuple[TaskTestView, ...]


@dataclass(frozen=True)
class TaskSessionView:
    """One task session's recorded identity, status and usage."""

    role: str
    agent: str
    model: str
    effort: str | None
    session_id: str | None
    status: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: float | None
    cost_basis: CostBasis
    context_used_pct: float | None
    usage_reported: bool | None = None
    legacy_token_totals: bool = False
    legacy_cost_recorded: bool = False
    billing_mode: BillingMode = BILLING_MODE_UNKNOWN
    billing_source: BillingSource = BILLING_SOURCE_UNKNOWN
    run_id: int | None = None
    current_lineage: bool = True
    model_history: tuple[TaskModelHistoryView, ...] = ()
    history_gaps: tuple[TaskHistoryGap, ...] = ()
    context_level: UsageLevel | None = None


@dataclass(frozen=True)
class TaskTurnUsageDeltaView:
    """Usage delta recorded for one completed agent turn."""

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: float | None
    cost_basis: CostBasis = COST_BASIS_UNKNOWN


@dataclass(frozen=True)
class TaskModelHistoryView:
    """One bounded turn observation with model identity and per-turn usage, when recorded."""

    agent: str
    model: str
    effort: str | None
    turn: str | None
    status: str
    usage_delta: TaskTurnUsageDeltaView | None = None
    identity_complete: bool = False


@dataclass(frozen=True)
class TaskMilestoneView:
    """One workflow stage and the recorded outcome shown in the compact inspector."""

    stage: str
    state: str


@dataclass(frozen=True)
class TaskTimelineEntryView:
    """One recorded engine event or normalized agent signal with stable identity."""

    identity: str
    timestamp: str | None
    source: str
    category: str
    summary: str
    phrase: str
    glyph: str
    tone: str
    detail: str
    role: str | None
    turn: str | None


@dataclass(frozen=True)
class TaskTimelinePageView:
    """A bounded newest-first task evidence page with an older-page cursor."""

    entries: tuple[TaskTimelineEntryView, ...]
    older_cursor: str | None
    has_older: bool


@dataclass(frozen=True)
class CommandFeedbackEntryView:
    """One stored command and its current result, ready for compact display."""

    command_id: int
    action: str
    target: str
    status: CommandFeedbackStatus
    result: str | None
    queued_at: str | None = None


@dataclass(frozen=True)
class CommandHistoryView:
    """A bounded command-history page with current engine liveness for pending rows."""

    engine_up: bool
    entries: tuple[CommandFeedbackEntryView, ...]
    offset: int
    limit: int
    total_count: int
    has_more: bool
    status: CommandFeedbackStatus | None


@dataclass(frozen=True)
class TaskDetailView:
    """Ready-to-render task state, evidence and existing target metadata."""

    task_id: str
    title: str
    status: str
    status_reason: str | None
    mode: str | None
    step: str
    acting_role: str
    iteration: int
    holds: tuple[str, ...]
    wait_reason: str | None
    ownership: tuple[str, ...]
    request: str
    acceptance_criteria: tuple[str, ...]
    progress: TaskStripData
    latest_report: TaskReportView | None
    latest_verdict: TaskReportView | None
    branch: str | None
    worktree_path: str
    window_id: str | None
    sessions: tuple[TaskSessionView, ...]
    relationships: TaskRelationshipsView | None = None
    blocker: TaskBlockerView | None = None
    submitted_age: str = ""
    requeued_age: str | None = None
    usage: RecordedUsageView | None = None
    signal_observed: bool = False
    quota_observations: tuple[AgentUsageView, ...] = ()
    agent_usage: tuple[AgentUsageView, ...] = ()
    priority: int | None = None
    actual_stage: str | None = None
    actual_stage_started_at: datetime | None = None
    stage_ended_at: datetime | None = None
    milestones: tuple[TaskMilestoneView, ...] = ()
    latest_signal: str | None = None
    latest_signal_at: datetime | None = None
    max_iterations: int = 0
    started_at: datetime | None = None
    escalation_remaining: int | None = None
    verdict_counts: VerdictCounts | None = None
    last_words: str | None = None
    results: TaskResults | None = None
    decisions: TaskDecisions | None = None
    can_take_over: bool = False


@dataclass(frozen=True)
class TaskFamilyOutcomeView:
    """The exact recorded count of family tasks in one lifecycle outcome."""

    outcome: str
    count: int


@dataclass(frozen=True)
class TaskRelationshipsView:
    """Parentage, dependencies and recorded replacements kept as separate relationships."""

    family_parent: TaskReferenceView
    family_root: TaskReferenceView
    family_children: tuple[TaskReferenceView, ...]
    family_child_count: int
    dependencies: tuple[TaskReferenceView, ...]
    dependency_count: int
    blocking_dependencies: tuple[TaskReferenceView, ...]
    blocking_dependency_count: int
    dependents: tuple[TaskReferenceView, ...]
    dependent_count: int
    replacements: tuple[TaskReferenceView, ...]
    replacement_count: int
    family_outcomes: tuple[TaskFamilyOutcomeView, ...]
    page_offset: int
    page_size: int
    has_more: bool


@dataclass(frozen=True)
class TaskBlockerView:
    """Recorded blocker reason and evidence, with navigable related tasks."""

    kind: str
    lines: tuple[str, ...]
    related_tasks: tuple[TaskReferenceView, ...] = ()


@dataclass(frozen=True)
class EscalationPanelView:
    """One open escalation, including the exact packet rendered by its splash panel."""

    escalation_id: str
    task_id: str
    headline: str
    question: str
    options: tuple[str, ...]
    packet: str
    pulse_red: bool
    task_title: str = ""
    age: str = ""
    resolution_actions: tuple[str, ...] = ()
    answer_available: bool = False
    evidence_error: str | None = None
    reason: EscalationReason | None = None


@dataclass(frozen=True)
class AttentionGroupView:
    """One task's open decisions, diagnosed stall and recorded engine faults."""

    task_id: str | None
    title: str
    reasons: tuple[str, ...]
    escalations: tuple[EscalationPanelView, ...]


@dataclass(frozen=True)
class AttentionView:
    """Grouped inspection items and their decision, stall and fault counts."""

    groups: tuple[AttentionGroupView, ...]
    decisions: int
    stalls: int
    faults: int


@dataclass(frozen=True)
class DialogueBubbleView:
    """One event summary prepared for the dialogue widget."""

    role: Role | None
    text: str
    task_id: str
    age: str


@dataclass(frozen=True)
class QuotaGaugeView:
    """One provider-reported quota window with human-readable time context."""

    name: str
    used_pct: float
    resets_in: str
    observed_ago: str


@dataclass(frozen=True)
class AgentUsageView:
    """Token and priced-cost totals plus every quota window for one agent."""

    agent: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: float | None
    cost_basis: CostBasis | Literal["mixed"]
    quotas: tuple[QuotaGaugeView, ...]
    run_count: int = 0
    usage_reported_runs: int = 0
    legacy_token_runs: int = 0
    billed_cost_usd: float | None = None
    billed_cost_runs: int = 0
    api_equivalent_cost_usd: float | None = None
    api_equivalent_cost_runs: int = 0
    unknown_cost_runs: int = 0


@dataclass(frozen=True)
class ResourceView:
    """Host, Shikiru and agent resource measurements from one sample."""

    host_cpu_pct: float
    host_ram_used_pct: float
    free_ram_gb: float
    instance_cpu_pct: float | None
    instance_rss_gb: float | None
    sampled_at: datetime | None = None
    state: DiagnosticSampleState | None = None
    sampled_ago: str | None = None
    error: str | None = None
    host_ram_used_bytes: int | None = None
    host_ram_total_bytes: int | None = None
    shikiru_cpu_pct: float | None = None
    shikiru_rss_gb: float | None = None
    agents_cpu_pct: float | None = None
    agents_rss_gb: float | None = None
    shikiru_process_tree_empty: bool | None = None
    agents_process_tree_empty: bool | None = None


@dataclass(frozen=True)
class ContainerView:
    """One running container's CPU and memory as numbers."""

    name: str
    cpu_pct: float
    memory_bytes: int


@dataclass(frozen=True)
class ContainerProblemView:
    """One unhealthy, restarting or non-zero-exited container."""

    name: str
    reason: str
    exit_code: int | None = None


@dataclass(frozen=True)
class ElsewhereView:
    """Running containers outside the project; usage is `None` when unavailable."""

    running: int
    cpu_pct: float | None
    memory_bytes: int | None


@dataclass(frozen=True)
class DockerSummaryView:
    """Scoped container counts, usage totals, problems, top five by CPU and the rest."""

    running: int
    total: int
    cpu_pct: float | None
    memory_bytes: int | None
    problems: tuple[ContainerProblemView, ...]
    top: tuple[ContainerView, ...]
    elsewhere: ElsewhereView
    usage_available: bool


@dataclass(frozen=True)
class DockerView:
    """Docker sampling state, including stale or failed observations."""

    available: bool
    summary: DockerSummaryView | None = None
    state: DiagnosticSampleState | None = None
    sampled_ago: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class DiskView:
    """Free and total bytes of the project filesystem, the `.shikiru` size and sampling state."""

    state: DiagnosticSampleState
    free_bytes: int | None = None
    total_bytes: int | None = None
    shikiru_bytes: int | None = None
    free_level: UsageLevel | None = None
    sampled_ago: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class EngineFaultView:
    """One open recorded engine fault streak for Diagnostics."""

    scope: str
    task_id: str | None
    error: str
    recoverable: bool
    first_seen: str
    last_seen: str
    repeats: int


class DashboardViews(Protocol):
    """Ready-to-render data; widget plugins never receive state or configuration objects."""

    async def header(self) -> HeaderView:
        """Return the project, engine, slots and repository view."""
        ...

    def engine_up(self) -> bool:
        """Return current engine lock liveness for active-turn presentation."""
        ...

    def engine_status(self) -> EngineStatusView:
        """Return engine liveness with its recorded run duration."""
        ...

    def project_usage(self) -> RecordedUsageView:
        """Return token and monetary coverage for all retained session lineages."""
        ...

    def workspace_summary(self) -> WorkspaceSummaryView:
        """Return attention, running and waiting counts for home navigation."""
        ...

    def attention(self) -> AttentionView:
        """Return open decisions, diagnosed stalls and recorded engine faults."""
        ...

    def engine_faults(self) -> tuple[EngineFaultView, ...]:
        """Return recorded engine fault streaks that have not recovered."""
        ...

    def strips(self) -> list[TaskStripData]:
        """Return active task strips, with the focused task first."""
        ...

    def queue(self) -> list[QueueRow]:
        """Return queued tasks in dispatch order."""
        ...

    def task_browser(
        self,
        *,
        task_filter: TaskBrowserFilter,
        search: str,
        offset: int,
        limit: int,
        selected_task_id: str | None,
    ) -> TaskBrowserView:
        """Return one bounded browser page and preserve context for a selected task."""
        ...

    @property
    def recent_task_limit(self) -> int:
        """Return how many most recent terminal tasks Home Recent can page through."""
        ...

    @property
    def recent_task_window(self) -> int:
        """Return how many Recent tasks Home shows at once."""
        ...

    def task_browser_pages(
        self,
        *,
        task_filter: TaskBrowserFilter,
        search: str,
        limit: int,
        page_count: int,
        selected_task_id: str | None,
    ) -> tuple[TaskBrowserView, ...]:
        """Return contiguous bounded browser pages from one consistent read snapshot."""
        ...

    async def task_detail(
        self,
        task_id: str,
        *,
        relationship_offset: int = 0,
    ) -> TaskDetailView | None:
        """Return task state, recorded workspace evidence and and relationships."""
        ...

    async def task_timeline(
        self,
        task_id: str,
        *,
        cursor: str | None,
        role: str | None,
        turn: str | None,
        category: str,
    ) -> TaskTimelinePageView | None:
        """Return only a bounded timeline page for state and signal refreshes."""
        ...

    def escalations(self) -> list[EscalationPanelView]:
        """Return every open escalation and its question and options from the packet."""
        ...

    def dialogue(self, task_id: str | None = None) -> list[DialogueBubbleView]:
        """Return speech-worthy events for the project or one task, oldest first."""
        ...

    def usage(self, task_id: str | None = None) -> list[AgentUsageView]:
        """Return project usage or usage recorded for one task."""
        ...

    def command_history(
        self,
        *,
        expanded: bool = False,
        limit: int | None = None,
        offset: int = 0,
        status: CommandFeedbackStatus | None = None,
    ) -> CommandHistoryView:
        """Return a bounded recent or paginated project command history."""
        ...

    async def resources(self) -> ResourceView:
        """Return host usage and the engine/task-pane process-tree usage."""
        ...

    def resource_sample(self) -> ResourceView | None:
        """Return the latest timestamped resource observation without sampling again."""
        ...

    def on_resources_refreshed(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Call `callback` after every `resources()` call; return the unsubscribe function."""
        ...

    async def containers(self) -> DockerView:
        """Return the current Docker sample and whether Docker is available."""
        ...

    async def disk(self) -> DiskView:
        """Return the cached disk sample with its free-space level."""
        ...


class DashboardWidget(Protocol):
    """A system-pane panel plugin, registered under `WIDGET_ENTRY_POINT_GROUP` (06 §5.4)."""

    name: str

    def create(self, context: WidgetContext) -> Widget:
        """Build this widget from its views and update subscription."""
        ...


@dataclass(frozen=True)
class WidgetContext:
    """Data and update subscription supplied to a `DashboardWidget`."""

    views: DashboardViews
    focus_task: str | None
    resources_refresh_seconds: float
    docker_refresh_seconds: float
    disk_refresh_seconds: float
    timeline_refresh_seconds: float
    now_max_lines: int
    now_compact_lines: int
    quota_warn_pct: int
    quota_alert_pct: int
    clock: Clock
    on_change: OnChangeRegistration
    on_motion: MotionRegistration
    open_palette: OpenPalette
    open_palette_context: OpenPaletteContext | None = None
    open_resolution: OpenResolution | None = None
    open_task_detail: OpenTaskDetail | None = None
    open_task_detail_view: OpenTaskDetailView | None = None
    navigate_task_window: NavigateTaskWindow | None = None
    navigate_home: NavigateHome | None = None
    on_task_selected: SelectTaskContext | None = None
    activate_home_summary: Callable[[str], Awaitable[None]] | None = None
    placement: WidgetPlacement = WidgetPlacement.EXTENSIONS
    home_attention_present: bool = False
    open_contextual_view: OpenContextualView | None = None
    animations: bool = True
    activate_attention_decision: ActivateAttentionDecision | None = None
