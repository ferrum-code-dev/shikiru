"""Task dispatch, gate evaluation and slot ownership (03 §10)."""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from shikiru.domain.events import EventKind
from shikiru.domain.ladder import pick_rung
from shikiru.domain.scheduling import (
    Dispatch,
    ReadySchedule,
    SchedulingGates,
    TaskAgents,
    occupied_slot_ids,
    pick_ready,
)
from shikiru.domain.steps import ACTING_ROLE, Transition
from shikiru.domain.tasks import Role, Status, Step, Task
from shikiru.errors import ShikiruError

if TYPE_CHECKING:
    from shikiru.clock import Clock
    from shikiru.config.settings import Settings
    from shikiru.engine.layout_guard import LayoutGuard
    from shikiru.engine.titles import TaskTitles
    from shikiru.store.events import EventLog
    from shikiru.store.quotas import QuotaStore
    from shikiru.store.tasks import TaskStore
    from shikiru.telemetry.resources import ResourceSampler
    from shikiru.workspace.roots import TaskRootBuilder

DependencyCallback = Callable[[Task], Awaitable[None]]
DispatchFailureCallback = Callable[[Task, Exception], Awaitable[None]]
MAX_DISPATCH_FAILURES = 3


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
        roots: TaskRootBuilder,
        events: EventLog,
        on_dependency_cancelled: DependencyCallback,
        on_dispatch_failure: DispatchFailureCallback,
        titles: TaskTitles,
    ) -> None:
        """Inject state stores, gates, and the task-window dispatch boundary."""
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
        self._dispatch_failures: dict[str, int] = {}
        self._logger = logging.getLogger(__name__)

    async def run_once(self) -> ReadySchedule:
        """Evaluate gates, persist wait reasons and dispatch every eligible task."""
        all_tasks = self._tasks.list_all()
        gates = self._gates(all_tasks)
        schedule = pick_ready(all_tasks, gates)
        self._persist_wait_reasons(all_tasks, schedule)
        for task_id in schedule.dependency_cancelled:
            task = self._tasks.get(task_id)
            if task is None:
                message = f"{task_id} vanished before dependency cancellation"
                raise ShikiruError(message)
            await self._cancel_dependency(task)
        successful_dispatches: list[Dispatch] = []
        for dispatch in schedule.dispatches:
            task = self._tasks.get(dispatch.task_id)
            if task is None:
                message = f"{dispatch.task_id} vanished before dispatch"
                raise ShikiruError(message)
            if await self._dispatch(task, dispatch.slot):
                successful_dispatches.append(dispatch)
        return dataclasses.replace(schedule, dispatches=tuple(successful_dispatches))

    def _gates(self, tasks: list[Task]) -> SchedulingGates:
        slots = frozenset(range(self._settings.orchestration.max_parallel)) - occupied_slot_ids(
            tasks
        )
        blocked = frozenset(block.agent for block in self._quotas.active_blocks(self._clock.now()))
        agents = {
            task.id: TaskAgents(
                leader=pick_rung(task, Role.LEADER, self._settings).agent,
                implementer=pick_rung(task, Role.IMPLEMENTER, self._settings).agent,
            )
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

    async def _cancel_dependency(self, task: Task) -> None:
        await self._on_dependency_cancelled(task)

    async def _dispatch(self, task: Task, slot: int) -> bool:
        current = task
        try:
            self._tasks.set_slot(task.id, slot)
            self._tasks.set_wait_reason(task.id, None)
            status = Status.INTEGRATING if task.step is Step.INTEGRATE else Status.RUNNING
            self._tasks.apply_transition(
                task.id, Transition.stay(task, step=task.step, status=status)
            )
            current = self._require_task(task.id, "slot assignment")
            if current.repo is None:
                repo = await self._roots.build(current.id, current.title)
                self._tasks.set_repo(current.id, repo)
                current = self._require_task(task.id, "task-root creation")
            await self._layout.ensure(current)
            current = self._require_task(task.id, "layout creation")
            await self._titles.refresh(current)
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
        self._events.append(
            EventKind.TASK_STARTED,
            task_id=current.id,
            message=f"{current.id} started in slot {slot}",
            data={"slot": slot},
        )
        if current.step is not None:
            role = ACTING_ROLE[current.step]
            rung = pick_rung(current, role, self._settings)
            self._events.append(
                EventKind.STEP_STARTED,
                task_id=current.id,
                message=f"{current.id} {current.step.value} started",
                data={
                    "step": current.step.value,
                    "role": role.value,
                    "iteration": current.iteration,
                    "agent": rung.agent,
                    "model": rung.model,
                },
            )
        return True

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
            raise ShikiruError(message)
        return current
