"""Task dispatch, gate evaluation and slot ownership (03 §10)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from shikiru.domain.events import EventKind
from shikiru.domain.ladder import pick_rung
from shikiru.domain.scheduling import ReadySchedule, SchedulingGates, TaskAgents, pick_ready
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import Role, Status, Step, Task
from shikiru.errors import ShikiruError

if TYPE_CHECKING:
    from shikiru.clock import Clock
    from shikiru.config.settings import Settings
    from shikiru.engine.layout_guard import LayoutGuard
    from shikiru.store.events import EventLog
    from shikiru.store.quotas import QuotaStore
    from shikiru.store.tasks import TaskStore
    from shikiru.telemetry.resources import ResourceSampler
    from shikiru.workspace.roots import TaskRootBuilder

DependencyCallback = Callable[[Task], Awaitable[None]]


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
        for dispatch in schedule.dispatches:
            task = self._tasks.get(dispatch.task_id)
            if task is None:
                message = f"{dispatch.task_id} vanished before dispatch"
                raise ShikiruError(message)
            await self._dispatch(task, dispatch.slot)
        return schedule

    def _gates(self, tasks: list[Task]) -> SchedulingGates:
        active_slots = {
            task.slot
            for task in tasks
            if task.status in (Status.RUNNING, Status.INTEGRATING) and task.slot is not None
        }
        slots = frozenset(range(self._settings.orchestration.max_parallel)) - active_slots
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

    async def _dispatch(self, task: Task, slot: int) -> None:
        self._tasks.set_slot(task.id, slot)
        self._tasks.set_wait_reason(task.id, None)
        status = Status.INTEGRATING if task.step is Step.INTEGRATE else Status.RUNNING
        self._tasks.apply_transition(task.id, Transition.stay(task, step=task.step, status=status))
        current = self._tasks.get(task.id)
        if current is None:
            message = f"{task.id} vanished after slot assignment"
            raise ShikiruError(message)
        if not current.repos:
            repos = await self._roots.build(current.id, current.title, self._settings.repos)
            self._tasks.set_repos(current.id, repos)
            current = self._tasks.get(current.id)
            if current is None:
                message = f"{task.id} vanished after task-root creation"
                raise ShikiruError(message)
        await self._layout.ensure(current)
        current = self._tasks.get(current.id)
        if current is None:
            message = f"{task.id} vanished after layout creation"
            raise ShikiruError(message)
        self._events.append(
            EventKind.TASK_STARTED,
            task_id=current.id,
            message=f"{current.id} started in slot {slot}",
            data={"slot": slot},
        )
