"""Task dispatch, gate evaluation and slot ownership."""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import TYPE_CHECKING

from shikiru.domain.events import EventKind
from shikiru.domain.faults import next_backoff
from shikiru.domain.ladder import move_up, pick_rung
from shikiru.domain.scheduling import (
    Dispatch,
    ReadySchedule,
    SchedulingGates,
    WaitReason,
    occupied_slot_ids,
    pick_ready,
    task_agents,
)
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import Role, Status, StatusReason, Step, Task
from shikiru.engine.events import emit_step_started
from shikiru.errors import InvariantError
from shikiru.workspace.base_refs import BaseFetchFailed

if TYPE_CHECKING:
    from datetime import datetime

    from shikiru.clock import Clock
    from shikiru.config.settings import Settings
    from shikiru.engine.fault_reporter import FaultReporter
    from shikiru.engine.layout_guard import LayoutGuard
    from shikiru.engine.root_preparation import RootPreparation
    from shikiru.engine.titles import TaskTitles
    from shikiru.store.events import EventLog
    from shikiru.store.quotas import QuotaStore
    from shikiru.store.tasks import TaskStore
    from shikiru.telemetry.resources import ResourceSampler

DependencyCallback = Callable[[Task], Awaitable[None]]
DispatchFailureCallback = Callable[[Task, Exception], Awaitable[None]]
MAX_DISPATCH_FAILURES = 3
FETCH_FAULT_SCOPE = "dispatch fetch"


