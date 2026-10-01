"""Stable view models and plugin protocols for dashboard widgets (06 §5.4)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from textual.widget import Widget

    from shikiru.agents.contracts import CostBasis
    from shikiru.domain.tasks import Role
    from shikiru.ui.strip import TaskStripData

WIDGET_ENTRY_POINT_GROUP = "shikiru.widgets"
OnChange = Callable[[], Awaitable[None]]
OnChangeRegistration = Callable[[OnChange], Awaitable[None]]
MotionRegistration = Callable[[OnChange], None]
OpenPalette = Callable[[str], Awaitable[None]]


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

    def escalations(self) -> list[EscalationPanelView]:
        """Return every open escalation and its question and options from the packet."""
        ...

    def dialogue(self) -> list[DialogueBubbleView]:
        """Return the latest speech-worthy events, oldest first."""
        ...

    def usage(self) -> list[AgentUsageView]:
        """Return per-agent usage and only quota windows reported by that agent."""
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
