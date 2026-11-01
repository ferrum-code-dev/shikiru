"""Which windows and role panes of a task are in a quota wait, from authoritative current facts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from shikiru.domain.ladder import pick_rung
from shikiru.domain.quota_waits import current_quota_wait_events
from shikiru.domain.scheduling import WaitReason
from shikiru.domain.steps import task_acting_role
from shikiru.domain.tasks import Role, Status

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    from shikiru.clock import Clock
    from shikiru.config.settings import Settings
    from shikiru.domain.quota_waits import QuotaAction
    from shikiru.domain.tasks import Task
    from shikiru.engine.quota_schedule import QuotaSchedule
    from shikiru.store.events import EventLog
    from shikiru.store.quotas import AgentBlock, QuotaStore

_QUOTA_ROLES = (Role.LEADER, Role.IMPLEMENTER)
_TURN_STATUSES = (Status.RUNNING, Status.INTEGRATING)


@dataclass(frozen=True)
class RoleQuotaWait:
    """A role whose agent is quota blocked, with the engine's next action on it when known."""

    action: QuotaAction | None = None
    at: datetime | None = None


@dataclass(frozen=True)
class TaskQuotaWait:
    """A task in a quota wait and the roles whose agents the wait is about.

    A queued task waits when any required role is blocked, whichever role acts next; a running
    task waits in the turn of its acting role only. `roles` can be empty while the persisted
    wait reason still names quota, as the task list also shows.
    """

    roles: Mapping[Role, RoleQuotaWait]


class QuotaWaits:
    """Resolves a task's quota wait with the same rules as the task list's quota marker."""

    def __init__(
        self,
        *,
        quotas: QuotaStore,
        events: EventLog,
        schedule: QuotaSchedule,
        clock: Clock,
        settings: Settings,
    ) -> None:
        """Read blocks from `quotas`, turn facts from `events`, next actions from `schedule`."""
        self._quotas = quotas
        self._events = events
        self._schedule = schedule
        self._clock = clock
        self._settings = settings

    def resolve(self, task: Task) -> TaskQuotaWait | None:
        """Return `task`'s quota wait, or none when it is not waiting on quota."""
        if task.status is Status.QUEUED:
            return self._queued(task)
        if task.status in _TURN_STATUSES and task.step is not None:
            return self._running(task)
        return None

    def _queued(self, task: Task) -> TaskQuotaWait | None:
        if task.wait_reason != WaitReason.QUOTA.value:
            return None
        blocks = {block.agent: block for block in self._quotas.active_blocks(self._clock.now())}
        roles = {
            role: self._role_wait(blocks[agent])
            for role in _QUOTA_ROLES
            if (agent := pick_rung(task, role, self._settings).agent) in blocks
        }
        return TaskQuotaWait(roles)

    def _running(self, task: Task) -> TaskQuotaWait | None:
        role = task_acting_role(task)
        agent = pick_rung(task, role, self._settings).agent
        if current_quota_wait_events(task, role, agent, self._events.latest) is None:
            return None
        block = self._quotas.block_for(agent)
        if block is None or block.until <= self._clock.now():
            return None
        return TaskQuotaWait({role: self._role_wait(block)})

    def _role_wait(self, block: AgentBlock) -> RoleQuotaWait:
        scheduled = self._schedule.get(block.agent)
        if scheduled is None or scheduled.blocked_at != block.blocked_at:
            return RoleQuotaWait()
        return RoleQuotaWait(scheduled.action, scheduled.at)
