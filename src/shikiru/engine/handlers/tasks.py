"""Handlers for task lifecycle and scheduling commands."""

from __future__ import annotations

import dataclasses

from shikiru.domain.commands import (
    Cancel,
    Pause,
    Prioritize,
    Resume,
    Retry,
    SetRung,
    Submit,
)
from shikiru.domain.events import EventKind
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import Hold, Status
from shikiru.engine.handlers.base import (
    HandlerContext,
    interrupt_task,
    refresh_titles,
    reject_terminal,
    require_task,
)
from shikiru.errors import CommandRejectedError


class SubmitHandler:
    """Insert a submitted task and emit its queue event."""

    command_type: type[Submit] = Submit

    def __init__(self, context: HandlerContext) -> None:
        self._context = context

    async def handle(self, command: Submit) -> dict[str, object]:
        task = self._context.tasks.insert_from_submit(command)
        self._context.events.append(
            EventKind.TASK_QUEUED,
            task_id=task.id,
            message=f"{task.id} queued",
            data={"priority": task.priority},
        )
        return {"task_id": task.id}


class PrioritizeHandler:
    """Change one queued task's dispatch priority."""

    command_type: type[Prioritize] = Prioritize

    def __init__(self, context: HandlerContext) -> None:
        self._context = context

    async def handle(self, command: Prioritize) -> dict[str, object]:
        task = require_task(self._context, command.task_id)
        if task.status is not Status.QUEUED:
            message = f"{task.id} can only be prioritized while queued"
            raise CommandRejectedError(message)
        self._context.tasks.set_priority(task.id, command.priority)
        return {"task_id": task.id, "priority": command.priority}


class PauseHandler:
    """Set a task pause hold, optionally interrupting its current turn."""

    command_type: type[Pause] = Pause

    def __init__(self, context: HandlerContext) -> None:
        self._context = context

    async def handle(self, command: Pause) -> dict[str, object]:
        task = require_task(self._context, command.task_id)
        reject_terminal(task)
        if command.now:
            await interrupt_task(self._context, task)
        if Hold.PAUSED not in task.holds:
            self._context.tasks.set_holds(task.id, task.holds | {Hold.PAUSED})
            self._context.events.append(
                EventKind.HOLD_SET,
                task_id=task.id,
                message=f"{task.id} paused",
                data={"hold": Hold.PAUSED.value, "now": command.now},
            )
        await refresh_titles(self._context, task.id)
        return {"task_id": task.id, "paused": True, "now": command.now}


class ResumeHandler:
    """Clear task holds and resume the task flow, unless a role is taken over."""

    command_type: type[Resume] = Resume

    def __init__(self, context: HandlerContext) -> None:
        self._context = context

    async def handle(self, command: Resume) -> dict[str, object]:
        task = require_task(self._context, command.task_id)
        reject_terminal(task)
        if self._context.runs.has_taken_over(task.id):
            message = f"{task.id} has a role taken over; hand it back before resuming"
            raise CommandRejectedError(message)
        holds = task.holds
        self._context.tasks.set_holds(task.id, frozenset())
        for hold in holds:
            self._context.events.append(
                EventKind.HOLD_CLEARED,
                task_id=task.id,
                message=f"{task.id} {hold.value} hold cleared",
                data={"hold": hold.value},
            )
        if holds & {Hold.PAUSED, Hold.INTERVENTION} and task.status in (
            Status.RUNNING,
            Status.INTEGRATING,
        ):
            await self._context.start_task(task.id)
        await refresh_titles(self._context, task.id)
        return {"task_id": task.id, "paused": False, "intervention": False}


class CancelHandler:
    """Cancel a task, interrupting its current turn and retaining its branch."""

    command_type: type[Cancel] = Cancel

    def __init__(self, context: HandlerContext) -> None:
        self._context = context

    async def handle(self, command: Cancel) -> dict[str, object]:
        task = require_task(self._context, command.task_id)
        reject_terminal(task)
        await interrupt_task(self._context, task)
        current = self._context.tasks.get(task.id)
        if current is None:
            message = f"task {task.id} disappeared while cancelling"
            raise CommandRejectedError(message)
        self._context.tasks.finish(
            task.id,
            Transition.stay(current, step=current.step, status=Status.CANCELLED),
            EventKind.TASK_CANCELLED,
            message=f"{task.id} cancelled",
            data={"reason": "cancelled by operator"},
        )
        updated = self._context.tasks.get(task.id)
        if updated is None:
            message = f"task {task.id} disappeared after cancellation"
            raise CommandRejectedError(message)
        await refresh_titles(self._context, updated.id)
        await self._context.finish_task(updated)
        return {"task_id": task.id, "status": Status.CANCELLED.value}


class RetryHandler:
    """Cancel the current step and requeue it for an idempotent retry."""

    command_type: type[Retry] = Retry

    def __init__(self, context: HandlerContext) -> None:
        self._context = context

    async def handle(self, command: Retry) -> dict[str, object]:
        task = require_task(self._context, command.task_id)
        reject_terminal(task)
        await interrupt_task(self._context, task)
        current = self._context.tasks.get(task.id)
        if current is None:
            message = f"task {task.id} disappeared while retrying"
            raise CommandRejectedError(message)
        counters = current.counters.model_copy(update={"consults": 0, "conflict_rounds": 0})
        transition = dataclasses.replace(
            Transition.stay(current, step=current.step, status=Status.QUEUED),
            counters=counters,
        )
        self._context.tasks.apply_transition(current.id, transition)
        self._context.tasks.set_requeued(current.id)
        await refresh_titles(self._context, current.id)
        return {"task_id": current.id, "status": Status.QUEUED.value}


class SetRungHandler:
    """Apply a role rung override to the next task turn."""

    command_type: type[SetRung] = SetRung

    def __init__(self, context: HandlerContext) -> None:
        self._context = context

    async def handle(self, command: SetRung) -> dict[str, object]:
        task = require_task(self._context, command.task_id)
        reject_terminal(task)
        overrides = dict(task.overrides)
        old = overrides.get(command.role)
        overrides[command.role] = command.rung
        transition = dataclasses.replace(
            Transition.stay(task, step=task.step, status=task.status),
            overrides=overrides,
        )
        self._context.tasks.apply_transition(task.id, transition)
        self._context.events.append(
            EventKind.RUNG_CHANGED,
            task_id=task.id,
            message=f"{task.id} {command.role.value} rung set to {command.rung.model}",
            data={
                "role": command.role.value,
                "from": old.model if old is not None else None,
                "to": command.rung.model,
                "operator": True,
            },
        )
        await refresh_titles(self._context, task.id)
        return {"task_id": task.id, "role": command.role.value, "model": command.rung.model}
