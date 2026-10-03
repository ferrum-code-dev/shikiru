"""Stable view models and plugin protocols for dashboard widgets (06 §5.4)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from datetime import datetime

    from textual.widget import Widget

    from shikiru.agents.contracts import CostBasis
    from shikiru.clock import Clock
    from shikiru.domain.tasks import Role
    from shikiru.ui.strip import TaskStripData

from shikiru.domain.timeline import (
    TIMELINE_ACTIVITY_CATEGORY,
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
TASK_STATUS_QUEUED = "queued"
TASK_TERMINAL_STATUSES = frozenset({"done", "cancelled"})


class TaskTimelineCategory(StrEnum):
    """A timeline category exposed by the dashboard contract."""

    ACTIVITY = TIMELINE_ACTIVITY_CATEGORY
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


DEFAULT_TASK_TIMELINE_CATEGORY = TaskTimelineCategory.ACTIVITY
DEFAULT_TASK_TIMELINE_REFRESH_SECONDS = 1
OnChange = Callable[[], Awaitable[None]]
OnChangeRegistration = Callable[[OnChange], Awaitable[None]]
MotionRegistration = Callable[[OnChange], None]


@dataclass(frozen=True)
class PaletteRequest:
    """Optional task, role and action context for the shared operator palette."""

    action: str | None = None
    task_id: str | None = None
    role: str | None = None


OpenPalette = Callable[[str], Awaitable[None]]
OpenPaletteContext = Callable[[PaletteRequest], Awaitable[None]]
SelectTaskContext = Callable[[str | None], None]
OpenTaskDetail = Callable[[str], Awaitable[None]]
NavigateTaskWindow = Callable[[str], Awaitable[str]]
NavigateRolePane = Callable[[str, str], Awaitable[str]]
NavigateHome = Callable[[], Awaitable[str]]


class TaskBrowserFilter(StrEnum):
    """A task browser category with membership and order defined by Workspace §5.1."""

    ACTIVE = "active"
    WAITING = "waiting"
    HISTORY = "history"
    ALL = "all"


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


@dataclass(frozen=True)
class RepoStatus:
    """The project checkout's state, as shown by the `header` widget (06 §5.4)."""

    branch: str
    ahead: int
    behind: int
    dirty: int
    sampled_ago: str = ""


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


@dataclass(frozen=True)
class WorkspaceSummaryView:
    """Task and attention counts for the home summary controls."""

    attention_groups: int
    decisions: int
    stalls: int
    faults: int
    running: int
    waiting: int

    @property
    def attention(self) -> int:
        """Count grouped attention rows, which may overlap waiting tasks."""
        return self.attention_groups


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


@dataclass(frozen=True)
class TaskBrowserContextRow:
    """An ancestor shown as family context outside the matching task count."""

    task_id: str
    title: str
    status: str
    mode: str | None


@dataclass(frozen=True)
class TaskBrowserView:
    """A bounded page of matching tasks and the selected task's retained context."""

    filter: TaskBrowserFilter
    search: str
    rows: tuple[TaskBrowserRow, ...]
    selected_task: TaskBrowserRow | None
    selected_task_missing: bool
    selected_task_matches: bool
    matching_count: int
    filtered_count: int
    project_count: int
    has_more: bool
    ancestor_context: tuple[TaskBrowserContextRow, ...] = ()


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
class TaskEventView:
    """One recent durable task event for the detail evidence panel."""

    timestamp: str
    kind: str
    message: str


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


@dataclass(frozen=True)
class TaskSignalView:
    """A bounded set of recorded signal lines for one agent role."""

    role: str
    lines: tuple[str, ...]


@dataclass(frozen=True)
class TaskTimelineEntryView:
    """One recorded engine event or normalized agent signal with stable identity."""

    identity: str
    timestamp: str | None
    timestamp_error: str | None
    source: str
    category: str
    summary: str
    detail: str
    role: str | None
    turn: str | None
    event_id: int | None
    incomplete: bool


@dataclass(frozen=True)
class TaskTimelinePageView:
    """A bounded newest-first task evidence page with an older-page cursor."""

    entries: tuple[TaskTimelineEntryView, ...]
    older_cursor: str | None
    has_older: bool


@dataclass(frozen=True)
class GlobalAttentionView:
    """Compact project-wide counts displayed in task detail."""

    decisions: int
    faults: int


@dataclass(frozen=True)
class CommandFeedbackEntryView:
    """One stored command and its current result, ready for compact display."""

    command_id: int
    action: str
    target: str
    status: CommandFeedbackStatus
    result: str | None


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
class TaskRolePaneView:
    """A task role and its recorded tmux pane target, if any."""

    role: str
    recorded_target: str | None


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
    events: tuple[TaskEventView, ...]
    signals: tuple[TaskSignalView, ...]
    recorded_facts: str | None
    facts_turn: str | None
    evidence_errors: tuple[str, ...]
    branch: str | None
    worktree_path: str
    window_id: str | None
    role_panes: tuple[TaskRolePaneView, ...]
    sessions: tuple[TaskSessionView, ...]
    attention: GlobalAttentionView
    relationships: TaskRelationshipsView | None = None
    blocker: TaskBlockerView | None = None
    submitted_age: str = ""
    requeued_age: str | None = None
    timeline: TaskTimelinePageView | None = None


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
    evidence_error: str | None = None


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
    legacy_unknown_token_runs: int = 0
    billed_cost_usd: float | None = None
    billed_cost_runs: int = 0
    legacy_billed_cost_runs: int = 0
    api_equivalent_cost_usd: float | None = None
    api_equivalent_cost_runs: int = 0
    legacy_api_equivalent_cost_runs: int = 0
    unknown_cost_runs: int = 0


@dataclass(frozen=True)
class ResourceView:
    """Host and Shikiru process-tree resource measurements."""

    host_cpu_pct: float
    host_ram_used_pct: float
    free_ram_gb: float
    instance_cpu_pct: float | None
    instance_rss_gb: float | None
    sampled_at: datetime | None = None
    state: DiagnosticSampleState | None = None
    sampled_ago: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class ContainerView:
    """One Docker container's current CPU and memory readings."""

    name: str
    cpu_pct: str
    memory: str


@dataclass(frozen=True)
class DockerView:
    """Docker sampling state, including stale or failed observations."""

    available: bool
    containers: tuple[ContainerView, ...]
    state: DiagnosticSampleState | None = None
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
        timeline_cursor: str | None = None,
        timeline_role: str | None = None,
        timeline_turn: str | None = None,
        timeline_category: str = DEFAULT_TASK_TIMELINE_CATEGORY,
    ) -> TaskDetailView | None:
        """Return task state, recorded workspace evidence and one bounded timeline page."""
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

    def global_attention(self) -> GlobalAttentionView:
        """Return the compact global decision and engine-fault counts."""
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

    async def containers(self) -> DockerView:
        """Return the current Docker sample and whether Docker is available."""
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
    timeline_refresh_seconds: float
    clock: Clock
    on_change: OnChangeRegistration
    on_motion: MotionRegistration
    open_palette: OpenPalette
    open_palette_context: OpenPaletteContext | None = None
    open_task_detail: OpenTaskDetail | None = None
    navigate_task_window: NavigateTaskWindow | None = None
    navigate_role_pane: NavigateRolePane | None = None
    navigate_home: NavigateHome | None = None
    on_task_selected: SelectTaskContext | None = None
    activate_home_summary: Callable[[str], Awaitable[None]] | None = None
