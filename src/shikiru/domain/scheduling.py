"""Ready-set computation and dispatch gating."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, auto
from graphlib import CycleError, TopologicalSorter
from typing import TYPE_CHECKING

from shikiru.domain.ladder import move_up, pick_rung
from shikiru.domain.tasks import Role, Status

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from datetime import datetime

    from shikiru.config.settings import Settings
    from shikiru.domain.tasks import Task


class WaitReason(StrEnum):
    """Why a task is waiting instead of running."""

    PAUSED = auto()
    DEPENDENCY = auto()
    SLOT = auto()
    RESOURCES = auto()
    QUOTA = auto()
    PR_OPEN = "PR open"


@dataclass(frozen=True)
class TaskAgents:
    """The agent names a task would use for the Leader and the Implementer roles."""

    leader: str
    implementer: str


@dataclass(frozen=True)
class SchedulingGates:
    """Pure data the scheduler gathers before a dispatch pass."""

    free_slots: frozenset[int]
    resources_ok: bool
    blocked_agents: frozenset[str]
    agents: Mapping[str, TaskAgents]


@dataclass(frozen=True)
class Dispatch:
    """A task to dispatch into `slot`."""

    task_id: str
    slot: int


@dataclass(frozen=True)
class ReadySchedule:
    """The result of one scheduling pass."""

    dispatches: tuple[Dispatch, ...]
    wait_reasons: Mapping[str, WaitReason]
    dependency_cancelled: tuple[str, ...]


def find_dependency_cycle(dependencies: Mapping[str, Iterable[str]]) -> tuple[str, ...]:
    """Return the task ids on one dependency cycle (a self-edge included), or none."""
    try:
        TopologicalSorter(dependencies).prepare()
    except CycleError as error:
        return tuple(dict.fromkeys(error.args[1]))
    return ()


def dispatch_sort_key(task: Task) -> tuple[bool, int, int]:
    """Order `task` the way dispatch does: requeued first, then priority, then submit order."""
    return (task.requeued_at is None, -task.priority, task.seq)


def occupied_slot_ids(tasks: Sequence[Task]) -> frozenset[int]:
    """Return distinct slots held by running or integrating tasks."""
    return frozenset(
        task.slot
        for task in tasks
        if task.status in (Status.RUNNING, Status.INTEGRATING) and task.slot is not None
    )


def task_agents(task: Task, settings: Settings) -> TaskAgents:
    """Resolve the Leader and Implementer agents `task` would use for its next turns."""
    return TaskAgents(
        leader=pick_rung(task, Role.LEADER, settings).agent,
        implementer=pick_rung(task, Role.IMPLEMENTER, settings).agent,
    )


def quota_blocked_agents(agents: TaskAgents, blocked_until: Mapping[str, datetime]) -> list[str]:
    """Return each agent of `agents` that is blocked, once each, Leader first."""
    return [
        agent
        for agent in dict.fromkeys((agents.leader, agents.implementer))
        if agent in blocked_until
    ]


def quota_blocking_agent(agents: TaskAgents, blocked_until: Mapping[str, datetime]) -> str | None:
    """Return the blocked agent of `agents` with the latest deadline; Leader wins ties."""
    blocked = quota_blocked_agents(agents, blocked_until)
    return max(blocked, key=blocked_until.__getitem__, default=None)


def quota_wait_reroutes(
    task: Task,
    settings: Settings,
    blocked_until: Mapping[str, datetime],
    wait_limit: datetime,
) -> bool:
    """Return whether the scheduler moves `task` to another agent: its block outlasts `wait_limit`.

    A role moves when its resolved agent is blocked beyond `wait_limit` and a higher rung on a
    different, unblocked agent exists; this is the rule the scheduler applies to queued tasks.
    """
    blocked_agents = frozenset(blocked_until)
    for role in (Role.LEADER, Role.IMPLEMENTER):
        until = blocked_until.get(pick_rung(task, role, settings).agent)
        if until is None or until <= wait_limit:
            continue
        moved = move_up(task, role, settings, different_agent=True, blocked_agents=blocked_agents)
        if moved is not None:
            return True
    return False


def _group_queued_tasks(
    tasks: Sequence[Task], by_id: Mapping[str, Task]
) -> tuple[list[Task], dict[str, WaitReason], list[str]]:
    ready: list[Task] = []
    wait_reasons: dict[str, WaitReason] = {}
    dependency_cancelled: list[str] = []
    for task in tasks:
        if task.status != Status.QUEUED:
            continue
        if task.held:
            wait_reasons[task.id] = WaitReason.PAUSED
            continue
        deps = [by_id[dep_id] for dep_id in task.depends_on]
        if any(dep.status == Status.CANCELLED for dep in deps):
            dependency_cancelled.append(task.id)
            continue
        if all(dep.status == Status.DONE for dep in deps):
            ready.append(task)
        else:
            wait_reasons[task.id] = WaitReason.DEPENDENCY
    return ready, wait_reasons, dependency_cancelled


def pick_ready(tasks: Sequence[Task], gates: SchedulingGates) -> ReadySchedule:
    """Return the ready set in dispatch order, gated by slots, resources and quota.

    `tasks` is every task the caller tracks, so dependency statuses can be looked up by id; an
    id that isn't in `tasks` is a caller bug and raises `KeyError`, as does a ready task missing
    from `gates.agents`.

    Ready tasks are ordered with `requeued_at` set first, then `priority` descending, then `seq`.
    Slot and resources are gates shared by the whole pass: the first one that fails stops
    dispatch there, and every task from that point on shares its `wait_reason`, since a
    lower-priority task never dispatches ahead of a blocked one. Quota is scoped to one task at a
    time (a quota block is the only cross-task effect, but it is still per-agent, not
    per-pass): a quota-blocked task is skipped and dispatch continues with the next one.
    """
    by_id = {task.id: task for task in tasks}
    ready, wait_reasons, dependency_cancelled = _group_queued_tasks(tasks, by_id)

    ordered = sorted(ready, key=dispatch_sort_key)
    remaining_slots = set(gates.free_slots)
    dispatches: list[Dispatch] = []
    stopped_reason: WaitReason | None = None

    for task in ordered:
        if stopped_reason is not None:
            wait_reasons[task.id] = stopped_reason
            continue
        if not remaining_slots:
            stopped_reason = WaitReason.SLOT
            wait_reasons[task.id] = stopped_reason
            continue
        if not gates.resources_ok:
            stopped_reason = WaitReason.RESOURCES
            wait_reasons[task.id] = stopped_reason
            continue
        agents = gates.agents[task.id]
        if agents.leader in gates.blocked_agents or agents.implementer in gates.blocked_agents:
            wait_reasons[task.id] = WaitReason.QUOTA
            continue
        slot = task.slot if task.slot in remaining_slots else min(remaining_slots)
        remaining_slots.discard(slot)
        dispatches.append(Dispatch(task_id=task.id, slot=slot))

    return ReadySchedule(
        dispatches=tuple(dispatches),
        wait_reasons=wait_reasons,
        dependency_cancelled=tuple(dependency_cancelled),
    )
