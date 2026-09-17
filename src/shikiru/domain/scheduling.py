"""Ready-set computation and dispatch gating (docs/design/03-workflow.md §10)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from shikiru.domain.tasks import Hold, Status

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from shikiru.domain.tasks import Task

WAIT_REASON_SLOT = "slot"
WAIT_REASON_RESOURCES = "resources"
WAIT_REASON_QUOTA = "quota"


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
    wait_reasons: Mapping[str, str]
    dependency_cancelled: tuple[str, ...]


def _sort_key(task: Task) -> tuple[bool, int, int]:
    return (task.requeued_at is None, -task.priority, task.seq)


def pick_ready(tasks: Sequence[Task], gates: SchedulingGates) -> ReadySchedule:
    """Return the ready set in dispatch order, gated by slots, resources and quota (03 §10).

    `tasks` is every task the caller tracks, so dependency statuses can be looked up by id.
    Ready tasks are ordered with `requeued_at` set first, then `priority` descending, then `seq`.
    The first gate that fails stops the whole pass: that task and every task after it in order
    share its `wait_reason`, since a lower-priority task never dispatches ahead of a blocked one.
    """
    by_id = {task.id: task for task in tasks}
    dependency_cancelled: list[str] = []
    ready: list[Task] = []
    for task in tasks:
        if task.status != Status.QUEUED or Hold.PAUSED in task.holds:
            continue
        deps = [by_id[dep_id] for dep_id in task.depends_on if dep_id in by_id]
        if any(dep.status == Status.CANCELLED for dep in deps):
            dependency_cancelled.append(task.id)
            continue
        if all(dep.status == Status.DONE for dep in deps):
            ready.append(task)

    ordered = sorted(ready, key=_sort_key)
    remaining_slots = set(gates.free_slots)
    dispatches: list[Dispatch] = []
    wait_reasons: dict[str, str] = {}
    stopped_reason: str | None = None

    for task in ordered:
        if stopped_reason is not None:
            wait_reasons[task.id] = stopped_reason
            continue
        if not remaining_slots:
            stopped_reason = WAIT_REASON_SLOT
            wait_reasons[task.id] = stopped_reason
            continue
        if not gates.resources_ok:
            stopped_reason = WAIT_REASON_RESOURCES
            wait_reasons[task.id] = stopped_reason
            continue
        agents = gates.agents.get(task.id)
        if agents is not None and (
            agents.leader in gates.blocked_agents or agents.implementer in gates.blocked_agents
        ):
            stopped_reason = WAIT_REASON_QUOTA
            wait_reasons[task.id] = stopped_reason
            continue
        slot = task.slot if task.slot in remaining_slots else min(remaining_slots)
        remaining_slots.discard(slot)
        dispatches.append(Dispatch(task_id=task.id, slot=slot))

    return ReadySchedule(
        dispatches=tuple(dispatches),
        wait_reasons=wait_reasons,
        dependency_cancelled=tuple(dependency_cancelled),
    )
