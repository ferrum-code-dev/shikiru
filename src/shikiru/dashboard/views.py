"""Compose ready-to-render dashboard data from state, settings and services."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from functools import partial
from operator import attrgetter
from typing import TYPE_CHECKING, Protocol

from shikiru.agents.contracts import (
    BILLING_MODE_API,
    BILLING_MODE_SUBSCRIPTION,
    BILLING_MODE_UNKNOWN,
    COST_BASIS_UNKNOWN,
)
from shikiru.clock import to_local
from shikiru.dashboard.contract import (
    AgentUsageView,
    AttentionGroupView,
    AttentionView,
    BlockerEvidenceView,
    CommandFeedbackEntryView,
    CommandFeedbackStatus,
    CommandHistoryView,
    DialogueBubbleView,
    DiskView,
    DockerView,
    EngineFaultView,
    EngineStatusView,
    EscalationPanelView,
    HeaderView,
    HeldTaskStateView,
    PublicationState,
    PublicationView,
    QueueRow,
    QuotaBlockView,
    QuotaEvidenceView,
    QuotaGaugeView,
    QuotaReadingView,
    QuotaWaitView,
    RecordedUsageView,
    RepoStatus,
    ResourceView,
    TaskBlockerView,
    TaskBrowserFilter,
    TaskBrowserRow,
    TaskBrowserView,
    TaskDetailView,
    TaskFamilyOutcomeView,
    TaskMilestoneView,
    TaskReferenceView,
    TaskRelationshipsView,
    TaskReportView,
    TaskSessionView,
    TaskTestView,
    TaskTimelineCategory,
    TaskTimelineEntryView,
    TaskTimelinePageView,
    TaskTimelineSource,
    WorkspaceSummaryView,
)
from shikiru.dashboard.fault_guidance import RetryPolicy, engine_fault_view
from shikiru.dashboard.session_history import project_task_sessions
from shikiru.dashboard.tick import TickMemo, tick_now
from shikiru.domain.acceptance import acceptance_criteria_from_request
from shikiru.domain.briefing import (
    escalation_remaining,
    pull_request_line,
    reported_tests,
    verdict_counts,
)
from shikiru.domain.commands import CommandStatus, ResolutionKind
from shikiru.domain.decisions import derive_decisions
from shikiru.domain.engine_uptime import project_engine_uptime
from shikiru.domain.escalation_evidence import parse_escalation_packet
from shikiru.domain.events import EventKind
from shikiru.domain.ladder import move_up, pick_rung
from shikiru.domain.levels import free_space_level, usage_level
from shikiru.domain.motion import MotionStampKind, QuotaMotionState
from shikiru.domain.quota_waits import (
    QUOTA_NOT_REPORTED,
    current_quota_wait_events,
    engine_retry_note,
    next_quota_reload,
    quota_banner,
    quota_wait_description,
)
from shikiru.domain.reports import (
    ConsultAnswer,
    FeedbackAnalysis,
    ImplementationReport,
    PlanReport,
    TriageReport,
    Verdict,
)
from shikiru.domain.results import derive_task_results, new_task_ids, tracks_live_work
from shikiru.domain.runs import RunStatus, has_positive_token_evidence
from shikiru.domain.scheduling import (
    WaitReason,
    dispatch_sort_key,
    occupied_slot_ids,
    quota_blocked_agents,
    quota_blocking_agent,
    quota_wait_reroutes,
    running_quota_wait_moves,
    task_agents,
)
from shikiru.domain.steps import ACTING_ROLE, Step, task_acting_role
from shikiru.domain.tasks import ACTIVE_STATUSES, Hold, Role, Status, Task
from shikiru.domain.timeline import TIMELINE_EVIDENCE_CATEGORY
from shikiru.domain.timeline_phrases import GLYPH_ATTENTION, Tone
from shikiru.domain.workflow import WorkflowProgress, WorkflowReport, project_workflow
from shikiru.errors import ShikiruError
from shikiru.operator.queries import (
    AGENT_REPORT_SOURCE,
    OperatorTaskAction,
    aggregate_usage,
)
from shikiru.operator.strip_views import task_strip_data
from shikiru.ui.age import SECONDS_PER_DAY, format_age, format_duration
from shikiru.ui.age import format_short_age as _age
from shikiru.ui.glyphs import GLYPH_STALLED, SPINNER_FRAMES
from shikiru.ui.section import GROUP_SEPARATOR
from shikiru.ui.strip import PanelState, StampKind, StripStamp, TaskStripData
from shikiru.view_log import turn_iteration

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

    from shikiru.agents.contracts import QuotaWindow
    from shikiru.clock import Clock
    from shikiru.dashboard.live_settings import LiveSettings
    from shikiru.dashboard.motion import DashboardMotion
    from shikiru.dashboard.services import TaskWorkSample, TaskWorkService
    from shikiru.domain.briefing import PullRequestLine, ReportedTests
    from shikiru.domain.decisions import TaskDecisions
    from shikiru.domain.events import Event
    from shikiru.domain.faults import FaultStreak
    from shikiru.domain.motion import MotionFrame, TaskMotionFrame
    from shikiru.domain.reports import Commit
    from shikiru.domain.results import TaskResults
    from shikiru.domain.runs import Run
    from shikiru.domain.tasks import Task
    from shikiru.operator.queries import (
        EscalationView,
        OperatorQueries,
        ReportRow,
        TaskDetail,
        UsageView,
    )
    from shikiru.process import ProcessIdentity
    from shikiru.store.quotas import AgentBlock
    from shikiru.store.reader import StateReader
    from shikiru.store.tasks import TaskBrowserPage, TaskRelationships

DIALOGUE_LINE_LIMIT = 20
ALERT_TIME_FORMAT = "%H:%M:%S"
PUSH_TIME_FORMAT = "%H:%M"
SECONDS_PER_MINUTE = 60
MINUTES_PER_HOUR = 60
TIME_UNKNOWN = "\N{EN DASH}"
TASK_TIMELINE_READ_ERROR_ID = "timeline-read-error"
TASK_TIMELINE_READ_ERROR_SUMMARY = "Task evidence could not be read"
ENGINE_LIFECYCLE_EVENT_LIMIT = 2
TASK_MODEL_HISTORY_EVENT_LIMIT = 50
RESULTS_PLAN_CHANGE_LIMIT = 20
DECISION_EVENT_LIMIT = 60
DECISION_EVENTS = (
    EventKind.VERDICT,
    EventKind.ESCALATION,
    EventKind.ESCALATION_RESOLVED,
    EventKind.SCOPE_CHANGE,
    EventKind.RUNG_CHANGED,
    EventKind.OPERATOR_MESSAGE,
    EventKind.TAKEN_OVER,
    EventKind.HANDED_BACK,
    EventKind.HOLD_SET,
    EventKind.HOLD_CLEARED,
    EventKind.TASK_RETRIED,
    EventKind.TASK_CANCELLED,
    EventKind.PR_OPENED,
    EventKind.PR_WITHDRAWN,
    EventKind.TASK_DONE,
)
WORKFLOW_EVENT_LIMIT = 200
WORKFLOW_EVENTS = (
    EventKind.TASK_QUEUED,
    EventKind.STEP_STARTED,
    EventKind.TURN_STARTED,
    EventKind.VERDICT,
)
TASK_MODEL_HISTORY_EVENTS = (
    EventKind.TURN_STARTED,
    EventKind.TURN_COMPLETED,
    EventKind.TURN_FAILED,
    EventKind.TURN_STOPPED,
)
_ENGINE_LIFECYCLE_EVENTS = (EventKind.ENGINE_STARTED, EventKind.ENGINE_STOPPED)
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
_MOTION_FILTERS = frozenset(
    {
        TaskBrowserFilter.ACTIVE,
        TaskBrowserFilter.ALL,
        TaskBrowserFilter.RUNNING,
        TaskBrowserFilter.INTEGRATING,
        TaskBrowserFilter.ESCALATED,
    }
)
_RECORDED_REASON = "Recorded scheduler reason"
_UNRECORDED_REASON = "Waiting on"
_RECORDED_HOLD = "Recorded hold"
_RECORDED_TAKEOVER = "Recorded takeover"
_PAUSED_HEADLINE = "Paused by operator"
_INTERVENTION_HEADLINE = "Held by operator"
_PAUSE_HOLD_DETAIL = "Pause hold · Resume clears this hold when submitted."
_PAUSE_HOLD_EVIDENCE = f"{_RECORDED_HOLD}{GROUP_SEPARATOR}{Hold.PAUSED.value}"
_INTERVENTION_HOLD_EVIDENCE = f"{_RECORDED_HOLD}{GROUP_SEPARATOR}{Hold.INTERVENTION.value}"
_OWNERSHIP_HOLD_LABEL = "Ownership hold · taken-over roles"
_NO_OWNER_RECORDED = "not recorded"
_HAND_BACK_DETAIL = "Hand back each taken-over role to return it to automation."
_DEPENDENCY_HEADLINE = "Waiting on dependency"
_DEPENDENCY_UNAVAILABLE = "Dependency relationship evidence is unavailable."
_NO_UNFINISHED_DEPENDENCY = "No unfinished dependency is present in the current stored state."
_DEPENDENCIES_BEYOND_PAGE = (
    "Additional blocking dependencies exist beyond the current bounded page."
)
_SLOT_HEADLINE = "Waiting for a slot"
_SLOT_DETAIL = "Starts when a slot frees."
_RESOURCES_HEADLINE = "Waiting on resources"
_RESOURCES_DETAIL = "Starts when the scheduler's resource gate admits it."
_RESOURCE_SAMPLE_DISCLAIMER = "The dashboard sample is separate from the scheduler's gate decision."
_QUOTA_HEADLINE = "Waiting on quota"
_QUOTA_REROUTED = "Moves to another agent first, as the wait exceeds max_quota_wait."
_QUOTA_WAITS_NO_OTHER_AGENT = "Waits for reset; no other agent is free to take it."
_QUOTA_ESCALATES_AT_LIMIT = "Escalates at max_quota_wait; no other agent is free to take it."
_QUOTA_WAITS_WITHIN_LIMIT = "Waits for reset; the block lifts within max_quota_wait."
_PR_HEADLINE = "Waiting on pull request"
_PR_NO_EVENT = "No recorded pull-request event is available."
_PR_CI_NOTE = "CI state is shown only when an engine observation recorded it."
_RESET_AT_FIELD = "resets_at"
_RECORDED_QUOTA_RESET = "Recorded quota reset"
_UNAVAILABLE = "unavailable"
StructuredEscalationEvidence = tuple[str, dict[ResolutionKind, str], tuple[str, ...]]


@dataclass(frozen=True)
class _StructuredEscalationCache:
    packet: str | None
    packet_error: str | None
    evidence: StructuredEscalationEvidence


@dataclass(frozen=True)
class _RunningQuotaWait:
    block: AgentBlock
    budget_ends: datetime
    quota: QuotaEvidenceView
    raw: tuple[str, ...]


@dataclass(frozen=True)
class _BlockerParts:
    headline: str
    details: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    related: tuple[TaskReferenceView, ...] = ()
    raw: tuple[str, ...] = ()
    quotas: tuple[QuotaEvidenceView, ...] = ()
    observed: tuple[datetime, ...] = ()


@dataclass(frozen=True)
class _FoundBlocker:
    kind: str
    parts: _BlockerParts
    block: AgentBlock | None = None


@dataclass(frozen=True)
class _QuotaWaits:
    task_ids: frozenset[str]
    waiting_by_agent: Counter[str]


class _VersionedCache[K, V]:
    def __init__(self, state: StateReader) -> None:
        self._state = state
        self._version: int | None = None
        self._values: dict[K, V] = {}

    def get(self, key: K, load: Callable[[], V]) -> V:
        version = self._state.data_version()
        if version != self._version:
            self._version = version
            self._values = {}
        if key not in self._values:
            self._values[key] = load()
        return self._values[key]


class DashboardSources(Protocol):
    """External information composed into dashboard view models."""

    async def repo_status(self) -> RepoStatus:
        """Return the project checkout's git status."""
        ...

    def engine_up(self) -> bool:
        """Return whether the engine holds its liveness lock."""
        ...

    def engine_identity(self) -> ProcessIdentity | None:
        """Return the verified process identity of the engine lock holder."""
        ...

    async def resources(self) -> ResourceView:
        """Return host and Shikiru process-tree resource readings."""
        ...

    async def containers(self) -> DockerView:
        """Return the current Docker sample."""
        ...

    async def disk(self) -> DiskView:
        """Return the current disk sample."""
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
        settings: LiveSettings,
        sources: DashboardSources,
        queries: OperatorQueries,
        clock: Clock,
        motion: DashboardMotion,
        work: TaskWorkService,
    ) -> None:
        """Use `state`, shared operator queries, sources and `clock` for widget data."""
        self._state = state
        self._focus_task = focus_task
        self._settings = settings
        self._sources = sources
        self._queries = queries
        self._clock = clock
        self._motion = motion
        self._work = work
        self._usage_version = -1
        self._resource_listeners: list[Callable[[], None]] = []
        self._project_usage_cache: tuple[list[UsageView], dict[str, list[QuotaWindow]]] | None = (
            None
        )
        self._project_usage_view_cache: RecordedUsageView | None = None
        self._task_usage_cache: dict[str, list[UsageView]] = {}
        self._task_cache: _VersionedCache[tuple[Status, ...] | None, list[Task]] = _VersionedCache(
            state
        )
        self._browser_page_cache: _VersionedCache[tuple[object, ...], TaskBrowserPage] = (
            _VersionedCache(state)
        )
        self._browser_pages_cache: _VersionedCache[
            tuple[object, ...], tuple[TaskBrowserPage, ...]
        ] = _VersionedCache(state)
        self._structured_escalation_cache: dict[str, _StructuredEscalationCache] = {}
        self._fault_version = -1
        self._fault_cache: tuple[FaultStreak, ...] | None = None
        self._tick_memo = TickMemo(state.data_version)

    def _now(self) -> datetime:
        return tick_now(self._clock)

    def _cached_tasks(self, *, statuses: tuple[Status, ...] | None = None) -> list[Task]:
        return list(
            self._task_cache.get(statuses, lambda: self._state.list_tasks(statuses=statuses))
        )

    async def header(self) -> HeaderView:
        """Build the project, engine, slot and repository summary."""
        tasks = self._cached_tasks()
        engine = self.engine_status()
        repo = await self._sources.repo_status()
        return HeaderView(
            project=self._settings.current().project.name,
            engine_up=engine.up,
            occupied_slots=len(occupied_slot_ids(tasks)),
            maximum_slots=self._settings.current().orchestration.max_parallel,
            repo=repo,
            alerts=self._alerts(),
            integration_mode=self._settings.current().orchestration.integration,
            engine_uptime_seconds=engine.uptime_seconds,
            previous_engine_run_seconds=engine.previous_run_seconds,
            publication=self._publication(repo),
        )

    def _publication(self, repo: RepoStatus) -> PublicationView:
        target = None if repo.base is None else self._state.push_target(repo.base)
        scheduled = None if target is None or not repo.remote_configured else target.scheduled_at
        push_time = None if scheduled is None else to_local(scheduled).strftime(PUSH_TIME_FORMAT)
        return PublicationView(
            state=_publication_state(repo),
            remote=self._settings.current().repo.remote,
            unpushed=repo.unpushed,
            push_time=push_time,
        )

    def engine_status(self) -> EngineStatusView:
        """Return the lock liveness with the recorded duration of the live or previous run."""
        engine_up = self.engine_up()
        process_identity = self._sources.engine_identity()
        identity = (
            (process_identity.pid, process_identity.started_at)
            if process_identity is not None and process_identity.started_at is not None
            else None
        )
        uptime = project_engine_uptime(
            self._state.latest_events(ENGINE_LIFECYCLE_EVENT_LIMIT, kinds=_ENGINE_LIFECYCLE_EVENTS),
            now=self._now(),
            running=engine_up,
            identity=identity,
        )
        return EngineStatusView(
            up=engine_up,
            uptime_seconds=uptime.running_seconds,
            previous_run_seconds=uptime.previous_run_seconds,
        )

    def project_usage(self) -> RecordedUsageView:
        """Project recorded usage and provenance across every retained session lineage."""
        self._refresh_usage_cache_version()
        if self._project_usage_view_cache is None:
            self._project_usage_view_cache = _recorded_usage(self._state.list_all_runs())
        return self._project_usage_view_cache

    def engine_up(self) -> bool:
        """Return whether the current engine holds its liveness lock."""
        return self._sources.engine_up()

    def workspace_summary(self) -> WorkspaceSummaryView:
        """Count home categories without treating them as disjoint task totals."""
        return self._tick_memo.get("workspace_summary", self._build_workspace_summary)

    def _build_workspace_summary(self) -> WorkspaceSummaryView:
        attention = self.attention()
        tasks = self._cached_tasks()
        active_tasks = [
            task for task in tasks if task.status not in (Status.DONE, Status.CANCELLED)
        ]
        now = self._now()
        quota_wait_ids = {
            task.id for task in tasks if self._running_quota_wait(task, now) is not None
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
        held_tasks = [task for task in active_tasks if task.held]
        engine_up = self.engine_up()
        operator_owned_roles = {
            task.id: tuple(role.value for role in self._state.list_taken_over_roles(task.id))
            for task in held_tasks
            if Hold.INTERVENTION in task.holds
        }
        operator_owned = tuple(
            f"{task.id} {role}"
            for task in held_tasks
            for role in operator_owned_roles.get(task.id, ())
        )
        held_task_states = tuple(
            HeldTaskStateView(
                task_id=task.id,
                holds=tuple(sorted(hold.value for hold in task.holds)),
                operator_owned_roles=operator_owned_roles.get(task.id, ()),
            )
            for task in held_tasks
        )
        return WorkspaceSummaryView(
            attention_groups=len(attention.groups),
            decisions=attention.decisions,
            stalls=attention.stalls,
            faults=attention.faults,
            running=running,
            waiting=waiting,
            engine_up=engine_up,
            held_tasks=len(held_tasks),
            holds=tuple(sorted({hold.value for task in held_tasks for hold in task.holds})),
            operator_owned=operator_owned,
            held_task_states=held_task_states,
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
        reload_error = self._settings.reload_error()
        if reload_error is not None:
            lines.append(f"settings not reloaded · {' · '.join(reload_error.splitlines())}")
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
        return list(self._tick_memo.get("strips", self._build_strips))

    def _build_strips(self) -> list[TaskStripData]:
        now = self._now()
        tasks = self._cached_tasks(statuses=ACTIVE_STATUSES)
        tasks.sort(key=lambda task: task.id != self._focus_task)
        recent_merges = self._motion.recent_merges(now)
        task_ids = {task.id for task in tasks}
        all_tasks = [*tasks, *(task for task in recent_merges if task.id not in task_ids)]
        rendered = self._motion.frames(tuple(all_tasks), now, engine_running=self.engine_up())
        frames = {item.task_id: item for item in rendered.tasks}
        return [
            self._strip_with_motion(task, frames[task.id], self._motion.current_cost(task))
            for task in all_tasks
        ]

    def queue(self) -> list[QueueRow]:
        """Build queued-task rows in dispatch order."""
        tasks = self._cached_tasks(statuses=(Status.QUEUED,))
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
        now = self._now()
        (
            motion_task_ids,
            stalled_ids,
            diagnosed_stall_task_ids,
            agent_processing_ids,
        ) = self._task_browser_motion(task_filter, now)
        quota_wait_task_ids = (
            self._quota_wait_task_ids(now) if task_filter is TaskBrowserFilter.WAITING else ()
        )
        page = self._browser_page_cache.get(
            (
                task_filter,
                search,
                limit,
                offset,
                diagnosed_stall_task_ids,
                quota_wait_task_ids,
            ),
            lambda: self._state.task_browser_page(
                category=task_filter.value,
                search=search,
                limit=limit,
                offset=offset,
                diagnosed_stall_task_ids=diagnosed_stall_task_ids,
                quota_wait_task_ids=quota_wait_task_ids,
            ),
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
            agent_processing_ids=agent_processing_ids,
        )

    @property
    def recent_task_limit(self) -> int:
        """Return how many most recent terminal tasks Home Recent can page through."""
        return self._settings.current().dashboard.recent_task_limit

    @property
    def recent_task_window(self) -> int:
        """Return how many Recent tasks Home shows at once."""
        return self._settings.current().dashboard.recent_task_window

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
        now = self._now()
        (
            motion_task_ids,
            stalled_ids,
            diagnosed_stall_task_ids,
            agent_processing_ids,
        ) = self._task_browser_motion(task_filter, now)
        quota_wait_task_ids = (
            self._quota_wait_task_ids(now) if task_filter is TaskBrowserFilter.WAITING else ()
        )
        pages = self._browser_pages_cache.get(
            (
                task_filter,
                search,
                limit,
                page_count,
                diagnosed_stall_task_ids,
                quota_wait_task_ids,
            ),
            lambda: self._state.task_browser_pages(
                category=task_filter.value,
                search=search,
                limit=limit,
                page_count=page_count,
                diagnosed_stall_task_ids=diagnosed_stall_task_ids,
                quota_wait_task_ids=quota_wait_task_ids,
            ),
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
                agent_processing_ids=agent_processing_ids,
            )
            for page_index, page in enumerate(pages)
        )

    def _task_browser_motion(
        self,
        task_filter: TaskBrowserFilter,
        now: datetime,
    ) -> tuple[set[str], set[str], tuple[str, ...], set[str]]:
        motion_task_ids: set[str] = set()
        stalled_ids: set[str] = set()
        agent_processing_ids: set[str] = set()
        if task_filter in _MOTION_FILTERS:
            current_turn_tasks = self._cached_tasks(statuses=_ACTIVE_TURN_STATUSES)
            motion_task_ids.update(task.id for task in current_turn_tasks)
            frames = self._active_turn_frames(now, engine_running=self.engine_up())
            stalled_ids.update(item.task_id for item in frames.tasks if item.stalled)
            agent_processing_ids.update(
                item.task_id for item in frames.tasks if item.agent_processing
            )
        return motion_task_ids, stalled_ids, tuple(sorted(stalled_ids)), agent_processing_ids

    def _active_turn_frames(self, now: datetime, *, engine_running: bool) -> MotionFrame:
        return self._tick_memo.get(
            ("active_turn_frames", engine_running),
            lambda: self._motion.frames(
                tuple(self._cached_tasks(statuses=_ACTIVE_TURN_STATUSES)),
                now,
                engine_running=engine_running,
            ),
        )

    def _quota_wait_task_ids(self, now: datetime) -> tuple[str, ...]:
        return tuple(
            task.id
            for task in self._cached_tasks(statuses=_ACTIVE_TURN_STATUSES)
            if self._running_quota_wait(task, now) is not None
        )

    def _quota_waits(self, now: datetime) -> _QuotaWaits:
        return self._tick_memo.get("quota_waits", lambda: self._build_quota_waits(now))

    def _build_quota_waits(self, now: datetime) -> _QuotaWaits:
        blocked_until = {block.agent: block.until for block in self._active_blocks(now)}
        settings = self._settings.current()
        task_ids: set[str] = set()
        waiting: Counter[str] = Counter()
        for task in self._cached_tasks(statuses=_ACTIVE_TURN_STATUSES):
            running_wait = self._running_quota_wait(task, now)
            if running_wait is not None:
                task_ids.add(task.id)
                waiting[running_wait.block.agent] += 1
        for task in self._cached_tasks(statuses=(Status.QUEUED,)):
            if task.wait_reason == WaitReason.QUOTA.value:
                task_ids.add(task.id)
                waiting.update(quota_blocked_agents(task_agents(task, settings), blocked_until))
        return _QuotaWaits(task_ids=frozenset(task_ids), waiting_by_agent=waiting)

    def _queued_quota_block(self, task: Task, now: datetime) -> AgentBlock | None:
        if task.status is not Status.QUEUED or task.wait_reason != WaitReason.QUOTA.value:
            return None
        blocks = {block.agent: block for block in self._active_blocks(now)}
        agent = quota_blocking_agent(
            task_agents(task, self._settings.current()),
            {name: block.until for name, block in blocks.items()},
        )
        return None if agent is None else blocks[agent]

    def _active_blocks(self, now: datetime) -> list[AgentBlock]:
        return self._tick_memo.get("active_blocks", lambda: self._state.active_agent_blocks(now))

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
        agent_processing_ids: set[str],
    ) -> TaskBrowserView:
        page_stalled_ids = set(stalled_ids)
        page_agent_processing_ids = set(agent_processing_ids)

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
            frames = self._motion.frames(tuple(framed_tasks), now, engine_running=self.engine_up())
            page_stalled_ids.update(item.task_id for item in frames.tasks if item.stalled)
            page_agent_processing_ids.update(
                item.task_id for item in frames.tasks if item.agent_processing
            )
        open_decision_ids = page.open_decision_task_ids
        quota_wait_ids = self._quota_waits(now).task_ids
        rows = [
            self._task_browser_row(
                task,
                open_decision=task.id in open_decision_ids,
                diagnosed_stall=task.id in page_stalled_ids,
                agent_processing_ids=page_agent_processing_ids,
                quota_wait=task.id in quota_wait_ids,
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
                agent_processing_ids=page_agent_processing_ids,
                quota_wait=selected_task.id in quota_wait_ids,
                now=now,
                task_filter=task_filter,
            )
        return TaskBrowserView(
            filter=task_filter,
            search=search,
            rows=tuple(rows),
            selected_task=selected_row,
            matching_count=page.matching_count,
            filtered_count=page.filtered_count,
            project_count=page.project_count,
            has_more=offset + len(rows) < page.matching_count,
        )

    def _task_browser_row(
        self,
        task: Task,
        *,
        open_decision: bool,
        diagnosed_stall: bool,
        agent_processing_ids: set[str],
        quota_wait: bool,
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
            agent_processing=(
                task.id in agent_processing_ids and not open_decision and not diagnosed_stall
            ),
            quota_wait=quota_wait,
        )

    def escalations(self) -> list[EscalationPanelView]:
        """Build every durable open decision, retaining its row when evidence is unreadable."""
        decisions = self._decision_views(self._now(), self._queries.list_escalations())
        return list(decisions.values())

    def _decision_views(
        self, now: datetime, items: list[EscalationView]
    ) -> dict[str, EscalationPanelView]:
        open_ids = {item.escalation.id for item in items}
        self._structured_escalation_cache = {
            escalation_id: cached
            for escalation_id, cached in self._structured_escalation_cache.items()
            if escalation_id in open_ids
        }
        pulse = self._motion.frames((), now, escalations_open=bool(items)).escalation_red
        tasks = {task.id: task for task in self._cached_tasks()}
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
                answer_available=any(
                    option.label is ResolutionKind.ANSWER for option in escalation.options
                ),
                evidence_error="; ".join(evidence_errors) if evidence_errors else None,
                reason=escalation.reason,
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
        now = self._now()
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
        return self._tick_memo.get("attention", self._build_attention)

    def _build_attention(self) -> AttentionView:
        now = self._now()
        tasks = {task.id: task for task in self._cached_tasks()}
        escalations = self._queries.list_escalations()
        decision_views = self._decision_views(now, escalations)
        group_reasons: dict[str | None, list[str]] = {}
        group_decisions: dict[str | None, list[EscalationPanelView]] = {}
        decision_order: dict[str | None, tuple[datetime, str]] = {}
        group_titles: dict[str | None, str] = {}

        for item in escalations:
            task_id = item.escalation.task_id
            group_decisions.setdefault(task_id, []).append(decision_views[item.escalation.id])
            task = tasks.get(task_id)
            group_titles[task_id] = task.title if task is not None else task_id
            order = (item.escalation.created_at, item.escalation.id)
            if task_id not in decision_order or order < decision_order[task_id]:
                decision_order[task_id] = order

        frames = self._active_turn_frames(now, engine_running=True)
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
        """Return open fault cards in stable order, those needing the Operator first."""
        settings = self._settings.current().orchestration
        policy = RetryPolicy(
            backoff_initial_seconds=settings.faults.backoff_initial_seconds,
            backoff_max_seconds=settings.faults.backoff_max_seconds,
            close_attempts=settings.network.pr_close_attempts,
        )
        log_path = self._queries.engine_log_location()
        state_path = self._queries.state_database_location()
        ordered = sorted(self._cached_faults(), key=lambda fault: fault.first_seen)
        views = [
            engine_fault_view(fault, policy, log_path=log_path, state_path=state_path)
            for fault in ordered
        ]
        return tuple(sorted(views, key=lambda view: not view.needs_action))

    async def live_work(self, task_id: str) -> TaskWorkSample | None:
        """Sample the live work of a task whose Results still change, or None when there is none."""
        task = self._state.get_task(task_id)
        if task is None or task.repo is None or not tracks_live_work(task.status):
            return None
        return await self._work.sample(
            task.id, self._queries.task_worktree_path(task.id), task.repo.base_ref
        )

    async def task_detail(
        self,
        task_id: str,
        *,
        relationship_offset: int = 0,
    ) -> TaskDetailView | None:
        """Project current task state and its bounded, recorded evidence for the detail view."""
        detail = await self._queries.get_task_detail(task_id)
        if detail is None:
            return None
        live = await self.live_work(task_id)
        try:
            signal_page = await self.task_timeline(
                task_id,
                cursor=None,
                role=None,
                turn=None,
                category=TaskTimelineCategory.SIGNAL.value,
            )
        except (ShikiruError, OSError, ValueError, TypeError) as error:
            signal_page = _timeline_read_error(error, role=None, turn=None)
        if signal_page is None:
            return None
        latest_signal = _latest_signal_observation(signal_page)
        task = detail.task
        now = self._now()
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
        try:
            history_events = self._state.task_timeline_events(
                task_id,
                limit=TASK_MODEL_HISTORY_EVENT_LIMIT,
                kinds=TASK_MODEL_HISTORY_EVENTS,
            )
            history_error = False
        except ShikiruError, OSError, ValueError:
            history_events = []
            history_error = True
        stored_relationships = self._state.task_relationships(
            task.id,
            offset=relationship_offset,
        )
        relationships = (
            _task_relationships_view(stored_relationships)
            if stored_relationships is not None
            else None
        )
        task_agent_usage = tuple(self.usage(task_id))
        task_frame = self._motion.frames((task,), now, engine_running=self.engine_up()).tasks[0]
        latest_verdict = next(
            (item.data for item in reversed(detail.reports) if isinstance(item.data, Verdict)),
            None,
        )
        implementation = next(
            (
                item.data
                for item in reversed(detail.reports)
                if item.source == AGENT_REPORT_SOURCE
                and isinstance(item.data, ImplementationReport)
            ),
            None,
        )
        workflow = self._task_workflow(task, detail.events, detail.reports)
        pull_request = self._pull_request_line(task.id, task_done=task.status is Status.DONE)
        reported = reported_tests(implementation.tests) if implementation is not None else None
        blockers, blocker_evidence = self._task_blockers(task, relationships, ownership, now)
        return TaskDetailView(
            task_id=task.id,
            title=task.title,
            status=task.status.value,
            status_reason=task.status_reason.value if task.status_reason is not None else None,
            mode=task.mode.value if task.mode is not None else None,
            complexity=task.complexity,
            step=_step_label(task.status, task.step, task.iteration),
            acting_role=task_acting_role(task).value,
            iteration=task.iteration,
            holds=tuple(sorted(hold.value for hold in task.holds)),
            wait_reason=task.wait_reason,
            ownership=ownership,
            request=task.request,
            acceptance_criteria=_acceptance_criteria(triage, task.request),
            progress=self._strip_with_motion(task, task_frame, self._motion.current_cost(task)),
            latest_report=latest_report,
            latest_verdict=verdict,
            branch=task.repo.branch if task.repo is not None else None,
            worktree_path=str(self._queries.task_worktree_path(task.id)),
            window_id=task.window_id,
            sessions=self._with_context_levels(
                project_task_sessions(
                    detail.sessions,
                    history_events,
                    history_truncated=len(history_events) == TASK_MODEL_HISTORY_EVENT_LIMIT,
                    history_error=history_error,
                )
            ),
            relationships=relationships,
            blockers=blockers,
            blocker_evidence=blocker_evidence,
            submitted_age=_age(now - task.created_at),
            requeued_age=_age(now - task.requeued_at) if task.requeued_at is not None else None,
            usage=_recorded_usage(detail.sessions),
            signal_observed=task_frame.signal_observed,
            agent_usage=task_agent_usage,
            priority=task.priority,
            actual_stage=task.step.value if task.step is not None else None,
            actual_stage_started_at=task_frame.actual_step_started_at,
            stage_ended_at=task.finished_at,
            milestones=_task_milestone_views(workflow),
            latest_signal=(latest_signal.summary if latest_signal is not None else None),
            latest_signal_at=(
                _timeline_timestamp(latest_signal.timestamp)
                if latest_signal is not None and latest_signal.timestamp is not None
                else None
            ),
            max_iterations=self._settings.current().orchestration.max_iterations,
            started_at=task.started_at,
            escalation_remaining=escalation_remaining(
                task.counters.rejections_on_rung,
                self._settings.current().roles.implementer.escalate_after,
                can_escalate=move_up(task, Role.IMPLEMENTER, self._settings.current()) is not None,
            ),
            verdict_counts=(verdict_counts(latest_verdict) if latest_verdict is not None else None),
            last_words=task_frame.last_words,
            results=self._task_results(
                task,
                detail,
                reported,
                pull_request,
                verdict_risks=_current_verdict_risks(task, detail.reports),
                live=live,
            ),
            decisions=self._task_decisions(task.id),
            can_take_over=self._can_take_over(task),
        )

    def _can_take_over(self, task: Task) -> bool:
        availability = self._queries.task_action_availability(task.id, task_acting_role(task))
        return any(
            item.action is OperatorTaskAction.TAKE_OVER and item.enabled for item in availability
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
            limit=self._settings.current().dashboard.timeline_page_size,
            cursor=cursor,
            role=role,
            turn=turn,
            category=category,
            scan_limit=self._settings.current().dashboard.timeline_scan_rows,
        )
        if timeline is None:
            return None
        return TaskTimelinePageView(
            entries=tuple(
                TaskTimelineEntryView(
                    identity=item.identity,
                    timestamp=item.timestamp,
                    source=item.source,
                    category=item.category,
                    summary=item.summary,
                    phrase=item.phrase,
                    glyph=item.glyph,
                    tone=item.tone,
                    detail=item.detail,
                    role=item.role,
                    turn=item.turn,
                )
                for item in timeline.entries
            ),
            older_cursor=timeline.older_cursor,
            has_older=timeline.has_older,
        )

    def _with_context_levels(
        self, sessions: tuple[TaskSessionView, ...]
    ) -> tuple[TaskSessionView, ...]:
        titles = self._settings.current().dashboard.titles
        return tuple(
            replace(
                session,
                context_level=usage_level(
                    session.context_used_pct,
                    warn_pct=titles.context_warn_pct,
                    alert_pct=titles.context_alert_pct,
                ),
            )
            if session.context_used_pct is not None
            else session
            for session in sessions
        )

    def _task_blockers(
        self,
        task: Task,
        relationships: TaskRelationshipsView | None,
        ownership: tuple[str, ...],
        now: datetime,
    ) -> tuple[tuple[TaskBlockerView, ...], BlockerEvidenceView | None]:
        found = self._found_blockers(task, relationships, ownership, now)
        if not found:
            return (), None
        recorded = (
            (f"{_RECORDED_REASON}{GROUP_SEPARATOR}{task.wait_reason}",) if task.wait_reason else ()
        )
        blockers = tuple(
            TaskBlockerView(
                kind=item.kind,
                headline=item.parts.headline,
                details=item.parts.details,
                related_tasks=item.parts.related,
                quota_wait=None if item.block is None else self._quota_wait_view(item.block, now),
            )
            for item in found
        )
        evidence = BlockerEvidenceView(
            quotas=tuple(quota for item in found for quota in item.parts.quotas),
            observed_at=tuple(instant for item in found for instant in item.parts.observed),
            notes=tuple(note for item in found for note in item.parts.notes),
            raw=(*recorded, *(line for item in found for line in item.parts.raw)),
        )
        has_evidence = bool(
            evidence.quotas or evidence.observed_at or evidence.notes or evidence.raw
        )
        return blockers, evidence if has_evidence else None

    def _found_blockers(
        self,
        task: Task,
        relationships: TaskRelationshipsView | None,
        ownership: tuple[str, ...],
        now: datetime,
    ) -> tuple[_FoundBlocker, ...]:
        wait_reason = task.wait_reason
        found: list[_FoundBlocker] = []
        quota = self._quota_blocker(task, now)
        if quota is not None:
            found.append(quota)
        elif wait_reason not in (None, WaitReason.PAUSED.value):
            found.append(
                _FoundBlocker(wait_reason, self._reason_parts(task, wait_reason, relationships))
            )
        if Hold.PAUSED in task.holds or wait_reason == WaitReason.PAUSED.value:
            raw = (_PAUSE_HOLD_EVIDENCE,) if Hold.PAUSED in task.holds else ()
            found.append(
                _FoundBlocker(
                    Hold.PAUSED.value,
                    _BlockerParts(_PAUSED_HEADLINE, (_PAUSE_HOLD_DETAIL,), raw=raw),
                )
            )
        if Hold.INTERVENTION in task.holds:
            found.append(_FoundBlocker(Hold.INTERVENTION.value, _intervention_parts(ownership)))
        return tuple(found)

    def _quota_blocker(self, task: Task, now: datetime) -> _FoundBlocker | None:
        if task.wait_reason == WaitReason.QUOTA.value:
            quota_block = self._queued_quota_block(task, now)
            parts = self._queued_quota_parts(task, quota_block, now)
            return _FoundBlocker(WaitReason.QUOTA.value, parts, quota_block)
        if task.wait_reason is not None:
            return None
        running_wait = self._running_quota_wait(task, now)
        if running_wait is None:
            return None
        parts = self._running_quota_parts(task, running_wait, now)
        return _FoundBlocker(WaitReason.QUOTA.value, parts, running_wait.block)

    def _quota_wait_view(self, quota_block: AgentBlock, now: datetime) -> QuotaWaitView:
        max_wait = timedelta(seconds=self._settings.current().orchestration.timeouts.max_quota_wait)
        changes = (
            quota_block.until,
            quota_block.until - max_wait,
            *(block.until for block in self._active_blocks(now)),
        )
        return QuotaWaitView(
            agent=quota_block.agent,
            until=quota_block.until,
            refresh_at=next_quota_reload(now, changes),
        )

    def _reason_parts(
        self,
        task: Task,
        wait_reason: str,
        relationships: TaskRelationshipsView | None,
    ) -> _BlockerParts:
        match wait_reason:
            case WaitReason.DEPENDENCY.value:
                return self._dependency_parts(relationships)
            case WaitReason.SLOT.value:
                return self._slot_parts()
            case WaitReason.RESOURCES.value:
                return self._resource_parts()
            case WaitReason.PR_OPEN.value:
                return self._pull_request_parts(task.id)
            case _:
                return _BlockerParts(f"{_UNRECORDED_REASON}{GROUP_SEPARATOR}{wait_reason}")

    def _dependency_parts(self, relationships: TaskRelationshipsView | None) -> _BlockerParts:
        if relationships is None:
            return _BlockerParts(_DEPENDENCY_HEADLINE, (_DEPENDENCY_UNAVAILABLE,))
        related = relationships.blocking_dependencies
        count = relationships.blocking_dependency_count
        if not related:
            return _BlockerParts(_DEPENDENCY_HEADLINE, (_dependency_absence(relationships),))
        headline = GROUP_SEPARATOR.join(
            (_DEPENDENCY_HEADLINE, f"{related[0].task_id} {related[0].status}")
        )
        if count > 1:
            headline = GROUP_SEPARATOR.join((headline, f"{count} unfinished"))
        shown_from = relationships.page_offset + 1
        shown_to = relationships.page_offset + len(related)
        details = (
            (f"Showing {shown_from} to {shown_to} of {count} unfinished dependencies.",)
            if count > shown_to
            else ()
        )
        return _BlockerParts(headline, details)

    def _queued_quota_parts(
        self,
        task: Task,
        quota_block: AgentBlock | None,
        now: datetime,
    ) -> _BlockerParts:
        settings = self._settings.current()
        agents = task_agents(task, settings)
        blocked = {block.agent: block.until for block in self._active_blocks(now)}
        quotas = tuple(
            self._quota_evidence(agent, blocked[agent], now)
            for agent in quota_blocked_agents(agents, blocked)
        )
        raw = tuple(
            line
            for agent in dict.fromkeys((agents.leader, agents.implementer))
            for line in (
                _account_raw(agent, self._state.agent_block(agent), now),
                *self._provider_quota_raw(agent),
            )
        )
        if quota_block is None:
            return _BlockerParts(_QUOTA_HEADLINE, quotas=quotas, raw=raw)
        wait_limit = now + timedelta(seconds=settings.orchestration.timeouts.max_quota_wait)
        return _quota_parts(
            quota_block.until,
            quotas=quotas,
            raw=raw,
            budget_ends=wait_limit,
            moves=quota_wait_reroutes(task, settings, blocked, wait_limit),
            exhausted_text=_QUOTA_WAITS_NO_OTHER_AGENT,
        )

    def _running_quota_parts(
        self, task: Task, running_wait: _RunningQuotaWait, now: datetime
    ) -> _BlockerParts:
        settings = self._settings.current()
        until = running_wait.block.until
        blocked_agents = frozenset(block.agent for block in self._active_blocks(now))
        return _quota_parts(
            until,
            quotas=(running_wait.quota,),
            raw=running_wait.raw,
            budget_ends=running_wait.budget_ends,
            moves=running_quota_wait_moves(
                task, settings, until, running_wait.budget_ends, blocked_agents
            ),
            exhausted_text=_QUOTA_ESCALATES_AT_LIMIT,
        )

    def _running_quota_wait(self, task: Task, now: datetime) -> _RunningQuotaWait | None:
        if task.status not in _ACTIVE_TURN_STATUSES or task.step is None:
            return None
        role = task_acting_role(task)
        settings = self._settings.current()
        agent = pick_rung(task, role, settings).agent
        quota_events = current_quota_wait_events(task, role, agent, self._state.latest_events)
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
            else QUOTA_NOT_REPORTED
            if reset_at is None and _RESET_AT_FIELD in failure.data
            else _UNAVAILABLE
        )
        raw = (
            f"Current turn quota wait · {role.value} · {agent}",
            f"{_RECORDED_QUOTA_RESET} · {reset_text}",
            f"Task quota-block event · {quota_block.ts.isoformat()}",
            _account_raw(agent, block, now),
            *self._provider_quota_raw(agent),
        )
        budget = timedelta(seconds=settings.orchestration.timeouts.max_quota_wait)
        return _RunningQuotaWait(
            block=block,
            budget_ends=quota_block.ts + budget,
            quota=self._quota_evidence(agent, block.until, now),
            raw=raw,
        )

    def _quota_evidence(self, agent: str, until: datetime, now: datetime) -> QuotaEvidenceView:
        windows = self._state.list_quotas((agent,)).get(agent, [])
        return QuotaEvidenceView(
            agent=agent,
            readings=tuple(
                QuotaReadingView(
                    window=window.name,
                    used_pct=window.used_pct,
                    resets_at=window.resets_at,
                    observed_at=window.observed_at,
                )
                for window in windows
                if window.resets_at is None or window.resets_at > now
            ),
            retry_note=engine_retry_note(until, windows, now),
            has_recorded_readings=bool(windows),
        )

    def _provider_quota_raw(self, agent: str) -> tuple[str, ...]:
        windows = self._state.list_quotas((agent,)).get(agent, [])
        if not windows:
            return (f"Provider quota observation · {agent} · {_UNAVAILABLE}",)
        return tuple(
            f"Provider quota · {agent} {window.name} · reset {_raw_reset(window.resets_at)} · "
            f"observed {window.observed_at.isoformat()}"
            for window in windows
        )

    def _slot_parts(self) -> _BlockerParts:
        occupied = [
            task
            for task in self._cached_tasks(statuses=(Status.RUNNING, Status.INTEGRATING))
            if task.slot is not None
        ]
        maximum = self._settings.current().orchestration.max_parallel
        headline = GROUP_SEPARATOR.join((_SLOT_HEADLINE, f"{len(occupied)}/{maximum} busy"))
        return _BlockerParts(
            headline,
            (_SLOT_DETAIL,),
            related=tuple(_task_reference_view(item) for item in occupied),
        )

    def _resource_parts(self) -> _BlockerParts:
        sample = self._sources.resource_sample()
        if sample is None or sample.sampled_at is None:
            sample_line = "Dashboard resource sample · unavailable"
            observed: tuple[datetime, ...] = ()
        else:
            sample_line = (
                "Dashboard resource sample · "
                f"host CPU {sample.host_cpu_pct:.1f}% · "
                f"free RAM {sample.free_ram_gb:.1f} GiB"
            )
            observed = (sample.sampled_at,)
        return _BlockerParts(
            _RESOURCES_HEADLINE,
            (_RESOURCES_DETAIL,),
            notes=(sample_line, _RESOURCE_SAMPLE_DISCLAIMER),
            observed=observed,
        )

    def _new_task_titles(self, scope: Sequence[Event]) -> dict[str, str]:
        found = {
            task_id: self._state.get_task(task_id)
            for task_id in new_task_ids([event.data for event in scope])
        }
        return {task_id: task.title for task_id, task in found.items() if task is not None}

    def _task_results(
        self,
        task: Task,
        detail: TaskDetail,
        reported: ReportedTests | None,
        pull_request: PullRequestLine | None,
        *,
        verdict_risks: tuple[str, ...],
        live: TaskWorkSample | None,
    ) -> TaskResults:
        done = self._state.latest_events(1, kinds=(EventKind.TASK_DONE,), task_id=task.id)
        scope = self._state.latest_events(
            RESULTS_PLAN_CHANGE_LIMIT, kinds=(EventKind.SCOPE_CHANGE,), task_id=task.id
        )
        return derive_task_results(
            task.status,
            branch=task.repo.branch if task.repo is not None else None,
            base=task.repo.base if task.repo is not None else None,
            done=done[0].data if done else None,
            report_commits=_latest_report_commits(detail.reports),
            facts=detail.recorded_facts,
            tests=reported,
            pull_request=pull_request,
            scope_events=[event.data for event in scope],
            titles=self._new_task_titles(scope),
            verdict_risks=verdict_risks,
            plan_summary=_latest_plan_summary(detail.reports),
            commit_cap=self._settings.current().dashboard.results_commits,
            live=live.work if live is not None else None,
            live_error=live.error if live is not None else None,
        )

    def _task_workflow(
        self,
        task: Task,
        window: Sequence[Event],
        reports: Sequence[ReportRow],
    ) -> WorkflowProgress:
        recorded = self._state.latest_events(
            WORKFLOW_EVENT_LIMIT, kinds=WORKFLOW_EVENTS, task_id=task.id
        )
        events = sorted(
            {event.id: event for event in (*recorded, *window)}.values(), key=attrgetter("id")
        )
        agent_reports = tuple(
            WorkflowReport(turn=report.turn, kind=report.kind, data=report.data)
            for report in reports
            if report.source == AGENT_REPORT_SOURCE
        )
        return project_workflow(
            task,
            events,
            agent_reports,
            event_window_complete=len(recorded) < WORKFLOW_EVENT_LIMIT,
        )

    def _task_decisions(self, task_id: str) -> TaskDecisions:
        return derive_decisions(
            self._state.latest_events(DECISION_EVENT_LIMIT, kinds=DECISION_EVENTS, task_id=task_id)
        )

    def _pull_request_line(self, task_id: str, *, task_done: bool) -> PullRequestLine | None:
        events = self._state.latest_events(
            1,
            kinds=(EventKind.PR_OPENED, EventKind.PR_WITHDRAWN),
            task_id=task_id,
        )
        return pull_request_line(events[0].data, task_done=task_done) if events else None

    def _pull_request_parts(self, task_id: str) -> _BlockerParts:
        events = self._state.latest_events(
            1,
            kinds=(EventKind.PR_OPENED, EventKind.PR_WITHDRAWN),
            task_id=task_id,
        )
        if not events:
            return _BlockerParts(_PR_HEADLINE, (_PR_NO_EVENT, _PR_CI_NOTE))
        event = events[0]
        evidence: list[str] = []
        url = event.data.get("url")
        state = event.data.get("state")
        if isinstance(url, str):
            evidence.append(f"Recorded PR URL · {url}")
        if isinstance(state, str):
            evidence.append(f"Latest recorded PR state · {state} · {event.ts.isoformat()}")
            details = (f"Pull request state · {state}", _PR_CI_NOTE)
        else:
            evidence.append(
                f"Latest recorded PR event · {event.kind.value} · {event.ts.isoformat()}"
            )
            details = (_PR_CI_NOTE,)
        return _BlockerParts(_PR_HEADLINE, details, raw=tuple(evidence))

    def dialogue(self, task_id: str | None = None) -> list[DialogueBubbleView]:
        """Build recent project or task decisions and activity in event order."""
        return self._dialogue_view(task_id)

    def usage(self, task_id: str | None = None) -> list[AgentUsageView]:
        """Build project usage or usage recorded for one task."""
        now = self._now()
        self._refresh_usage_cache_version()
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
                    cost_basis=item.cost_basis if item is not None else COST_BASIS_UNKNOWN,
                    quotas=windows,
                    run_count=item.run_count if item is not None else 0,
                    usage_reported_runs=item.usage_reported_runs if item is not None else 0,
                    legacy_token_runs=item.legacy_token_runs if item is not None else 0,
                    billed_cost_usd=item.billed_cost_usd if item is not None else None,
                    billed_cost_runs=item.billed_cost_runs if item is not None else 0,
                    api_equivalent_cost_usd=(
                        item.api_equivalent_cost_usd if item is not None else None
                    ),
                    api_equivalent_cost_runs=(
                        item.api_equivalent_cost_runs if item is not None else 0
                    ),
                    unknown_cost_runs=item.unknown_cost_runs if item is not None else 0,
                )
            )
        return result

    def quota_blocks(self) -> list[QuotaBlockView]:
        """Build one banner per agent whose quota block is still in effect."""
        now = self._now()
        waiting = self._quota_waits(now).waiting_by_agent
        return [
            QuotaBlockView(
                agent=block.agent,
                banner=quota_banner(
                    quota_wait_description(block.agent, to_local(block.until), to_local(now)),
                    waiting[block.agent],
                ),
            )
            for block in self._active_blocks(now)
        ]

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
                    queued_at=(entry.timestamp if entry.status is CommandStatus.PENDING else None),
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
        view = await self._sources.resources()
        for listener in tuple(self._resource_listeners):
            listener()
        return view

    def on_resources_refreshed(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Call `callback` after every `resources()` call; return the unsubscribe function."""
        self._resource_listeners.append(callback)
        return partial(self._resource_listeners.remove, callback)

    def resource_sample(self) -> ResourceView | None:
        """Return the latest resource observation without triggering a new sample."""
        return self._sources.resource_sample()

    async def containers(self) -> DockerView:
        """Return the current Docker sample."""
        return await self._sources.containers()

    async def disk(self) -> DiskView:
        """Return the disk sample with its free-space level under the configured thresholds."""
        view = await self._sources.disk()
        if view.free_bytes is None or view.total_bytes is None:
            return view
        level = free_space_level(
            view.free_bytes,
            view.total_bytes,
            warn_pct=self._settings.current().dashboard.disk_warn_free_pct,
            alert_pct=self._settings.current().dashboard.disk_alert_free_pct,
        )
        return replace(view, free_level=level)

    def _strip_with_motion(
        self, task: Task, motion: TaskMotionFrame, cost_usd: float | None
    ) -> TaskStripData:
        data = task_strip_data(task, self._settings.current())
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
        return replace(
            data,
            panels=tuple(panels),
            stamp=stamp,
            actual_step_started_at=motion.actual_step_started_at,
            last_signal_at=motion.last_signal_at,
            last_signal=motion.last_signal,
            last_words=motion.last_words,
        )

    def _refresh_usage_cache_version(self) -> None:
        version = self._state.data_version()
        if version != self._usage_version:
            self._usage_version = version
            self._project_usage_cache = None
            self._project_usage_view_cache = None
            self._task_usage_cache.clear()


def _publication_state(repo: RepoStatus) -> PublicationState:
    if repo.unpushed is None or repo.unpulled is None:
        return PublicationState.UNAVAILABLE
    if repo.unpulled:
        return PublicationState.NOT_IN_SYNC
    if repo.unpushed:
        return PublicationState.AWAITING_PUSH
    return PublicationState.IN_SYNC


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


def _intervention_parts(ownership: tuple[str, ...]) -> _BlockerParts:
    owners = ", ".join(ownership)
    takeover = (f"{_RECORDED_TAKEOVER}{GROUP_SEPARATOR}{owners}",) if ownership else ()
    return _BlockerParts(
        _INTERVENTION_HEADLINE,
        (
            f"{_OWNERSHIP_HOLD_LABEL}{GROUP_SEPARATOR}{owners or _NO_OWNER_RECORDED}",
            _HAND_BACK_DETAIL,
        ),
        raw=(_INTERVENTION_HOLD_EVIDENCE, *takeover),
    )


def _quota_parts(
    until: datetime,
    *,
    quotas: tuple[QuotaEvidenceView, ...],
    raw: tuple[str, ...],
    budget_ends: datetime,
    moves: bool,
    exhausted_text: str,
) -> _BlockerParts:
    return _BlockerParts(
        headline=_QUOTA_HEADLINE,
        details=(
            _quota_reroute_text(
                moves=moves,
                exceeds=until > budget_ends,
                exhausted_text=exhausted_text,
            ),
        ),
        quotas=quotas,
        raw=raw,
    )


def _account_raw(agent: str, block: AgentBlock | None, now: datetime) -> str:
    if block is None:
        return f"Scheduler quota account block · {agent} · not recorded"
    activity = "active" if block.until > now else "expired"
    return (
        f"Scheduler quota account block · {agent} · {activity} · {block.reason} · "
        f"retry/probe deadline {block.until.isoformat()}"
    )


def _raw_reset(resets_at: datetime | None) -> str:
    return resets_at.isoformat() if resets_at is not None else QUOTA_NOT_REPORTED


def _quota_reroute_text(*, moves: bool, exceeds: bool, exhausted_text: str) -> str:
    if moves:
        return _QUOTA_REROUTED
    return exhausted_text if exceeds else _QUOTA_WAITS_WITHIN_LIMIT


def _dependency_absence(relationships: TaskRelationshipsView) -> str:
    count = relationships.blocking_dependency_count
    if count == 0:
        return _NO_UNFINISHED_DEPENDENCY
    if relationships.page_offset >= count:
        return (
            f"Unfinished dependency evidence is on an earlier relationship page ({count} recorded)."
        )
    return _DEPENDENCIES_BEYOND_PAGE


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


def _latest_signal_observation(page: TaskTimelinePageView) -> TaskTimelineEntryView | None:
    agent_entries = [
        entry for entry in page.entries if entry.source == TaskTimelineSource.AGENT.value
    ]
    signal_entries = [entry for entry in agent_entries if entry.identity.startswith("signal:")]
    errors = [
        entry
        for entry in agent_entries
        if entry.identity.startswith(("unavailable:", TASK_TIMELINE_READ_ERROR_ID))
    ]
    timed = [
        (timestamp, entry)
        for entry in signal_entries
        if (timestamp := _timeline_timestamp(entry.timestamp)) is not None
    ]
    if len(timed) < len(signal_entries):
        return next(
            entry for entry in signal_entries if _timeline_timestamp(entry.timestamp) is None
        )
    if timed:
        return max(timed, key=lambda item: item[0])[1]
    return errors[0] if errors else None


def _timeline_timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError:
        return None
    return timestamp if timestamp.tzinfo is not None else None


def _task_milestone_views(workflow: WorkflowProgress) -> tuple[TaskMilestoneView, ...]:
    return tuple(
        TaskMilestoneView(stage=_stage_label(item.stage), state=item.state.value)
        for item in workflow.milestones
    )


def _stage_label(step: Step) -> str:
    return "Merge" if step is Step.INTEGRATE else step.value.title()


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
    if seconds >= SECONDS_PER_DAY:
        return format_duration(seconds)
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


def _latest_report_commits(reports: Sequence[ReportRow]) -> list[Commit]:
    empty: list[Commit] = []
    return next(
        (
            item.data.commits
            for item in reversed(reports)
            if item.source == AGENT_REPORT_SOURCE
            and isinstance(item.data, ImplementationReport | PlanReport)
        ),
        empty,
    )


def _latest_plan_summary(reports: Sequence[ReportRow]) -> str | None:
    return next(
        (
            item.data.summary
            for item in reversed(reports)
            if item.source == AGENT_REPORT_SOURCE and isinstance(item.data, PlanReport)
        ),
        None,
    )


def _acceptance_criteria(triage: TriageReport | None, request: str) -> tuple[str, ...]:
    if triage is not None and triage.acceptance_criteria:
        return tuple(triage.acceptance_criteria)
    return acceptance_criteria_from_request(request)


def _current_verdict_risks(task: Task, reports: Sequence[ReportRow]) -> tuple[str, ...]:
    for row in reversed(reports):
        if isinstance(row.data, Verdict):
            current = turn_iteration(row.turn) == task.iteration
            return tuple(row.data.residual_risks) if current else ()
    return ()


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
    elif isinstance(report, FeedbackAnalysis):
        summary = report.summary
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


def _recorded_usage(runs: Sequence[Run]) -> RecordedUsageView:
    usage = aggregate_usage(runs)
    api_runs = [run for run in runs if run.billing_mode == BILLING_MODE_API]
    api_usage = aggregate_usage(api_runs)
    billed_sessions = sum(item.billed_cost_runs for item in usage)
    api_billed_sessions = sum(item.billed_cost_runs for item in api_usage)
    api_equivalent_sessions = sum(item.api_equivalent_cost_runs for item in usage)
    return RecordedUsageView(
        session_count=len(runs),
        input_tokens=sum(item.input_tokens for item in usage),
        output_tokens=sum(item.output_tokens for item in usage),
        cache_read_tokens=sum(item.cache_read_tokens for item in usage),
        cache_write_tokens=sum(item.cache_write_tokens for item in usage),
        reported_sessions=sum(run.usage_reported is True for run in runs),
        legacy_token_sessions=sum(
            run.usage_reported is None and has_positive_token_evidence(run) for run in runs
        ),
        unknown_token_sessions=sum(
            run.usage_reported is not True
            and not (run.usage_reported is None and has_positive_token_evidence(run))
            for run in runs
        ),
        billed_cost_usd=_cost_total((item.billed_cost_usd for item in usage), billed_sessions),
        billed_cost_sessions=billed_sessions,
        api_billed_cost_usd=_cost_total(
            (item.billed_cost_usd for item in api_usage), api_billed_sessions
        ),
        api_billed_cost_sessions=api_billed_sessions,
        api_cost_unreported_sessions=len(api_runs) - api_billed_sessions,
        api_equivalent_cost_usd=_cost_total(
            (item.api_equivalent_cost_usd for item in usage), api_equivalent_sessions
        ),
        api_equivalent_cost_sessions=api_equivalent_sessions,
        unknown_billing_sessions=sum(run.billing_mode == BILLING_MODE_UNKNOWN for run in runs),
        subscription_sessions=sum(run.billing_mode == BILLING_MODE_SUBSCRIPTION for run in runs),
        subscription_agents=tuple(
            sorted({run.agent for run in runs if run.billing_mode == BILLING_MODE_SUBSCRIPTION})
        ),
    )


def _cost_total(amounts: Iterable[float | None], count: int) -> float | None:
    if count == 0:
        return None
    return sum(amount for amount in amounts if amount is not None)


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
        billed_cost_usd=item.billed_cost_usd,
        billed_cost_runs=item.billed_cost_runs,
        api_equivalent_cost_usd=item.api_equivalent_cost_usd,
        api_equivalent_cost_runs=item.api_equivalent_cost_runs,
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
                source=TaskTimelineSource.AGENT.value,
                category=TIMELINE_EVIDENCE_CATEGORY,
                summary=TASK_TIMELINE_READ_ERROR_SUMMARY,
                phrase=TASK_TIMELINE_READ_ERROR_SUMMARY,
                glyph=GLYPH_ATTENTION,
                tone=Tone.WARNING.value,
                detail=str(error),
                role=role,
                turn=turn,
            ),
        ),
        older_cursor=None,
        has_older=False,
    )
