"""Compose ready-to-render dashboard data from state, settings and services (06 §5.4)."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Protocol

from shikiru.agents.contracts import FailureKind
from shikiru.dashboard.contract import (
    DEFAULT_TASK_TIMELINE_CATEGORY,
    AgentUsageView,
    AttentionGroupView,
    AttentionView,
    CommandFeedbackEntryView,
    CommandFeedbackStatus,
    CommandHistoryView,
    DialogueBubbleView,
    DockerView,
    EngineFaultView,
    EscalationPanelView,
    GlobalAttentionView,
    HeaderView,
    QueueRow,
    QuotaGaugeView,
    RepoStatus,
    ResourceView,
    TaskBlockerView,
    TaskBrowserContextRow,
    TaskBrowserFilter,
    TaskBrowserRow,
    TaskBrowserView,
    TaskDetailView,
    TaskEventView,
    TaskFamilyOutcomeView,
    TaskReferenceView,
    TaskRelationshipsView,
    TaskReportView,
    TaskRolePaneView,
    TaskSessionView,
    TaskSignalView,
    TaskTestView,
    TaskTimelineEntryView,
    TaskTimelinePageView,
    TaskTimelineSource,
    WorkspaceSummaryView,
)
from shikiru.domain.commands import CommandStatus, ResolutionKind
from shikiru.domain.escalation_evidence import parse_escalation_packet
from shikiru.domain.events import EventKind
from shikiru.domain.ladder import pick_rung
from shikiru.domain.motion import MotionStampKind, QuotaMotionState
from shikiru.domain.reports import (
    ConsultAnswer,
    ImplementationReport,
    PlanReport,
    TriageReport,
    Verdict,
)
from shikiru.domain.runs import RunStatus, has_positive_token_evidence
from shikiru.domain.scheduling import WaitReason, dispatch_sort_key, occupied_slot_ids
from shikiru.domain.steps import ACTING_ROLE, Step, task_acting_role
from shikiru.domain.tasks import ACTIVE_STATUSES, Hold, Role, Status, Task
from shikiru.domain.timeline import TIMELINE_EVIDENCE_CATEGORY
from shikiru.errors import ShikiruError
from shikiru.operator.queries import AGENT_REPORT_SOURCE
from shikiru.operator.strip_views import task_strip_data
from shikiru.ui.age import format_age
from shikiru.ui.glyphs import GLYPH_STALLED, SPINNER_FRAMES
from shikiru.ui.strip import PanelState, StampKind, StripStamp, TaskStripData

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime, timedelta

    from shikiru.agents.contracts import QuotaWindow
    from shikiru.clock import Clock
    from shikiru.config.settings import Settings
    from shikiru.dashboard.motion import DashboardMotion
    from shikiru.domain.events import Event
    from shikiru.domain.faults import FaultStreak
    from shikiru.domain.motion import TaskMotionFrame
    from shikiru.domain.runs import Run
    from shikiru.domain.tasks import Task
    from shikiru.operator.queries import EscalationView, OperatorQueries, ReportRow, UsageView
    from shikiru.store.reader import StateReader
    from shikiru.store.tasks import TaskBrowserPage, TaskRelationships

DIALOGUE_LINE_LIMIT = 20
ALERT_TIME_FORMAT = "%H:%M:%S"
SECONDS_PER_MINUTE = 60
MINUTES_PER_HOUR = 60
HOURS_PER_DAY = 24
TIME_UNKNOWN = "\N{EN DASH}"
TASK_TIMELINE_READ_ERROR_ID = "timeline-read-error"
TASK_TIMELINE_READ_ERROR_SUMMARY = "Task evidence could not be read"
TASK_TIMELINE_READ_ERROR_TIMESTAMP = (
    "recorded timestamp is unavailable because the page could not be read"
)
AGENT_REPORTED_TESTS = "agent-reported"
TASK_ROLES = (Role.LEADER, Role.IMPLEMENTER)
SETUP_STEP = "setup"
UNSTARTED_STEP = "not started"
CONSULT_STEP = "consult"
INTEGRATION_STEP = "integration"
REWORK_STEP = "rework"
_ACTIVITY_KINDS = (
    EventKind.VERDICT,
    EventKind.TURN_COMPLETED,
    EventKind.TURN_FAILED,
    EventKind.TURN_STOPPED,
    EventKind.HELPER_ACTION,
    EventKind.RUNG_CHANGED,
    EventKind.QUOTA_BLOCKED,
    EventKind.QUOTA_CLEARED,
    EventKind.SCOPE_CHANGE,
    EventKind.HOLD_SET,
    EventKind.HOLD_CLEARED,
    EventKind.TASK_RETRIED,
    EventKind.INTEGRATION_WAITING,
    EventKind.TASK_DONE,
    EventKind.ENGINE_FAULT,
    EventKind.ENGINE_RECOVERED,
)
_ACTIVE_TURN_STATUSES = (Status.RUNNING, Status.INTEGRATING)
_RESET_AT_FIELD = "resets_at"
_QUOTA_TURN_LIFECYCLE = (
    EventKind.STEP_STARTED,
    EventKind.TURN_STARTED,
    EventKind.TURN_COMPLETED,
    EventKind.TURN_FAILED,
    EventKind.TURN_STOPPED,
    EventKind.RUNG_CHANGED,
)
StructuredEscalationEvidence = tuple[str, dict[ResolutionKind, str], tuple[str, ...]]


@dataclass(frozen=True)
class _StructuredEscalationCache:
    packet: str | None
    packet_error: str | None
    evidence: StructuredEscalationEvidence


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

    def resource_sample(self) -> ResourceView | None:
        """Return the latest dashboard resource sample without taking a new one."""
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
        motion: DashboardMotion,
    ) -> None:
        """Use `state`, shared operator queries, sources and `clock` for widget data."""
        self._state = state
        self._focus_task = focus_task
        self._settings = settings
        self._sources = sources
        self._queries = queries
        self._clock = clock
        self._motion = motion
        self._usage_version = -1
        self._project_usage_cache: tuple[list[UsageView], dict[str, list[QuotaWindow]]] | None = (
            None
        )
        self._task_usage_cache: dict[str, list[UsageView]] = {}
        self._escalation_version = -1
        self._escalation_cache: list[EscalationView] | None = None
        self._structured_escalation_cache: dict[str, _StructuredEscalationCache] = {}
        self._fault_version = -1
        self._fault_cache: tuple[FaultStreak, ...] | None = None

    async def header(self) -> HeaderView:
        """Build the project, engine, slot and repository summary."""
        tasks = self._state.list_tasks()
        return HeaderView(
            project=self._settings.project.name,
            engine_up=self._sources.engine_up(),
            occupied_slots=len(occupied_slot_ids(tasks)),
            maximum_slots=self._settings.orchestration.max_parallel,
            repo=await self._sources.repo_status(),
            alerts=self._alerts(),
            integration_mode=self._settings.orchestration.integration,
        )

    def workspace_summary(self) -> WorkspaceSummaryView:
        """Count home categories without treating them as disjoint task totals."""
        attention = self.attention()
        tasks = self._state.list_tasks()
        now = self._clock.now()
        quota_wait_ids = {
            task.id for task in tasks if self._running_quota_evidence(task, now) is not None
        }
        running = sum(
            task.status in _ACTIVE_TURN_STATUSES
            and not task.held
            and task.wait_reason is None
            and task.id not in quota_wait_ids
            for task in tasks
        )
        waiting = sum(
            task.status not in (Status.DONE, Status.CANCELLED)
            and (
                task.status in (Status.QUEUED, Status.ESCALATED)
                or task.held
                or task.wait_reason is not None
                or task.id in quota_wait_ids
            )
            for task in tasks
        )
        return WorkspaceSummaryView(
            attention_groups=len(attention.groups),
            decisions=attention.decisions,
            stalls=attention.stalls,
            faults=attention.faults,
            running=running,
            waiting=waiting,
        )

    def _alerts(self) -> tuple[str, ...]:
        lines = [
            f"{GLYPH_STALLED} {fault.scope}{f' {fault.task_id}' if fault.task_id else ''}: "
            f"{fault.error} x{fault.repeat} since {fault.first_seen.strftime(ALERT_TIME_FORMAT)}"
            for fault in self._cached_faults()
        ]
        age = self._queries.oldest_escalation_age_seconds()
        if age is not None:
            lines.append(f"oldest escalation {format_age(age)}")
        return tuple(lines)

    def _cached_faults(self) -> tuple[FaultStreak, ...]:
        version = self._state.data_version()
        if version != self._fault_version:
            self._fault_version = version
            self._fault_cache = tuple(self._queries.open_faults())
        faults = self._fault_cache
        if faults is None:
            message = "fault cache was not initialized"
            raise RuntimeError(message)
        return faults

    def strips(self) -> list[TaskStripData]:
        """Build the active task strips, placing the focused task first."""
        now = self._clock.now()
        tasks = self._state.list_tasks(statuses=ACTIVE_STATUSES)
        tasks.sort(key=lambda task: task.id != self._focus_task)
        recent_merges = self._motion.recent_merges(now)
        task_ids = {task.id for task in tasks}
        all_tasks = [*tasks, *(task for task in recent_merges if task.id not in task_ids)]
        rendered = self._motion.frames(tuple(all_tasks), now)
        frames = {item.task_id: item for item in rendered.tasks}
        return [
            self._strip_with_motion(task, frames[task.id], self._motion.current_cost(task))
            for task in all_tasks
        ]

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

    def task_browser(
        self,
        *,
        task_filter: TaskBrowserFilter,
        search: str,
        offset: int,
        limit: int,
        selected_task_id: str | None,
    ) -> TaskBrowserView:
        """Build one ready-to-render browser page and retain the selected task context."""
        now = self._clock.now()
        motion_task_ids, stalled_ids, diagnosed_stall_task_ids = self._task_browser_motion(
            task_filter,
            now,
        )
        quota_wait_task_ids = (
            self._quota_wait_task_ids(now) if task_filter is TaskBrowserFilter.WAITING else ()
        )
        page = self._state.task_browser_page(
            category=task_filter.value,
            search=search,
            limit=limit,
            offset=offset,
            selected_task_id=selected_task_id,
            diagnosed_stall_task_ids=diagnosed_stall_task_ids,
            quota_wait_task_ids=quota_wait_task_ids,
        )
        selected_task = self._selected_browser_task((page,), selected_task_id)
        return self._task_browser_view(
            page,
            task_filter=task_filter,
            search=search,
            offset=offset,
            selected_task_id=selected_task_id,
            selected_task=selected_task,
            now=now,
            motion_task_ids=motion_task_ids,
            stalled_ids=stalled_ids,
        )

    def task_browser_pages(
        self,
        *,
        task_filter: TaskBrowserFilter,
        search: str,
        limit: int,
        page_count: int,
        selected_task_id: str | None,
    ) -> tuple[TaskBrowserView, ...]:
        """Build contiguous bounded browser pages from one state snapshot."""
        now = self._clock.now()
        motion_task_ids, stalled_ids, diagnosed_stall_task_ids = self._task_browser_motion(
            task_filter,
            now,
        )
        quota_wait_task_ids = (
            self._quota_wait_task_ids(now) if task_filter is TaskBrowserFilter.WAITING else ()
        )
        pages = self._state.task_browser_pages(
            category=task_filter.value,
            search=search,
            limit=limit,
            page_count=page_count,
            selected_task_id=selected_task_id,
            diagnosed_stall_task_ids=diagnosed_stall_task_ids,
            quota_wait_task_ids=quota_wait_task_ids,
        )
        selected_task = self._selected_browser_task(pages, selected_task_id)
        return tuple(
            self._task_browser_view(
                page,
                task_filter=task_filter,
                search=search,
                offset=page_index * limit,
                selected_task_id=selected_task_id,
                selected_task=selected_task,
                now=now,
                motion_task_ids=motion_task_ids,
                stalled_ids=stalled_ids,
            )
            for page_index, page in enumerate(pages)
        )

    def _task_browser_motion(
        self,
        task_filter: TaskBrowserFilter,
        now: datetime,
    ) -> tuple[set[str], set[str], tuple[str, ...]]:
        motion_task_ids: set[str] = set()
        stalled_ids: set[str] = set()
        if task_filter is TaskBrowserFilter.ACTIVE:
            current_turn_tasks = self._state.list_tasks(statuses=_ACTIVE_TURN_STATUSES)
            motion_task_ids.update(task.id for task in current_turn_tasks)
            frames = self._motion.frames(tuple(current_turn_tasks), now)
            stalled_ids.update(item.task_id for item in frames.tasks if item.stalled)
        return motion_task_ids, stalled_ids, tuple(sorted(stalled_ids))

    def _quota_wait_task_ids(self, now: datetime) -> tuple[str, ...]:
        return tuple(
            task.id
            for task in self._state.list_tasks(statuses=_ACTIVE_TURN_STATUSES)
            if self._running_quota_evidence(task, now) is not None
        )

    def _selected_browser_task(
        self,
        pages: Sequence[TaskBrowserPage],
        selected_task_id: str | None,
    ) -> Task | None:
        if selected_task_id is None:
            return None
        selected_task = next(
            (task for page in pages for task in page.tasks if task.id == selected_task_id),
            None,
        )
        if selected_task is None:
            selected_task = self._state.get_task(selected_task_id)
        return selected_task

    def _task_browser_view(
        self,
        page: TaskBrowserPage,
        *,
        task_filter: TaskBrowserFilter,
        search: str,
        offset: int,
        selected_task_id: str | None,
        selected_task: Task | None,
        now: datetime,
        motion_task_ids: set[str],
        stalled_ids: set[str],
    ) -> TaskBrowserView:
        page_stalled_ids = set(stalled_ids)

        framed_tasks = [
            task
            for task in page.tasks
            if task.status in ACTIVE_STATUSES and task.id not in motion_task_ids
        ]
        if (
            selected_task is not None
            and selected_task.status in ACTIVE_STATUSES
            and selected_task.id not in motion_task_ids
            and selected_task.id not in {task.id for task in framed_tasks}
        ):
            framed_tasks.append(selected_task)
        if framed_tasks:
            frames = self._motion.frames(tuple(framed_tasks), now)
            page_stalled_ids.update(item.task_id for item in frames.tasks if item.stalled)
        open_decision_ids = page.open_decision_task_ids
        rows = [
            self._task_browser_row(
                task,
                open_decision=task.id in open_decision_ids,
                diagnosed_stall=task.id in page_stalled_ids,
                now=now,
                task_filter=task_filter,
            )
            for task in page.tasks
        ]
        if task_filter is TaskBrowserFilter.ACTIVE:
            rows.sort(key=lambda row: not (row.open_decision or row.diagnosed_stall))
        selected_row = next(
            (row for row in rows if row.task_id == selected_task_id),
            None,
        )
        if selected_row is None and selected_task is not None:
            selected_row = self._task_browser_row(
                selected_task,
                open_decision=selected_task.status is Status.ESCALATED,
                diagnosed_stall=selected_task.id in page_stalled_ids,
                now=now,
                task_filter=task_filter,
            )
        return TaskBrowserView(
            filter=task_filter,
            search=search,
            rows=tuple(rows),
            selected_task=selected_row,
            selected_task_missing=selected_task_id is not None and selected_task is None,
            selected_task_matches=page.selected_task_matches,
            matching_count=page.matching_count,
            filtered_count=page.filtered_count,
            project_count=page.project_count,
            has_more=offset + len(rows) < page.matching_count,
            ancestor_context=tuple(
                TaskBrowserContextRow(
                    task_id=task.id,
                    title=task.title,
                    status=task.status.value,
                    mode=task.mode.value if task.mode is not None else None,
                )
                for task in page.ancestor_tasks
                if task.id not in {row.task_id for row in rows}
            ),
        )

    def _task_browser_row(
        self,
        task: Task,
        *,
        open_decision: bool,
        diagnosed_stall: bool,
        now: datetime,
        task_filter: TaskBrowserFilter,
    ) -> TaskBrowserRow:
        role = ACTING_ROLE.get(task.step) if task.step is not None else None
        group_label = None
        if task_filter is TaskBrowserFilter.WAITING:
            group_label = "Queued" if task.status is Status.QUEUED else "Started waiting"
        return TaskBrowserRow(
            task_id=task.id,
            title=task.title,
            status=task.status.value,
            step=task.step.value if task.step is not None else None,
            iteration=task.iteration,
            acting_role=role.value if role is not None else None,
            priority=task.priority,
            dependencies=tuple(task.depends_on),
            wait_reason=task.wait_reason,
            holds=tuple(sorted(hold.value for hold in task.holds)),
            age=_age(now - task.created_at),
            group_label=group_label,
            open_decision=open_decision,
            diagnosed_stall=diagnosed_stall,
            requeued_age=_age(now - task.requeued_at) if task.requeued_at is not None else None,
        )

    def escalations(self) -> list[EscalationPanelView]:
        """Build every durable open decision, retaining its row when evidence is unreadable."""
        return list(self._decision_views(self._clock.now()).values())

    def _cached_escalations(self) -> list[EscalationView]:
        version = self._state.data_version()
        if version != self._escalation_version:
            self._escalation_version = version
            self._escalation_cache = self._queries.list_escalations()
        items = self._escalation_cache
        if items is None:
            message = "escalation cache was not initialized"
            raise RuntimeError(message)
        return items

    def _decision_views(self, now: datetime) -> dict[str, EscalationPanelView]:
        items = self._cached_escalations()
        open_ids = {item.escalation.id for item in items}
        self._structured_escalation_cache = {
            escalation_id: cached
            for escalation_id, cached in self._structured_escalation_cache.items()
            if escalation_id in open_ids
        }
        pulse = self._motion.frames((), now, escalations_open=bool(items)).escalation_red
        tasks = {task.id: task for task in self._state.list_tasks()}
        result: dict[str, EscalationPanelView] = {}
        for item in items:
            escalation = item.escalation
            question, consequences, evidence_errors = self._structured_escalation(item)
            task = tasks.get(escalation.task_id)
            result[escalation.id] = EscalationPanelView(
                escalation_id=escalation.id,
                task_id=escalation.task_id,
                headline=escalation.headline,
                question=question,
                options=tuple(
                    f"`{option.label.value}`{' ★' if option.recommended else ''}"
                    + (f": {consequences[option.label]}" if option.label in consequences else "")
                    for option in escalation.options
                ),
                packet=item.packet if item.packet is not None else "",
                pulse_red=pulse,
                task_title=task.title if task is not None else escalation.task_id,
                age=_age(now - escalation.created_at),
                resolution_actions=tuple(option.label.value for option in escalation.options),
                evidence_error="; ".join(evidence_errors) if evidence_errors else None,
            )
        return result

    def _structured_escalation(
        self,
        item: EscalationView,
    ) -> tuple[str, dict[ResolutionKind, str], tuple[str, ...]]:
        escalation_id = item.escalation.id
        cached = self._structured_escalation_cache.get(escalation_id)
        if (
            cached is not None
            and cached.packet == item.packet
            and cached.packet_error == item.packet_error
        ):
            return cached.evidence
        question = ""
        consequences: dict[ResolutionKind, str] = {}
        errors = [item.packet_error] if item.packet_error is not None else []
        if item.packet is not None:
            packet = parse_escalation_packet(item.packet)
            errors.extend(packet.errors)
            if packet.agent_request is not None:
                question = packet.agent_request.question
            elif packet.question is not None:
                question = packet.question
            consequences = {
                resolution.kind: resolution.consequences for resolution in packet.resolutions
            }
        cached = question, consequences, tuple(errors)
        self._structured_escalation_cache[escalation_id] = _StructuredEscalationCache(
            packet=item.packet,
            packet_error=item.packet_error,
            evidence=cached,
        )
        return cached

    def _dialogue_view(self, task_id: str | None = None) -> list[DialogueBubbleView]:
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
                kinds=_ACTIVITY_KINDS,
                implementer_turns_only=True,
                task_id=task_id,
            )
        ]

    def attention(self) -> AttentionView:
        """Group durable decisions, existing stall diagnoses and open fault streaks by task."""
        now = self._clock.now()
        tasks = {task.id: task for task in self._state.list_tasks()}
        decision_views = self._decision_views(now)
        group_reasons: dict[str | None, list[str]] = {}
        group_decisions: dict[str | None, list[EscalationPanelView]] = {}
        decision_order: dict[str | None, tuple[datetime, str]] = {}
        group_titles: dict[str | None, str] = {}

        for item in self._cached_escalations():
            task_id = item.escalation.task_id
            group_decisions.setdefault(task_id, []).append(decision_views[item.escalation.id])
            task = tasks.get(task_id)
            group_titles[task_id] = task.title if task is not None else task_id
            order = (item.escalation.created_at, item.escalation.id)
            if task_id not in decision_order or order < decision_order[task_id]:
                decision_order[task_id] = order

        active = self._state.list_tasks(statuses=_ACTIVE_TURN_STATUSES)
        frames = self._motion.frames(tuple(active), now)
        stalled_ids = {item.task_id for item in frames.tasks if item.stalled}
        for task_id in stalled_ids:
            group_reasons.setdefault(task_id, []).append("diagnosed stall")
            group_titles[task_id] = tasks[task_id].title

        faults = self._cached_faults()
        for fault in faults:
            task_id = fault.task_id
            reason = f"engine fault · {fault.scope}: {fault.error} x{fault.repeat}"
            if task_id is None:
                group_titles[None] = "Project"
            else:
                task = tasks.get(task_id)
                group_titles[task_id] = task.title if task is not None else task_id
            group_reasons.setdefault(task_id, []).append(reason)

        group_ids = set(group_reasons) | set(group_decisions)
        decision_groups = sorted(
            (task_id for task_id in group_ids if task_id in decision_order),
            key=lambda task_id: decision_order[task_id],
        )
        other_groups = sorted(
            (task_id for task_id in group_ids if task_id not in decision_order),
            key=lambda task_id: (task_id is None, task_id or ""),
        )
        entries = tuple(
            AttentionGroupView(
                task_id=task_id,
                title=group_titles[task_id],
                reasons=tuple(group_reasons.get(task_id, ())),
                escalations=tuple(group_decisions.get(task_id, ())),
            )
            for task_id in (*decision_groups, *other_groups)
        )
        return AttentionView(
            groups=entries,
            decisions=len(decision_views),
            stalls=len(stalled_ids),
            faults=len(faults),
        )

    def engine_faults(self) -> tuple[EngineFaultView, ...]:
        """Return open recorded fault streaks with their original timestamps."""
        return tuple(
            EngineFaultView(
                scope=fault.scope,
                task_id=fault.task_id,
                error=fault.error,
                recoverable=fault.recoverable,
                first_seen=fault.first_seen.isoformat(),
                last_seen=fault.last_seen.isoformat(),
                repeats=fault.repeat,
            )
            for fault in self._cached_faults()
        )

    async def task_detail(
        self,
        task_id: str,
        *,
        relationship_offset: int = 0,
        timeline_cursor: str | None = None,
        timeline_role: str | None = None,
        timeline_turn: str | None = None,
        timeline_category: str = DEFAULT_TASK_TIMELINE_CATEGORY,
        include_timeline: bool = True,
    ) -> TaskDetailView | None:
        """Project current task state and its bounded, recorded evidence for the detail view."""
        detail = await self._queries.get_task_detail(task_id)
        if detail is None:
            return None
        timeline_view = None
        if include_timeline:
            try:
                timeline_view = await self.task_timeline(
                    task_id,
                    cursor=timeline_cursor,
                    role=timeline_role,
                    turn=timeline_turn,
                    category=timeline_category,
                )
            except (ShikiruError, OSError, ValueError, TypeError) as error:
                timeline_view = _timeline_read_error(error, role=timeline_role, turn=timeline_turn)
            if timeline_view is None:
                return None
        task = detail.task
        now = self._clock.now()
        triage = next(
            (item.data for item in reversed(detail.reports) if isinstance(item.data, TriageReport)),
            None,
        )
        verdict = next(
            (
                _task_report(item)
                for item in reversed(detail.reports)
                if isinstance(item.data, Verdict)
            ),
            None,
        )
        latest_report = next(
            (
                _task_report(item)
                for item in reversed(detail.reports)
                if item.source == AGENT_REPORT_SOURCE
            ),
            None,
        )
        ownership = tuple(
            run.role.value for run in detail.sessions if run.status is RunStatus.TAKEN_OVER
        )
        stored_relationships = self._state.task_relationships(
            task.id,
            offset=relationship_offset,
        )
        relationships = (
            _task_relationships_view(stored_relationships)
            if stored_relationships is not None
            else None
        )
        return TaskDetailView(
            task_id=task.id,
            title=task.title,
            status=task.status.value,
            status_reason=task.status_reason.value if task.status_reason is not None else None,
            mode=task.mode.value if task.mode is not None else None,
            step=_step_label(task.status, task.step, task.iteration),
            acting_role=task_acting_role(task).value,
            iteration=task.iteration,
            holds=tuple(sorted(hold.value for hold in task.holds)),
            wait_reason=task.wait_reason,
            ownership=ownership,
            request=task.request,
            acceptance_criteria=tuple(triage.acceptance_criteria) if triage is not None else (),
            progress=task_strip_data(task, self._settings),
            latest_report=latest_report,
            latest_verdict=verdict,
            events=tuple(
                TaskEventView(
                    timestamp=event.ts.isoformat(),
                    kind=event.kind.value,
                    message=event.message,
                )
                for event in detail.events
            ),
            signals=tuple(
                TaskSignalView(role=role, lines=tuple(lines))
                for role, lines in detail.signals.items()
            ),
            recorded_facts=detail.recorded_facts,
            facts_turn=detail.facts_turn,
            evidence_errors=tuple(detail.evidence_errors),
            branch=task.repo.branch if task.repo is not None else None,
            worktree_path=str(self._queries.task_worktree_path(task.id)),
            window_id=task.window_id,
            role_panes=tuple(
                TaskRolePaneView(
                    role=role.value,
                    recorded_target=task.panes.get(role),
                )
                for role in TASK_ROLES
            ),
            sessions=tuple(_task_session(run) for run in detail.sessions),
            attention=self.global_attention(),
            relationships=relationships,
            blocker=self._task_blocker(task, relationships, ownership, now),
            submitted_age=_age(now - task.created_at),
            requeued_age=_age(now - task.requeued_at) if task.requeued_at is not None else None,
            timeline=timeline_view,
        )

    async def task_timeline(
        self,
        task_id: str,
        *,
        cursor: str | None,
        role: str | None,
        turn: str | None,
        category: str,
    ) -> TaskTimelinePageView | None:
        """Return the task's bounded normalized evidence page without loading other files."""
        timeline = await self._queries.task_timeline(
            task_id,
            limit=self._settings.dashboard.timeline_page_size,
            cursor=cursor,
            role=role,
            turn=turn,
            category=category,
            scan_limit=self._settings.dashboard.timeline_scan_rows,
        )
        if timeline is None:
            return None
        return TaskTimelinePageView(
            entries=tuple(
                TaskTimelineEntryView(
                    identity=item.identity,
                    timestamp=item.timestamp,
                    timestamp_error=item.timestamp_error,
                    source=item.source,
                    category=item.category,
                    summary=item.summary,
                    detail=item.detail,
                    role=item.role,
                    turn=item.turn,
                    event_id=item.event_id,
                    incomplete=item.incomplete,
                )
                for item in timeline.entries
            ),
            older_cursor=timeline.older_cursor,
            has_older=timeline.has_older,
        )

    def _task_blocker(
        self,
        task: Task,
        relationships: TaskRelationshipsView | None,
        ownership: tuple[str, ...],
        now: datetime,
    ) -> TaskBlockerView | None:
        wait_reason = task.wait_reason
        quota_wait = self._running_quota_evidence(task, now)
        if wait_reason is None and not task.holds and quota_wait is None:
            return None
        hold_reason = Hold.PAUSED.value if Hold.PAUSED in task.holds else Hold.INTERVENTION.value
        reason = wait_reason or (WaitReason.QUOTA.value if quota_wait is not None else hold_reason)
        lines = _hold_evidence(task, ownership)
        if wait_reason is not None:
            lines.insert(0, f"Recorded scheduler reason · {wait_reason}")
        related: tuple[TaskReferenceView, ...] = ()
        match wait_reason:
            case WaitReason.DEPENDENCY.value:
                extra_lines, related = self._dependency_evidence(relationships)
            case WaitReason.SLOT.value:
                extra_lines, related = self._slot_evidence()
            case WaitReason.RESOURCES.value:
                extra_lines = self._resource_evidence(now)
            case WaitReason.QUOTA.value:
                extra_lines = self._quota_evidence(task, now)
            case WaitReason.PR_OPEN.value:
                extra_lines = self._pull_request_evidence(task.id)
            case WaitReason.PAUSED.value:
                extra_lines = ["Resume clears the scheduler pause wait when submitted."]
            case None:
                extra_lines = list(quota_wait or ())
            case _:
                extra_lines = ["No additional blocker evidence is recorded."]
        lines.extend(extra_lines)
        return TaskBlockerView(kind=reason, lines=tuple(lines), related_tasks=related)

    def _dependency_evidence(
        self,
        relationships: TaskRelationshipsView | None,
    ) -> tuple[list[str], tuple[TaskReferenceView, ...]]:
        if relationships is None:
            return ["Dependency relationship evidence is unavailable."], ()
        related = relationships.blocking_dependencies
        if related:
            lines = [
                "Blocking dependencies · "
                + "; ".join(f"{item.task_id} {item.title} ({item.status})" for item in related)
            ]
        elif relationships.blocking_dependency_count == 0:
            lines = ["No unfinished dependency is present in the current stored state."]
        elif relationships.page_offset >= relationships.blocking_dependency_count:
            lines = [
                (
                    f"Unfinished dependency evidence is on an earlier relationship page "
                    f"({relationships.blocking_dependency_count} recorded)."
                )
            ]
        else:
            lines = ["Additional blocking dependencies exist beyond the current bounded page."]
        if relationships.page_offset < relationships.blocking_dependency_count and (
            relationships.blocking_dependency_count > relationships.page_offset + len(related)
        ):
            lines.append(
                f"Showing {relationships.page_offset + 1} to "
                f"{relationships.page_offset + len(related)} of "
                f"{relationships.blocking_dependency_count} unfinished dependencies."
            )
        return lines, related

    def _running_quota_evidence(self, task: Task, now: datetime) -> tuple[str, ...] | None:
        if task.status not in _ACTIVE_TURN_STATUSES or task.step is None:
            return None
        role = task_acting_role(task)
        agent = pick_rung(task, role, self._settings).agent
        quota_events = self._current_quota_wait_events(task, role, agent)
        if quota_events is None:
            return None
        failure, quota_block = quota_events
        block = self._state.agent_block(agent)
        if block is None or block.until <= now:
            return None
        reset_at = failure.data.get(_RESET_AT_FIELD)
        reset_text = (
            reset_at
            if isinstance(reset_at, str)
            else "not reported"
            if reset_at is None and _RESET_AT_FIELD in failure.data
            else "unavailable"
        )
        lines = [
            f"Current turn quota wait · {role.value} · {agent}",
            f"Provider-reported quota reset · {reset_text}",
            f"Task quota-block event · {quota_block.ts.isoformat()}",
            f"Engine retry/probe deadline · {block.reason} · {block.until.isoformat()}",
        ]
        windows = self._state.list_quotas((agent,)).get(agent, [])
        if windows:
            for window in windows:
                reset = (
                    window.resets_at.isoformat() if window.resets_at is not None else "not reported"
                )
                lines.append(
                    f"Provider quota · {window.name} · reset {reset} · "
                    f"observed {_age(now - window.observed_at)} ago"
                )
        else:
            lines.append(f"Provider quota observation · {agent} · unavailable")
        return tuple(lines)

    def _current_quota_wait_events(
        self,
        task: Task,
        role: Role,
        agent: str,
    ) -> tuple[Event, Event] | None:
        if task.step is None:
            return None
        lifecycle = self._state.latest_events(
            1,
            kinds=_QUOTA_TURN_LIFECYCLE,
            task_id=task.id,
        )
        if (
            not lifecycle
            or lifecycle[0].kind is not EventKind.TURN_FAILED
            or lifecycle[0].data.get("kind") != FailureKind.QUOTA.value
            or lifecycle[0].data.get("step") != task.step.value
        ):
            return None
        failure = lifecycle[0]
        starts = self._state.latest_events(
            1,
            kinds=(EventKind.TURN_STARTED,),
            task_id=task.id,
        )
        if not starts or starts[0].id >= failure.id:
            return None
        started = starts[0]
        if (
            started.data.get("step") != task.step.value
            or started.data.get("iteration") != task.iteration
            or started.data.get("role") != role.value
            or started.data.get("agent") != agent
        ):
            return None
        quota_events = self._state.latest_events(
            1,
            kinds=(EventKind.QUOTA_BLOCKED, EventKind.QUOTA_CLEARED),
            task_id=task.id,
        )
        if (
            not quota_events
            or quota_events[0].kind is not EventKind.QUOTA_BLOCKED
            or quota_events[0].id <= failure.id
            or quota_events[0].data.get("agent") != agent
        ):
            return None
        return failure, quota_events[0]

    def _slot_evidence(self) -> tuple[list[str], tuple[TaskReferenceView, ...]]:
        occupied = [
            task
            for task in self._state.list_tasks(statuses=(Status.RUNNING, Status.INTEGRATING))
            if task.slot is not None
        ]
        lines = [
            f"Current occupancy · {len(occupied)}/{self._settings.orchestration.max_parallel} slots"
        ]
        if occupied:
            lines.append(
                "Occupying tasks · "
                + "; ".join(f"{item.id} {item.title} (slot {item.slot})" for item in occupied)
            )
        return lines, tuple(_task_reference_view(item) for item in occupied)

    def _resource_evidence(self, now: datetime) -> list[str]:
        sample = self._sources.resource_sample()
        if sample is None or sample.sampled_at is None:
            lines = ["Dashboard resource sample · unavailable"]
        else:
            lines = [
                (
                    "Dashboard resource sample · "
                    f"host CPU {sample.host_cpu_pct:.1f}% · "
                    f"free RAM {sample.free_ram_gb:.1f} GiB · "
                    f"sample age {_age(now - sample.sampled_at)}"
                )
            ]
        lines.append("The dashboard sample is separate from the scheduler's gate decision.")
        return lines

    def _quota_evidence(self, task: Task, now: datetime) -> list[str]:
        lines: list[str] = []
        required_agents = tuple(
            dict.fromkeys(
                pick_rung(task, role, self._settings).agent
                for role in (Role.LEADER, Role.IMPLEMENTER)
            )
        )
        for agent in required_agents:
            block = self._state.agent_block(agent)
            if block is None:
                lines.append(f"Scheduler quota account block · {agent} · not recorded")
            else:
                activity = "active" if block.until > now else "expired"
                lines.append(
                    f"Scheduler quota account block · {agent} · {activity} · "
                    f"{block.reason} · retry/probe deadline {block.until.isoformat()}"
                )
            windows = self._state.list_quotas((agent,)).get(agent, [])
            if not windows:
                lines.append(f"Provider quota observation · {agent} · unavailable")
            for window in windows:
                reset = (
                    window.resets_at.isoformat() if window.resets_at is not None else "not reported"
                )
                lines.append(
                    f"Provider quota · {agent} {window.name} · reset {reset} · "
                    f"observed {_age(now - window.observed_at)} ago"
                )
        return lines

    def _pull_request_evidence(self, task_id: str) -> list[str]:
        events = self._state.latest_events(
            1,
            kinds=(EventKind.PR_OPENED, EventKind.PR_WITHDRAWN),
            task_id=task_id,
        )
        if not events:
            return [
                "No recorded pull-request event is available.",
                "CI state is shown only when an engine observation recorded it.",
            ]
        event = events[0]
        lines: list[str] = []
        url = event.data.get("url")
        state = event.data.get("state")
        if isinstance(url, str):
            lines.append(f"Recorded PR URL · {url}")
        if isinstance(state, str):
            lines.append(f"Latest recorded PR state · {state} · {event.ts.isoformat()}")
        else:
            lines.append(f"Latest recorded PR event · {event.kind.value} · {event.ts.isoformat()}")
        lines.append("CI state is shown only when an engine observation recorded it.")
        return lines

    def dialogue(self, task_id: str | None = None) -> list[DialogueBubbleView]:
        """Build recent project or task decisions and activity in event order."""
        return self._dialogue_view(task_id)

    def usage(self, task_id: str | None = None) -> list[AgentUsageView]:
        """Build project usage or usage recorded for one task."""
        now = self._clock.now()
        version = self._state.data_version()
        if version != self._usage_version:
            self._usage_version = version
            self._project_usage_cache = None
            self._task_usage_cache.clear()
        if task_id is not None:
            usage = self._task_usage_cache.get(task_id)
            if usage is None:
                usage = self._queries.task_usage(task_id)
                self._task_usage_cache[task_id] = usage
            return [_agent_usage(item, quotas=()) for item in usage]
        project_usage = self._project_usage_cache
        if project_usage is None:
            project_usage = self._queries.usage_and_quotas()
            self._project_usage_cache = project_usage
        usage, quotas = project_usage
        by_agent = {item.agent: item for item in usage}
        agents = sorted(set(by_agent) | set(quotas))
        quota_states = tuple(
            QuotaMotionState(agent, window.name, window.resets_at)
            for agent in agents
            for window in quotas.get(agent, [])
        )
        quota_frames = {
            (item.agent, item.window): item.remaining_seconds
            for item in self._motion.frames((), now, quotas=quota_states).quotas
        }
        result: list[AgentUsageView] = []
        for agent in agents:
            item = by_agent.get(agent)
            windows = tuple(
                QuotaGaugeView(
                    name=window.name,
                    used_pct=window.used_pct,
                    resets_in=(
                        _quota_countdown(
                            quota_frames[(agent, window.name)], window.resets_at is not None
                        )
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
                    run_count=item.run_count if item is not None else 0,
                    usage_reported_runs=item.usage_reported_runs if item is not None else 0,
                    legacy_token_runs=item.legacy_token_runs if item is not None else 0,
                    legacy_unknown_token_runs=(
                        item.legacy_unknown_token_runs if item is not None else 0
                    ),
                    billed_cost_usd=item.billed_cost_usd if item is not None else None,
                    billed_cost_runs=item.billed_cost_runs if item is not None else 0,
                    legacy_billed_cost_runs=(
                        item.legacy_billed_cost_runs if item is not None else 0
                    ),
                    api_equivalent_cost_usd=(
                        item.api_equivalent_cost_usd if item is not None else None
                    ),
                    api_equivalent_cost_runs=(
                        item.api_equivalent_cost_runs if item is not None else 0
                    ),
                    legacy_api_equivalent_cost_runs=(
                        item.legacy_api_equivalent_cost_runs if item is not None else 0
                    ),
                    unknown_cost_runs=item.unknown_cost_runs if item is not None else 0,
                )
            )
        return result

    def global_attention(self) -> GlobalAttentionView:
        """Count open decisions and fault streaks for the task window's global indicator."""
        return GlobalAttentionView(
            decisions=len(self._state.list_open_escalations()),
            faults=len(self._queries.open_faults()),
        )

    def command_history(
        self,
        *,
        expanded: bool = False,
        limit: int | None = None,
        offset: int = 0,
        status: CommandFeedbackStatus | None = None,
    ) -> CommandHistoryView:
        """Project a bounded durable command-history page and engine liveness."""
        history = self._queries.command_history(
            expanded=expanded,
            limit=limit,
            offset=offset,
            status=_stored_command_status(status),
        )
        return CommandHistoryView(
            engine_up=history.engine_up,
            entries=tuple(
                CommandFeedbackEntryView(
                    command_id=entry.id,
                    action=entry.action,
                    target=entry.target,
                    status=_command_feedback_status(entry.status),
                    result=(
                        json.dumps(entry.result, ensure_ascii=False, sort_keys=True)
                        if entry.result is not None
                        else None
                    ),
                )
                for entry in history.entries
            ),
            offset=history.offset,
            limit=history.limit,
            total_count=history.total_count,
            has_more=history.has_more,
            status=status,
        )

    async def resources(self) -> ResourceView:
        """Return the current host and engine/task-pane process-tree sample."""
        return await self._sources.resources()

    async def containers(self) -> DockerView:
        """Return the current Docker sample."""
        return await self._sources.containers()

    def _strip_with_motion(
        self, task: Task, motion: TaskMotionFrame, cost_usd: float | None
    ) -> TaskStripData:
        data = task_strip_data(task, self._settings)
        panels = list(data.panels)
        active_index = next(
            (index for index, panel in enumerate(panels) if panel.state is PanelState.ACTIVE),
            None,
        )
        if active_index is not None:
            panel = panels[active_index]
            detail = [panel.detail, _duration(motion.elapsed_seconds)]
            if cost_usd is not None:
                detail.append(f"${cost_usd:.2f}")
            panels[active_index] = replace(
                panel,
                state=PanelState.STALLED if motion.stalled else panel.state,
                detail=" · ".join(part for part in detail if part),
                spinner_frame=(
                    ""
                    if not motion.spinner_visible
                    else SPINNER_FRAMES[motion.spinner_phase % len(SPINNER_FRAMES)]
                ),
            )
        stamp = data.stamp
        if motion.stamp_kind is MotionStampKind.VERDICT:
            stamp = StripStamp(StampKind.VERDICT, motion.stamp_detail, motion.stamp_flash)
        elif motion.stamp_kind is MotionStampKind.MERGED:
            stamp = StripStamp(StampKind.MERGED, flash=motion.stamp_flash)
        return replace(data, panels=tuple(panels), stamp=stamp)


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


def _task_reference_view(task: Task) -> TaskReferenceView:
    return TaskReferenceView(
        task_id=task.id,
        title=task.title,
        status=task.status.value,
        status_reason=task.status_reason.value if task.status_reason is not None else None,
        mode=task.mode.value if task.mode is not None else None,
    )


def _hold_evidence(task: Task, ownership: tuple[str, ...]) -> list[str]:
    lines: list[str] = []
    if Hold.PAUSED in task.holds:
        lines.append("Pause hold · Resume clears this hold when submitted.")
    if Hold.INTERVENTION in task.holds:
        lines.append(
            "Ownership hold · taken-over roles · "
            + (", ".join(ownership) if ownership else "not recorded")
        )
        lines.append("Hand back each taken-over role to return it to automation.")
    return lines


def _task_relationships_view(relationships: TaskRelationships) -> TaskRelationshipsView:
    return TaskRelationshipsView(
        family_parent=_task_reference_view(relationships.family_parent),
        family_root=_task_reference_view(relationships.family_root),
        family_children=tuple(_task_reference_view(task) for task in relationships.family_children),
        family_child_count=relationships.family_child_count,
        dependencies=tuple(_task_reference_view(task) for task in relationships.dependencies),
        dependency_count=relationships.dependency_count,
        blocking_dependencies=tuple(
            _task_reference_view(task) for task in relationships.blocking_dependencies
        ),
        blocking_dependency_count=relationships.blocking_dependency_count,
        dependents=tuple(_task_reference_view(task) for task in relationships.dependents),
        dependent_count=relationships.dependent_count,
        replacements=tuple(_task_reference_view(task) for task in relationships.replacements),
        replacement_count=relationships.replacement_count,
        family_outcomes=tuple(
            TaskFamilyOutcomeView(outcome=outcome, count=count)
            for outcome, count in relationships.family_outcomes
        ),
        page_offset=relationships.page_offset,
        page_size=relationships.page_size,
        has_more=relationships.has_more,
    )


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


def _duration(seconds: int) -> str:
    minutes, remainder = divmod(seconds, SECONDS_PER_MINUTE)
    hours, minutes = divmod(minutes, MINUTES_PER_HOUR)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {remainder}s"
    return f"{remainder}s"


def _quota_countdown(seconds: int | None, has_reset: bool) -> str:
    if not has_reset:
        return TIME_UNKNOWN
    if seconds is None:
        message = "a quota reset deadline did not produce a countdown"
        raise RuntimeError(message)
    return _duration(seconds)


def _step_label(status: Status, step: Step | None, iteration: int) -> str:
    if step is None:
        return SETUP_STEP if status is Status.RUNNING else UNSTARTED_STEP
    if step is Step.CONSULT:
        return CONSULT_STEP
    if step is Step.INTEGRATE:
        return INTEGRATION_STEP
    if step is Step.IMPLEMENT and iteration > 1:
        return REWORK_STEP
    return step.value


def _task_report(row: ReportRow) -> TaskReportView:
    report = row.data
    summary = ""
    decision = None
    question = ""
    issues: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    tests: tuple[TaskTestView, ...] = ()
    if isinstance(report, TriageReport):
        summary = report.title
    elif isinstance(report, ImplementationReport):
        summary = report.summary
        question = report.question
        risks = tuple(report.risks)
        tests = tuple(
            TaskTestView(
                name=test.name,
                result=test.result,
                details=test.details,
                provenance=AGENT_REPORTED_TESTS,
            )
            for test in report.tests
        )
    elif isinstance(report, PlanReport):
        summary = report.summary
        risks = tuple(report.scope_changes)
    elif isinstance(report, ConsultAnswer):
        summary = report.answer or report.decision
        decision = report.decision
    else:
        summary = report.summary
        decision = report.decision
        issues = tuple(
            f"{issue.severity}: {issue.description} {issue.location}".rstrip()
            for issue in report.issues
        )
        risks = tuple(report.residual_risks)
    return TaskReportView(
        turn=row.turn,
        kind=row.kind.value,
        role=(
            Role.IMPLEMENTER.value
            if isinstance(report, ImplementationReport)
            else Role.LEADER.value
        ),
        provenance=row.source,
        summary=summary,
        decision=decision,
        question=question,
        issues=issues,
        risks=risks,
        tests=tests,
    )


def _task_session(run: Run) -> TaskSessionView:
    return TaskSessionView(
        role=run.role.value,
        agent=run.agent,
        model=run.model,
        effort=run.effort,
        session_id=run.session_id,
        status=run.status.value,
        input_tokens=run.usage.input_tokens,
        output_tokens=run.usage.output_tokens,
        cache_read_tokens=run.usage.cache_read_tokens,
        cache_write_tokens=run.usage.cache_write_tokens,
        cost_usd=run.usage.cost_usd,
        cost_basis=run.usage.cost_basis,
        context_used_pct=run.usage.context_used_pct,
        usage_reported=run.usage_reported,
        legacy_token_totals=(run.usage_reported is None and has_positive_token_evidence(run)),
        legacy_cost_recorded=(run.usage_reported is None and run.usage.cost_usd is not None),
    )


def _agent_usage(item: UsageView, *, quotas: tuple[QuotaGaugeView, ...]) -> AgentUsageView:
    return AgentUsageView(
        agent=item.agent,
        input_tokens=item.input_tokens,
        output_tokens=item.output_tokens,
        cache_read_tokens=item.cache_read_tokens,
        cache_write_tokens=item.cache_write_tokens,
        cost_usd=item.cost_usd,
        cost_basis=item.cost_basis,
        quotas=quotas,
        run_count=item.run_count,
        usage_reported_runs=item.usage_reported_runs,
        legacy_token_runs=item.legacy_token_runs,
        legacy_unknown_token_runs=item.legacy_unknown_token_runs,
        billed_cost_usd=item.billed_cost_usd,
        billed_cost_runs=item.billed_cost_runs,
        legacy_billed_cost_runs=item.legacy_billed_cost_runs,
        api_equivalent_cost_usd=item.api_equivalent_cost_usd,
        api_equivalent_cost_runs=item.api_equivalent_cost_runs,
        legacy_api_equivalent_cost_runs=item.legacy_api_equivalent_cost_runs,
        unknown_cost_runs=item.unknown_cost_runs,
    )


def _command_feedback_status(status: CommandStatus) -> CommandFeedbackStatus:
    if status is CommandStatus.PENDING:
        return CommandFeedbackStatus.PENDING
    if status is CommandStatus.DONE:
        return CommandFeedbackStatus.APPLIED
    return CommandFeedbackStatus.REJECTED


def _stored_command_status(
    status: CommandFeedbackStatus | None,
) -> CommandStatus | None:
    if status is None:
        return None
    if status is CommandFeedbackStatus.PENDING:
        return CommandStatus.PENDING
    if status is CommandFeedbackStatus.APPLIED:
        return CommandStatus.DONE
    return CommandStatus.REJECTED


def _timeline_read_error(
    error: ShikiruError | OSError | ValueError | TypeError,
    *,
    role: str | None,
    turn: str | None,
) -> TaskTimelinePageView:
    return TaskTimelinePageView(
        entries=(
            TaskTimelineEntryView(
                identity=TASK_TIMELINE_READ_ERROR_ID,
                timestamp=None,
                timestamp_error=TASK_TIMELINE_READ_ERROR_TIMESTAMP,
                source=TaskTimelineSource.AGENT.value,
                category=TIMELINE_EVIDENCE_CATEGORY,
                summary=TASK_TIMELINE_READ_ERROR_SUMMARY,
                detail=str(error),
                role=role,
                turn=turn,
                event_id=None,
                incomplete=False,
            ),
        ),
        older_cursor=None,
        has_older=False,
    )
