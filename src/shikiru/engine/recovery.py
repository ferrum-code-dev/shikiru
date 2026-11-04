"""Recovery of a turn that ended without a report: retries, quota waits and rung moves."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from shikiru.agents.contracts import FailureKind
from shikiru.domain.events import EventKind
from shikiru.domain.ladder import move_up, pick_rung
from shikiru.domain.recovery import (
    QUOTA_MARGIN,
    Backoff,
    Escalate,
    Failed,
    NextRung,
    RecoveryPolicy,
    ResumeTurn,
    RetryTurn,
    TurnCounters,
    WaitForQuota,
    decide,
    quota_block_until,
    with_known_quota_reset,
)
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import EscalationReason
from shikiru.engine.events import emit_turn_failed
from shikiru.engine.turn_context import ORDINARY_TURN, NextTurn

if TYPE_CHECKING:
    from datetime import datetime

    from shikiru.clock import Clock
    from shikiru.config.settings import Settings
    from shikiru.domain.recovery import Action, TurnResult
    from shikiru.domain.tasks import Role, Step, Task
    from shikiru.engine.quota_reconciler import QuotaReconciler
    from shikiru.engine.transitions import TransitionWriter
    from shikiru.engine.turn_execution import TurnExecution
    from shikiru.store.events import EventLog
    from shikiru.store.quotas import QuotaStore
    from shikiru.store.tasks import TaskStore

QUOTA_BLOCK_REASON = "quota"
_QUOTA_WAIT_EXHAUSTED_MESSAGE = "quota wait exceeded"
_CONTINUE = NextTurn(continue_turn=True)


@dataclass(frozen=True)
class RecoveryStep:
    """What the flow does after a failed turn: run again, re-decide on `execution` or escalate."""

    task: Task
    counters: TurnCounters
    execution: TurnExecution | None = None
    escalation: EscalationReason | None = None
    next_turn: NextTurn = ORDINARY_TURN


class TurnRecovery:
    """Decides and carries out the recovery of a failed turn; escalation stays with the flow."""

    def __init__(
        self,
        *,
        tasks: TaskStore,
        events: EventLog,
        quota: QuotaStore,
        reconciler: QuotaReconciler,
        clock: Clock,
        writer: TransitionWriter,
        settings: Settings,
    ) -> None:
        """Recover through the ladder, the quota blocks and the clock."""
        self._tasks = tasks
        self._events = events
        self._quota = quota
        self._reconciler = reconciler
        self._clock = clock
        self._writer = writer
        self._settings = settings

    async def recover(
        self,
        task: Task,
        step: Step,
        role: Role,
        execution: TurnExecution,
        counters: TurnCounters,
    ) -> RecoveryStep:
        """Apply the recovery `domain.recovery.decide` chooses for `execution`'s failed result."""
        execution = self._with_known_reset(execution)
        result = execution.result
        emit_turn_failed(
            self._events,
            task,
            step,
            result,
            rung=execution.rung,
            run_id=execution.run_id,
            turn=execution.turn_dir.name,
            role=execution.role,
        )
        action = decide(
            result,
            counters,
            self._policy(task, role, quota_wait_exhausted=execution.quota_wait_exhausted),
        )
        if isinstance(result, Failed) and result.kind is FailureKind.QUOTA:
            self._block_quota(task, role, result.resets_at)
        return await self._apply(
            task, step, role, execution=execution, counters=counters, action=action
        )

    def _with_known_reset(self, execution: TurnExecution) -> TurnExecution:
        windows = self._quota.windows_for(execution.rung.agent)
        result = with_known_quota_reset(execution.result, windows, self._clock.now())
        return dataclasses.replace(execution, result=result)

    def _policy(self, task: Task, role: Role, *, quota_wait_exhausted: bool) -> RecoveryPolicy:
        current = move_up(task, role, self._settings)
        different = move_up(
            task, role, self._settings, different_agent=True, blocked_agents=self._blocked_agents()
        )
        retries = self._settings.orchestration.retries
        return RecoveryPolicy(
            now=self._clock.now(),
            max_quota_wait=timedelta(seconds=self._settings.orchestration.timeouts.max_quota_wait),
            has_next_rung=current is not None,
            has_next_rung_different_agent=different is not None,
            unavailable_initial_seconds=retries.unavailable_initial_seconds,
            unavailable_multiplier=retries.unavailable_multiplier,
            unavailable_max_seconds=retries.unavailable_max_seconds,
            unavailable_max_retries=retries.unavailable_max_retries,
            quota_wait_exhausted=quota_wait_exhausted,
        )

    async def _apply(
        self,
        task: Task,
        step: Step,
        role: Role,
        *,
        execution: TurnExecution,
        counters: TurnCounters,
        action: Action,
    ) -> RecoveryStep:
        if isinstance(action, NextRung):
            return await self._next_rung(task, step, role, execution.result, action)
        if isinstance(action, Escalate):
            return RecoveryStep(task, TurnCounters(), escalation=action.reason)
        return await self._retry(task, role, execution, counters, action)

    async def _retry(
        self,
        task: Task,
        role: Role,
        execution: TurnExecution,
        counters: TurnCounters,
        action: WaitForQuota | Backoff | ResumeTurn | RetryTurn,
    ) -> RecoveryStep:
        if isinstance(action, RetryTurn):
            return RecoveryStep(
                task, dataclasses.replace(counters, other_retries=counters.other_retries + 1)
            )
        if isinstance(action, Backoff):
            await self._clock.sleep(action.delay.total_seconds())
            retried = dataclasses.replace(
                counters, unavailable_retries=counters.unavailable_retries + 1
            )
            return RecoveryStep(self._tasks.require(task.id), retried)
        if isinstance(action, ResumeTurn):
            resumed = dataclasses.replace(counters, stall_recoveries=counters.stall_recoveries + 1)
            return RecoveryStep(self._tasks.require(task.id), resumed, next_turn=_CONTINUE)
        terminal = await self._wait_for_quota(task, role)
        if terminal is None:
            return RecoveryStep(self._tasks.require(task.id), counters, next_turn=_CONTINUE)
        exhausted = dataclasses.replace(execution, result=terminal, quota_wait_exhausted=True)
        return RecoveryStep(self._tasks.require(task.id), counters, execution=exhausted)

    async def _next_rung(
        self, task: Task, step: Step, role: Role, result: TurnResult, action: NextRung
    ) -> RecoveryStep:
        updated = await self._move_rung(task, step, role, action.different_agent, result)
        if updated is not None:
            return RecoveryStep(
                updated, TurnCounters(), next_turn=NextTurn(handoff=action.different_agent)
            )
        reason = (
            EscalationReason.QUOTA_EXHAUSTED
            if action.different_agent
            else EscalationReason.LADDER_EXHAUSTED
        )
        return RecoveryStep(task, TurnCounters(), escalation=reason)

    async def _move_rung(
        self,
        task: Task,
        step: Step,
        role: Role,
        different_agent: bool,
        result: TurnResult,
    ) -> Task | None:
        move = move_up(
            task,
            role,
            self._settings,
            different_agent=different_agent,
            blocked_agents=self._blocked_agents() if different_agent else frozenset(),
        )
        if move is None:
            return None
        old_rung = pick_rung(task, role, self._settings)
        transition = dataclasses.replace(
            Transition.stay(task, step=step, status=task.status),
            counters=move.counters,
            rung_index=move.rung_index,
            cleared_overrides=move.cleared_overrides,
        )
        updated = await self._writer.apply(task, transition, None)
        new_rung = pick_rung(updated, role, self._settings)
        self._events.append(
            EventKind.RUNG_CHANGED,
            task_id=task.id,
            message=f"{task.id} {role.value} moved from {old_rung.model} to {new_rung.model}",
            data={
                "role": role.value,
                "from": old_rung.model,
                "to": new_rung.model,
                "different_agent": different_agent,
                "result": type(result).__name__,
            },
        )
        return updated

    def _blocked_agents(self) -> frozenset[str]:
        return frozenset(block.agent for block in self._quota.active_blocks(self._clock.now()))

    def _block_quota(self, task: Task, role: Role, resets_at: datetime | None) -> None:
        agent = pick_rung(task, role, self._settings).agent
        now = self._clock.now()
        until = quota_block_until(resets_at, now)
        self._quota.block(agent, until, QUOTA_BLOCK_REASON, now)
        self._events.append(
            EventKind.QUOTA_BLOCKED,
            task_id=task.id,
            message=f"{agent} quota blocked until {until.isoformat()}",
            data={"agent": agent, "until": until.isoformat(), "reason": QUOTA_BLOCK_REASON},
        )

    async def _wait_for_quota(self, task: Task, role: Role) -> Failed | None:
        agent = pick_rung(task, role, self._settings).agent
        deadline = self._clock.now() + timedelta(
            seconds=self._settings.orchestration.timeouts.max_quota_wait
        )
        while True:
            current = self._quota.block_for(agent)
            now = self._clock.now()
            if current is None:
                return None
            if current.until > now:
                if now >= deadline:
                    return Failed(
                        kind=FailureKind.QUOTA,
                        resets_at=current.until - QUOTA_MARGIN,
                        message=_QUOTA_WAIT_EXHAUSTED_MESSAGE,
                    )
                remaining = min(current.until, deadline) - now
                delay = min(remaining.total_seconds(), self._reconciler.interval_seconds)
                self._reconciler.expect_check(agent, now + timedelta(seconds=delay))
                await self._clock.sleep(delay)
                await self._reconciler.refresh(agent)
                continue
            self._quota.clear_block(agent)
            self._events.append(
                EventKind.QUOTA_CLEARED,
                task_id=task.id,
                message=f"{agent} quota block cleared",
                data={"agent": agent},
            )
            return None
