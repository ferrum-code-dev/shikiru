"""Compose ready-to-render dashboard data from state, settings and services (06 §5.4)."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Protocol

from shikiru.dashboard.contract import (
    AgentUsageView,
    DialogueBubbleView,
    DockerView,
    EscalationPanelView,
    HeaderView,
    QueueRow,
    QuotaGaugeView,
    RepoStatus,
    ResourceView,
)
from shikiru.domain.events import EventKind
from shikiru.domain.scheduling import dispatch_sort_key, occupied_slot_ids
from shikiru.domain.tasks import ACTIVE_STATUSES, Role, Status
from shikiru.operator.strip_views import task_strip_data

if TYPE_CHECKING:
    from shikiru.clock import Clock
    from shikiru.config.settings import Settings
    from shikiru.operator.queries import OperatorQueries
    from shikiru.store.reader import StateReader
    from shikiru.ui.strip import TaskStripData

DIALOGUE_LINE_LIMIT = 20
SECONDS_PER_MINUTE = 60
MINUTES_PER_HOUR = 60
HOURS_PER_DAY = 24
TIME_UNKNOWN = "\N{EN DASH}"
PACKET_QUESTION_HEADING = "Question"
PACKET_OPTIONS_HEADING = "Options"

_DIALOGUE_KINDS = (
    EventKind.VERDICT,
    EventKind.TURN_COMPLETED,
    EventKind.HELPER_ACTION,
    EventKind.RUNG_CHANGED,
    EventKind.QUOTA_BLOCKED,
    EventKind.SCOPE_CHANGE,
    EventKind.TASK_DONE,
)


class DashboardSources(Protocol):
    """External information composed into dashboard view models."""

    async def repo_status(self) -> RepoStatus:
        """Return the project checkout's git status."""
        ...

    def engine_up(self) -> bool:
        """Return whether the engine holds its liveness lock."""
        ...

    async def resources(self) -> ResourceView:
        """Return host and Shikiru process-tree resource readings."""
        ...

    async def containers(self) -> DockerView:
        """Return the current Docker sample."""
        ...


class StateDashboardViews:
    """Build widget models from the current state and its injected data sources."""

    def __init__(
        self,
        state: StateReader,
        *,
        focus_task: str | None,
        settings: Settings,
        sources: DashboardSources,
        queries: OperatorQueries,
        clock: Clock,
    ) -> None:
        """Use `state`, shared operator queries, sources and `clock` for widget data."""
        self._state = state
        self._focus_task = focus_task
        self._settings = settings
        self._sources = sources
        self._queries = queries
        self._clock = clock

    async def header(self) -> HeaderView:
        """Build the project, engine, slot and repository summary."""
        tasks = self._state.list_tasks()
        return HeaderView(
            project=self._settings.project.name,
            engine_up=self._sources.engine_up(),
            occupied_slots=len(occupied_slot_ids(tasks)),
            maximum_slots=self._settings.orchestration.max_parallel,
            repo=await self._sources.repo_status(),
        )

    def strips(self) -> list[TaskStripData]:
        """Build the active task strips, placing the focused task first."""
        tasks = self._state.list_tasks(statuses=ACTIVE_STATUSES)
        tasks.sort(key=lambda task: task.id != self._focus_task)
        return [task_strip_data(task, self._settings) for task in tasks]

    def queue(self) -> list[QueueRow]:
        """Build queued-task rows in dispatch order."""
        tasks = self._state.list_tasks(statuses=(Status.QUEUED,))
        tasks.sort(key=dispatch_sort_key)
        return [
            QueueRow(
                task_id=task.id,
                title=task.title,
                priority=task.priority,
                dependencies=tuple(task.depends_on),
                wait_reason=task.wait_reason,
            )
            for task in tasks
        ]

    def escalations(self) -> list[EscalationPanelView]:
        """Build splash panels from each stored packet's headline, question and options."""
        return [
            EscalationPanelView(
                escalation_id=item.escalation.id,
                task_id=item.escalation.task_id,
                headline=_packet_headline(item.packet),
                question=_packet_section(item.packet, PACKET_QUESTION_HEADING, allow_empty=True),
                options=_packet_options(item.packet),
                packet=item.packet,
            )
            for item in self._queries.list_escalations()
        ]

    def dialogue(self) -> list[DialogueBubbleView]:
        """Build the latest twenty speech-worthy events, oldest first."""
        now = self._clock.now()
        return [
            DialogueBubbleView(
                role=_event_role(event.kind, event.data),
                text=_event_text(event.kind, event.message, event.data),
                task_id=event.task_id or "",
                age=_age(now - event.ts),
            )
            for event in self._state.latest_events(
                DIALOGUE_LINE_LIMIT,
                kinds=_DIALOGUE_KINDS,
                implementer_turns_only=True,
            )
            if event.task_id is not None
        ]

    def usage(self) -> list[AgentUsageView]:
        """Build per-agent usage and reported quota windows for every known agent."""
        now = self._clock.now()
        usage, quotas = self._queries.usage_and_quotas()
        by_agent = {item.agent: item for item in usage}
        agents = sorted(set(by_agent) | set(quotas))
        result: list[AgentUsageView] = []
        for agent in agents:
            item = by_agent.get(agent)
            windows = tuple(
                QuotaGaugeView(
                    name=window.name,
                    used_pct=window.used_pct,
                    resets_in=(
                        _relative(window.resets_at - now)
                        if window.resets_at is not None
                        else TIME_UNKNOWN
                    ),
                    observed_ago=_age(now - window.observed_at),
                )
                for window in quotas.get(agent, [])
            )
            result.append(
                AgentUsageView(
                    agent=agent,
                    input_tokens=item.input_tokens if item is not None else 0,
                    output_tokens=item.output_tokens if item is not None else 0,
                    cache_read_tokens=item.cache_read_tokens if item is not None else 0,
                    cache_write_tokens=item.cache_write_tokens if item is not None else 0,
                    cost_usd=item.cost_usd if item is not None else None,
                    cost_basis=item.cost_basis if item is not None else "unknown",
                    quotas=windows,
                )
            )
        return result

    async def resources(self) -> ResourceView:
        """Return the current host and engine/task-pane process-tree sample."""
        return await self._sources.resources()

    async def containers(self) -> DockerView:
        """Return the current Docker sample."""
        return await self._sources.containers()


