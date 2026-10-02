"""Stable view models and plugin protocols for dashboard widgets (06 §5.4)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from textual.widget import Widget

    from shikiru.agents.contracts import CostBasis
    from shikiru.domain.tasks import Role
    from shikiru.ui.strip import TaskStripData

WIDGET_ENTRY_POINT_GROUP = "shikiru.widgets"
TASK_STATUS_QUEUED = "queued"
TASK_TERMINAL_STATUSES = frozenset({"done", "cancelled"})
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


@dataclass(frozen=True)
class RepoStatus:
    """The project checkout's state, as shown by the `header` widget (06 §5.4)."""

    branch: str
    ahead: int
    behind: int
    dirty: int


@dataclass(frozen=True)
class HeaderView:
    """Project and engine summary rendered by the header widget."""

    project: str
    engine_up: bool
    occupied_slots: int
    maximum_slots: int
    repo: RepoStatus
    alerts: tuple[str, ...]


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


@dataclass(frozen=True)
class TaskSignalView:
    """A bounded set of recorded signal lines for one agent role."""

    role: str
    lines: tuple[str, ...]


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


@dataclass(frozen=True)
class ResourceView:
    """Host and Shikiru process-tree resource measurements."""

    host_cpu_pct: float
    host_ram_used_pct: float
    free_ram_gb: float
    instance_cpu_pct: float | None
    instance_rss_gb: float | None


@dataclass(frozen=True)
class ContainerView:
    """One Docker container's current CPU and memory readings."""

    name: str
    cpu_pct: str
    memory: str


@dataclass(frozen=True)
class DockerView:
    """Docker sampling state; unavailable Docker is hidden by its widget."""

    available: bool
    containers: tuple[ContainerView, ...]


class DashboardViews(Protocol):
    """Ready-to-render data; widget plugins never receive state or configuration objects."""

    async def header(self) -> HeaderView:
        """Return the project, engine, slots and repository view."""
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

    async def task_detail(self, task_id: str) -> TaskDetailView | None:
        """Return task state and its recorded report, signal and workspace evidence."""
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
    on_change: OnChangeRegistration
    on_motion: MotionRegistration
    open_palette: OpenPalette
    open_palette_context: OpenPaletteContext | None = None
    open_task_detail: OpenTaskDetail | None = None
    navigate_task_window: NavigateTaskWindow | None = None
    navigate_role_pane: NavigateRolePane | None = None
    navigate_home: NavigateHome | None = None
    on_task_selected: SelectTaskContext | None = None
