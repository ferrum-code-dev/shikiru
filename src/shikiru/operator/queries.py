"""Read-only operator views over the state reader (06 §1)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from shikiru.agents.contracts import QuotaWindow
from shikiru.domain.events import Event
from shikiru.domain.runs import Run
from shikiru.domain.scheduling import dispatch_sort_key, occupied_slot_ids
from shikiru.domain.tasks import ACTIVE_STATUSES, Role, Status, Task
from shikiru.errors import ShikiruError
from shikiru.paths import turns_dir
from shikiru.store.escalations import Escalation
from shikiru.workspace.reports import load_turn_report

if TYPE_CHECKING:
    from shikiru.store.reader import StateReader
    from shikiru.tmux.client import Tmux

PEEK_TAIL_LINES = 60


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


class TaskDetail(OperatorView):
    """A task with its runs and event timeline."""

    task: Task
    events: list[Event] = Field(default_factory=list)
    reports: list[dict[str, object]] = Field(default_factory=list)
    branches: list[str] = Field(default_factory=list)
    sessions: list[Run] = Field(default_factory=list)


class EscalationView(OperatorView):
    """An escalation row and its packet text."""

    escalation: Escalation
    packet: str


class PeekView(OperatorView):
    """Visible tail text captured from one or more task agent panes."""

    task_id: str
    panes: dict[str, str]


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
    cost_basis: str


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
        tmux: Tmux,
        max_parallel: int,
    ) -> None:
        """Use `reader`, `engine_up` and `tmux` for the operator's read boundaries."""
        self._reader = reader
        self._project = project
        self._project_root = project_root
        self._engine_up = engine_up
        self._tmux = tmux
        self._max_parallel = max_parallel

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
        runs = self._reader.list_all_runs()
        usage = _aggregate_usage(runs)
        agents = sorted({run.agent for run in runs} | set(self._reader.list_quota_agents()))
        task_detail = self.get_task(task_id) if task_id is not None else None
        return StatusView(
            project=self._project,
            engine_up=self._engine_up(),
            slots=SlotView(active=len(occupied_slot_ids(tasks)), maximum=self._max_parallel),
            active_tasks=active,
            queued_tasks=queued,
            escalations=open_escalations,
            usage=usage,
            quotas=self._reader.list_quotas(agents),
            task=task_detail,
        )

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

    async def peek_session(self, task_id: str, role: Role | None = None) -> PeekView | None:
        """Capture the visible tail of one role pane or every assigned agent pane."""
        task = self._reader.get_task(task_id)
        if task is None:
            return None
        roles = [role] if role is not None else list(task.panes)
        if not roles:
            message = f"{task_id} has no agent panes"
            raise OperatorQueryError(message)
        panes: dict[str, str] = {}
        for pane_role in roles:
            pane_id = task.panes.get(pane_role)
            if pane_id is None:
                message = f"{task_id} has no {pane_role.value} pane"
                raise OperatorQueryError(message)
            panes[pane_role.value] = await self._tmux.capture_pane(
                pane_id, tail_lines=PEEK_TAIL_LINES
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

    def _reports(self, task_id: str) -> list[dict[str, object]]:
        task_turns_dir = turns_dir(self._project_root, task_id)
        if not task_turns_dir.is_dir():
            return []
        reports: list[dict[str, object]] = []
        for turn_dir in sorted(path for path in task_turns_dir.iterdir() if path.is_dir()):
            loaded = load_turn_report(turn_dir)
            if loaded is None:
                continue
            reports.append(
                {
                    "turn": turn_dir.name,
                    "kind": loaded.kind.value,
                    "data": loaded.report.model_dump(mode="json"),
                }
            )
        return reports


def _aggregate_usage(runs: Sequence[Run]) -> list[UsageView]:
    by_agent: dict[str, list[Run]] = {}
    for run in runs:
        by_agent.setdefault(run.agent, []).append(run)
    views: list[UsageView] = []
    for agent in sorted(by_agent):
        agent_runs = by_agent[agent]
        costs = [run.usage.cost_usd for run in agent_runs if run.usage.cost_usd is not None]
        cost_bases = {run.usage.cost_basis for run in agent_runs if run.usage.cost_usd is not None}
        cost_basis = next(iter(cost_bases), "unknown") if len(cost_bases) <= 1 else "mixed"
        views.append(
            UsageView(
                agent=agent,
                input_tokens=sum(run.usage.input_tokens for run in agent_runs),
                output_tokens=sum(run.usage.output_tokens for run in agent_runs),
                cache_read_tokens=sum(run.usage.cache_read_tokens for run in agent_runs),
                cache_write_tokens=sum(run.usage.cache_write_tokens for run in agent_runs),
                cost_usd=sum(costs) if costs else None,
                cost_basis=cost_basis,
            )
        )
    return views
