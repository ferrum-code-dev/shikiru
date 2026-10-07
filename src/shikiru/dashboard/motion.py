"""Resolve bounded state and current-turn signal evidence for dashboard frames."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.domain.events import EventKind
from shikiru.domain.motion import (
    MotionStamp,
    MotionStampKind,
    MotionState,
    QuotaMotionState,
    TaskMotionState,
)
from shikiru.domain.motion import (
    frame as motion_frame,
)
from shikiru.domain.reports import CURRENT_TURN_LINK_NAME
from shikiru.domain.runs import RunStatus
from shikiru.domain.steps import ACTING_ROLE, Step
from shikiru.domain.tasks import Role, Status, Task
from shikiru.paths import run_dir
from shikiru.view_log import TURN_DIR_ITERATION_MARKER, TURN_DIR_SEPARATOR, view_log_path

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

    from shikiru.config.settings import Settings
    from shikiru.dashboard.activity import RoleViewTail
    from shikiru.store.reader import StateReader

_MILLISECONDS_PER_SECOND = 1000
_TASK_EVENT_KINDS = (
    EventKind.STEP_STARTED,
    EventKind.TURN_STARTED,
    EventKind.TURN_COMPLETED,
    EventKind.TURN_FAILED,
    EventKind.TURN_STOPPED,
    EventKind.VERDICT,
)


class DashboardMotion:
    """Build time-derived frames from cached events and incremental current-turn logs."""

    def __init__(
        self,
        state: StateReader,
        project_root: Path,
        settings: Settings,
        role_view_tail: RoleViewTail,
    ) -> None:
        """Use the read-only state, configuration and injected role-log tail."""
        self._state = state
        self._project_root = project_root
        self._settings = settings
        self._role_view_tail = role_view_tail
        self._event_version = -1
        self._task_events: dict[str, tuple[Event, ...]] = {}
        self._task_runs: dict[str, tuple[Run, ...]] = {}
        self._merge_events: tuple[Event, ...] = ()

    def recent_merges(self, now: datetime) -> list[Task]:
        """Return completed tasks whose merge stamp is still within its flash window."""
        self._sync_event_cache()
        if not self._settings.dashboard.animations:
            return []
        flash_seconds = (
            self._settings.dashboard.motion.stamp_flash_milliseconds / _MILLISECONDS_PER_SECOND
        )
        tasks: list[Task] = []
        for event in reversed(self._merge_events):
            age = (now - event.ts).total_seconds()
            if event.task_id is None or age < 0 or age >= flash_seconds:
                continue
            task = self._state.get_task(event.task_id)
            if task is not None and task.status is Status.DONE:
                tasks.append(task)
        return tasks

    def frames(
        self,
        tasks: tuple[Task, ...],
        now: datetime,
        *,
        quotas: tuple[QuotaMotionState, ...] = (),
        escalations_open: bool = False,
        engine_running: bool = True,
    ) -> MotionFrame:
        """Resolve task, quota and escalation frames at one injected time value."""
        self._sync_event_cache()
        return motion_frame(
            self._motion_state(
                tasks=tuple(self._task_motion(task) for task in tasks),
                quotas=quotas,
                escalations_open=escalations_open,
                engine_running=engine_running,
            ),
            now,
        )

    def current_cost(self, task: Task) -> float | None:
        """Return the acting run's latest cost when that run is known."""
        if task.step is None:
            return None
        run = self._run_for_role(task, ACTING_ROLE[task.step])
        return run.usage.cost_usd if run is not None else None

    def _sync_event_cache(self) -> None:
        version = self._state.data_version()
        if version == self._event_version:
            return
        self._event_version = version
        self._task_events.clear()
        self._task_runs.clear()
        self._merge_events = tuple(
            self._state.latest_events(
                self._settings.dashboard.motion.merge_event_limit,
                kinds=(EventKind.TASK_DONE,),
            )
        )

    def _task_motion(self, task: Task) -> TaskMotionState:
        events = self._events_for(task)
        step_started_at, actual_step_started_at, turn_started_at, turn_active, stamp = (
            self._event_timing(task, events)
        )
        (
            last_signal_at,
            last_signal,
            last_signal_identity,
            last_signal_role,
            last_signal_turn,
            tool_running,
            turn_active,
        ) = self._current_activity(task, turn_active, turn_started_at)
        held = self._is_held(task)
        stamp = self._merge_stamp(task.id, stamp)
        return TaskMotionState(
            task_id=task.id,
            step_started_at=step_started_at,
            actual_step_started_at=actual_step_started_at,
            turn_started_at=turn_started_at,
            last_signal_at=last_signal_at,
            last_signal=last_signal,
            last_signal_identity=last_signal_identity,
            last_signal_role=last_signal_role,
            last_signal_turn=last_signal_turn,
            tool_running=tool_running,
            turn_active=turn_active,
            held=held,
            ended_at=task.finished_at,
            stamp=stamp,
        )

    def _events_for(self, task: Task) -> tuple[Event, ...]:
        events = self._task_events.get(task.id)
        if events is None:
            events = tuple(
                self._state.latest_events(
                    self._settings.dashboard.motion.task_event_limit,
                    kinds=_TASK_EVENT_KINDS,
                    task_id=task.id,
                )
            )
            self._task_events[task.id] = events
        return events

    @staticmethod
    def _event_timing(
        task: Task, events: tuple[Event, ...]
    ) -> tuple[datetime, datetime | None, datetime | None, bool, MotionStamp | None]:
        step_started_at = task.started_at or task.created_at
        actual_step_started_at = None
        turn_started_at = None
        turn_active = False
        stamp: MotionStamp | None = None
        role = ACTING_ROLE.get(task.step) if task.step is not None else None
        latest_stage_entry = next(
            (event for event in reversed(events) if event.kind is EventKind.STEP_STARTED),
            None,
        )
        if latest_stage_entry is not None:
            step_value = latest_stage_entry.data.get("step")
            iteration = latest_stage_entry.data.get("iteration")
            try:
                entry_step = Step(step_value) if isinstance(step_value, str) else None
            except ValueError:
                entry_step = None
            if (
                task.step is not None
                and entry_step is task.step
                and type(iteration) is int
                and iteration == task.iteration
            ):
                actual_step_started_at = latest_stage_entry.ts
                step_started_at = latest_stage_entry.ts
        for event in events:
            if event.kind is EventKind.VERDICT:
                stamp = MotionStamp(
                    event.ts,
                    MotionStampKind.VERDICT,
                    str(event.data["decision"]),
                )
            if role is None or task.step is None:
                continue
            matching_turn = (
                event.data.get("step") == task.step.value
                and event.data.get("role") == role.value
                and event.data.get("iteration") == task.iteration
            )
            if event.kind is EventKind.TURN_STARTED and matching_turn:
                turn_started_at = event.ts
                turn_active = True
            elif (
                event.kind in (EventKind.TURN_COMPLETED, EventKind.TURN_STOPPED) and matching_turn
            ) or (
                event.kind is EventKind.TURN_FAILED and event.data.get("step") == task.step.value
            ):
                turn_started_at = None
                turn_active = False
        return step_started_at, actual_step_started_at, turn_started_at, turn_active, stamp

    def _current_activity(
        self, task: Task, turn_active: bool, turn_started_at: datetime | None
    ) -> tuple[datetime | None, str | None, str | None, str | None, str | None, bool, bool]:
        role = ACTING_ROLE.get(task.step) if task.step is not None else None
        last_signal_at = None
        last_signal = None
        last_signal_identity = None
        last_signal_role = None
        last_signal_turn = None
        tool_running = False
        if role is None:
            return (
                last_signal_at,
                last_signal,
                last_signal_identity,
                last_signal_role,
                last_signal_turn,
                tool_running,
                False,
            )
        turn_name = self._current_turn_name(task)
        if turn_name is None:
            return (
                last_signal_at,
                last_signal,
                last_signal_identity,
                last_signal_role,
                last_signal_turn,
                tool_running,
                turn_active,
            )
        activity = self._role_view_tail.read(
            view_log_path(run_dir(self._project_root, task.id), role.value), turn_name
        )
        if activity is None or (
            turn_started_at is not None and activity.last_signal_at < turn_started_at
        ):
            return (
                last_signal_at,
                last_signal,
                last_signal_identity,
                last_signal_role,
                last_signal_turn,
                tool_running,
                turn_active,
            )
        last_signal_at = activity.last_signal_at
        last_signal = activity.last_signal
        last_signal_identity = f"signal:{role.value}:{activity.identity_offset}"
        last_signal_role = role.value
        last_signal_turn = activity.turn
        tool_running = activity.tool_running
        turn_active = turn_active and not activity.ended
        return (
            last_signal_at,
            last_signal,
            last_signal_identity,
            last_signal_role,
            last_signal_turn,
            tool_running,
            turn_active,
        )

    def _is_held(self, task: Task) -> bool:
        role = ACTING_ROLE.get(task.step) if task.step is not None else None
        run = self._run_for_role(task, role) if role is not None else None
        held = (
            bool(task.holds)
            or task.wait_reason is not None
            or task.status not in (Status.RUNNING, Status.INTEGRATING)
        )
        if run is not None and run.status is RunStatus.TAKEN_OVER:
            held = True
        return held

    def _merge_stamp(self, task_id: str, stamp: MotionStamp | None) -> MotionStamp | None:
        merge = next(
            (event for event in reversed(self._merge_events) if event.task_id == task_id), None
        )
        if merge is not None:
            return MotionStamp(merge.ts, MotionStampKind.MERGED)
        return stamp

    def _current_turn_name(self, task: Task) -> str | None:
        if task.step is None:
            return None
        role = ACTING_ROLE[task.step]
        link = run_dir(self._project_root, task.id) / role.value / CURRENT_TURN_LINK_NAME
        try:
            if not link.is_symlink():
                return None
            turn_name = link.readlink().name
        except FileNotFoundError:
            return None
        expected_suffix = (
            f"{TURN_DIR_SEPARATOR}{task.step.value}{TURN_DIR_ITERATION_MARKER}{task.iteration}"
        )
        return turn_name if turn_name.endswith(expected_suffix) else None

    def _run_for_role(self, task: Task, role: Role) -> Run | None:
        runs = self._task_runs.get(task.id)
        if runs is None:
            runs = tuple(self._state.list_runs(task.id))
            self._task_runs[task.id] = runs
        return next(
            (
                run
                for run in runs
                if run.role is role and run.status in (RunStatus.LIVE, RunStatus.TAKEN_OVER)
            ),
            None,
        )

    def _motion_state(
        self,
        *,
        tasks: tuple[TaskMotionState, ...] = (),
        quotas: tuple[QuotaMotionState, ...] = (),
        escalations_open: bool = False,
        engine_running: bool = True,
    ) -> MotionState:
        config = self._settings.dashboard.motion
        return MotionState(
            animations=self._settings.dashboard.animations,
            timeouts=self._settings.orchestration.timeouts,
            refresh_hz=config.refresh_hz,
            stamp_flash_seconds=config.stamp_flash_milliseconds / _MILLISECONDS_PER_SECOND,
            escalation_pulse_hz=config.escalation_pulse_hz,
            tasks=tasks,
            quotas=quotas,
            escalations_open=escalations_open,
            engine_running=engine_running,
        )


if TYPE_CHECKING:
    from shikiru.domain.events import Event
    from shikiru.domain.motion import MotionFrame
    from shikiru.domain.runs import Run