class Scheduler:
    """Builds the ready set, persists wait reasons and dispatches eligible tasks."""

    def __init__(
        self,
        *,
        tasks: TaskStore,
        settings: Settings,
        quotas: QuotaStore,
        resources: ResourceSampler,
        clock: Clock,
        layout: LayoutGuard,
        roots: RootPreparation,
        events: EventLog,
        on_dependency_cancelled: DependencyCallback,
        on_dispatch_failure: DispatchFailureCallback,
        titles: TaskTitles,
        faults: FaultReporter,
    ) -> None:
        """Inject state stores, gates, and the task-window dispatch boundary.

        A dispatch whose base fetch fails is retried on a growing backoff, recorded through
        `faults`, and never counts toward `MAX_DISPATCH_FAILURES`.
        """
        self._tasks = tasks
        self._settings = settings
        self._quotas = quotas
        self._resources = resources
        self._clock = clock
        self._layout = layout
        self._roots = roots
        self._events = events
        self._on_dependency_cancelled = on_dependency_cancelled
        self._on_dispatch_failure = on_dispatch_failure
        self._titles = titles
        self._faults = faults
        self._dispatch_failures: dict[str, int] = {}
        self._fetch_backoffs: dict[str, float] = {}
        self._fetch_retry_at: dict[str, datetime] = {}
        self._logger = logging.getLogger(__name__)

    async def run_once(self) -> ReadySchedule:
        """Evaluate gates, persist wait reasons and dispatch every eligible task."""
        all_tasks = self._tasks.list_all()
        gates = self._gates(all_tasks)
        schedule = pick_ready(all_tasks, gates)
        if self._advance_quota_blocked(all_tasks, schedule):
            all_tasks = self._tasks.list_all()
            schedule = pick_ready(all_tasks, self._gates(all_tasks))
        self._roots.begin_tick({task.id for task in all_tasks if task.status is Status.QUEUED})
        self._persist_wait_reasons(all_tasks, schedule)
        for task_id in schedule.dependency_cancelled:
            task = self._tasks.get(task_id)
            if task is None:
                message = f"{task_id} vanished before dependency cancellation"
                raise InvariantError(message)
            await self._on_dependency_cancelled(task)
        successful_dispatches: list[Dispatch] = []
        for dispatch in schedule.dispatches:
            task = self._tasks.get(dispatch.task_id)
            if task is None:
                message = f"{dispatch.task_id} vanished before dispatch"
                raise InvariantError(message)
            if self._awaits_fetch_retry(task.id):
                continue
            if await self._dispatch(task, dispatch.slot):
                successful_dispatches.append(dispatch)
        return dataclasses.replace(schedule, dispatches=tuple(successful_dispatches))

    async def shutdown(self) -> None:
        """Stop the task root builds still in flight."""
        await self._roots.shutdown()

    def _advance_quota_blocked(self, tasks: list[Task], schedule: ReadySchedule) -> bool:
        now = self._clock.now()
        blocks = {block.agent: block.until for block in self._quotas.active_blocks(now)}
        blocked_agents = frozenset(blocks)
        wait_limit = now + timedelta(seconds=self._settings.orchestration.timeouts.max_quota_wait)
        changed = False
        for queued in tasks:
            if schedule.wait_reasons.get(queued.id) is not WaitReason.QUOTA:
                continue
            task = queued
            for role in (Role.LEADER, Role.IMPLEMENTER):
                old_rung = pick_rung(task, role, self._settings)
                until = blocks.get(old_rung.agent)
                if until is None or until <= wait_limit:
                    continue
                move = move_up(
                    task, role, self._settings, different_agent=True, blocked_agents=blocked_agents
                )
                if move is None:
                    continue
                transition = dataclasses.replace(
                    Transition.stay(task, step=task.step, status=task.status),
                    counters=move.counters,
                    rung_index=move.rung_index,
                    cleared_overrides=move.cleared_overrides,
                )
                self._tasks.apply_transition(task.id, transition)
                task = self._tasks.require(task.id)
                new_rung = pick_rung(task, role, self._settings)
                self._events.append(
                    EventKind.RUNG_CHANGED,
                    task_id=task.id,
                    message=(
                        f"{task.id} {role.value} moved from {old_rung.model} to {new_rung.model}"
                    ),
                    data={
                        "role": role.value,
                        "from": old_rung.model,
                        "to": new_rung.model,
                        "different_agent": True,
                        "reason": WaitReason.QUOTA.value,
                    },
                )
                changed = True
        return changed

    def _gates(self, tasks: list[Task]) -> SchedulingGates:
        slots = frozenset(range(self._settings.orchestration.max_parallel)) - occupied_slot_ids(
            tasks
        )
        blocked = frozenset(block.agent for block in self._quotas.active_blocks(self._clock.now()))
        agents = {
            task.id: task_agents(task, self._settings)
            for task in tasks
            if task.status is Status.QUEUED
        }
        return SchedulingGates(
            free_slots=slots,
            resources_ok=self._resources.resources_ok(self._settings.orchestration.resources),
            blocked_agents=blocked,
            agents=agents,
        )

    def _persist_wait_reasons(self, tasks: list[Task], schedule: ReadySchedule) -> None:
        reasons = schedule.wait_reasons
        for task in tasks:
            if task.status is not Status.QUEUED:
                continue
            reason = reasons.get(task.id)
            if task.wait_reason != (reason.value if reason is not None else None):
                self._tasks.set_wait_reason(task.id, reason)

    async def _dispatch(self, task: Task, slot: int) -> bool:
        current = task
        try:
            repo = task.repo
            if repo is None:
                repo = await self._roots.prepare(task.id, task.title, self._seed_branch(task))
                if repo is None:
                    return False
            self._tasks.set_slot(task.id, slot)
            self._tasks.set_wait_reason(task.id, None)
            status = Status.INTEGRATING if task.step is Step.INTEGRATE else Status.RUNNING
            self._tasks.apply_transition(
                task.id, Transition.stay(task, step=task.step, status=status)
            )
            current = self._require_task(task.id, "slot assignment")
            if current.repo is None:
                self._tasks.set_repo(current.id, repo)
                current = self._require_task(task.id, "task-root creation")
            await self._layout.ensure(current)
            current = self._require_task(task.id, "layout creation")
            await self._titles.refresh(current)
        except BaseFetchFailed as error:
            self._logger.warning("dispatch of %s waits for the remote: %s", task.id, error)
            await self._rollback_dispatch(task)
            self._back_off_fetch(task.id, error)
            return False
        except Exception as error:
            self._logger.exception("dispatch setup failed for %s", task.id)
            await self._rollback_dispatch(task)
            failures = self._dispatch_failures.get(task.id, 0) + 1
            self._dispatch_failures[task.id] = failures
            self._events.append(
                EventKind.DISPATCH_FAILED,
                task_id=task.id,
                message=f"{task.id} dispatch failed",
                data={"attempt": failures, "error": str(error)},
            )
            if failures >= MAX_DISPATCH_FAILURES:
                current = self._require_task(task.id, "dispatch failure")
                await self._on_dispatch_failure(current, error)
                self._dispatch_failures.pop(task.id, None)
            return False
        self._dispatch_failures.pop(task.id, None)
        self._fetch_backoffs.pop(task.id, None)
        self._fetch_retry_at.pop(task.id, None)
        self._faults.resolve(FETCH_FAULT_SCOPE, task_id=task.id)
        self._events.append(
            EventKind.TASK_STARTED,
            task_id=current.id,
            message=f"{current.id} started in slot {slot}",
            data={"slot": slot},
        )
        emit_step_started(self._events, current, self._settings)
        return True

    def _awaits_fetch_retry(self, task_id: str) -> bool:
        retry_at = self._fetch_retry_at.get(task_id)
        return retry_at is not None and self._clock.now() < retry_at

    def _back_off_fetch(self, task_id: str, error: BaseFetchFailed) -> None:
        faults = self._settings.orchestration.faults
        delay = next_backoff(
            self._fetch_backoffs.get(task_id, 0.0),
            initial=faults.backoff_initial_seconds,
            maximum=faults.backoff_max_seconds,
        )
        self._fetch_backoffs[task_id] = delay
        self._fetch_retry_at[task_id] = self._clock.now() + timedelta(seconds=delay)
        self._faults.report(error, scope=FETCH_FAULT_SCOPE, task_id=task_id)

    def _seed_branch(self, task: Task) -> str | None:
        parent = self._tasks.get(task.parent_id) if task.parent_id is not None else None
        if parent is None or parent.status_reason is not StatusReason.SUPERSEDED:
            return None
        return parent.repo.branch if parent.repo is not None else None

    async def _rollback_dispatch(self, task: Task) -> None:
        current = self._tasks.get(task.id)
        if current is None:
            return
        if task.window_id is None and current.window_id is not None:
            try:
                await self._layout.close(current)
            except Exception:
                self._logger.exception("dispatch cleanup failed for %s", task.id)
        self._tasks.apply_transition(
            current.id, Transition.stay(current, step=current.step, status=Status.QUEUED)
        )
        self._tasks.set_slot(current.id, None)
        self._tasks.set_wait_reason(current.id, None)

    def _require_task(self, task_id: str, operation: str) -> Task:
        current = self._tasks.get(task_id)
        if current is None:
            message = f"{task_id} vanished after {operation}"
            raise InvariantError(message)
        return current