def _packet_headline(packet: str) -> str:
    lines = packet.splitlines()
    if not lines or not lines[0].startswith("# "):
        message = "escalation packet has no headline"
        raise ValueError(message)
    return lines[0].removeprefix("# ")


def _packet_section(packet: str, heading: str, *, allow_empty: bool = False) -> str:
    lines = packet.splitlines()
    marker = f"## {heading}"
    try:
        start = lines.index(marker) + 1
    except ValueError as error:
        message = f"escalation packet has no {heading!r} section"
        raise ValueError(message) from error
    end = (
        lines.index(f"## {PACKET_OPTIONS_HEADING}", start)
        if heading == PACKET_QUESTION_HEADING
        else len(lines)
    )
    content = "\n".join(lines[start:end]).strip()
    if not content and not allow_empty:
        message = f"escalation packet has an empty {heading!r} section"
        raise ValueError(message)
    return content


def _packet_options(packet: str) -> tuple[str, ...]:
    section = _packet_section(packet, PACKET_OPTIONS_HEADING)
    options = tuple(
        line.removeprefix("- ") for line in section.splitlines() if line.startswith("- ")
    )
    if not options:
        message = "escalation packet has no options"
        raise ValueError(message)
    return options


def _event_role(kind: EventKind, data: dict[str, object]) -> Role | None:
    if kind is EventKind.VERDICT:
        return Role.LEADER
    if kind is EventKind.HELPER_ACTION:
        return Role.HELPER
    if kind is EventKind.TURN_COMPLETED:
        return Role(str(data["role"]))
    return None


def _event_text(kind: EventKind, message: str, data: dict[str, object]) -> str:
    if kind is not EventKind.TURN_COMPLETED:
        return message
    summary = str(data["summary"])
    question = data.get("question")
    return f"{summary}\n{question}" if isinstance(question, str) and question else summary


def _age(duration: timedelta) -> str:
    seconds = max(int(duration.total_seconds()), 0)
    if seconds < SECONDS_PER_MINUTE:
        return "now"
    minutes = seconds // SECONDS_PER_MINUTE
    if minutes < MINUTES_PER_HOUR:
        return f"{minutes}m"
    hours = minutes // MINUTES_PER_HOUR
    if hours < HOURS_PER_DAY:
        return f"{hours}h"
    return f"{hours // HOURS_PER_DAY}d"


def _relative(duration: timedelta) -> str:
    seconds = int(duration.total_seconds())
    if abs(seconds) < SECONDS_PER_MINUTE:
        return "now"
    label = _age(timedelta(seconds=abs(seconds)))
    return f"in {label}" if seconds > 0 else f"{label} ago"
