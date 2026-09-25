"""Ready-set computation and dispatch gating (docs/design/03-workflow.md §10)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, auto
from typing import TYPE_CHECKING

from shikiru.domain.tasks import Hold, Status

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from shikiru.domain.tasks import Task


class WaitReason(StrEnum):
    """Why a queued task isn't running yet (03 §9, §10)."""

    PAUSED = auto()
    DEPENDENCY = auto()
    SLOT = auto()
    RESOURCES = auto()
    QUOTA = auto()


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


def dispatch_sort_key(task: Task) -> tuple[bool, int, int]:
    """Order `task` the way dispatch does: requeued first, then priority, then submit order."""
    return (task.requeued_at is None, -task.priority, task.seq)


def occupied_slot_ids(tasks: Sequence[Task]) -> frozenset[int]:
    """Return distinct slots held by running or integrating tasks (03 §1.2, §10)."""
    return frozenset(
        task.slot
        for task in tasks
        if task.status in (Status.RUNNING, Status.INTEGRATING) and task.slot is not None
    )


def _group_queued_tasks(
    tasks: Sequence[Task], by_id: Mapping[str, Task]
) -> tuple[list[Task], dict[str, WaitReason], list[str]]:
    ready: list[Task] = []
    wait_reasons: dict[str, WaitReason] = {}
    dependency_cancelled: list[str] = []
    for task in tasks:
        if task.status != Status.QUEUED:
            continue
        if Hold.PAUSED in task.holds:
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
    """Return the ready set in dispatch order, gated by slots, resources and quota (03 §10).

    `tasks` is every task the caller tracks, so dependency statuses can be looked up by id; an
    id that isn't in `tasks` is a caller bug and raises `KeyError`, as does a ready task missing
    from `gates.agents`.

    Ready tasks are ordered with `requeued_at` set first, then `priority` descending, then `seq`.
    Slot and resources are gates shared by the whole pass: the first one that fails stops
    dispatch there, and every task from that point on shares its `wait_reason`, since a
    lower-priority task never dispatches ahead of a blocked one. Quota is scoped to one task at a
    time (03 §9: a quota block is the only cross-task effect, but it is still per-agent, not
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
