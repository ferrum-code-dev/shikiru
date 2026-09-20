"""Handlers for task lifecycle and scheduling commands."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

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
from shikiru.domain.steps import ACTING_ROLE, Transition
from shikiru.domain.tasks import Hold, Status
from shikiru.engine.handlers.base import (
    HandlerContext,
    reject_terminal,
    reject_wrong_command,
    require_task,
)
from shikiru.errors import CommandRejectedError

if TYPE_CHECKING:
    from shikiru.domain.commands import Command


class SubmitHandler:
    """Insert a submitted task and emit its queue event."""

    command_type: ClassVar[object] = Submit

    def __init__(self, context: HandlerContext) -> None:
        self._context = context

    async def handle(self, command: Command) -> dict[str, object]:
        if not isinstance(command, Submit):
            reject_wrong_command("SubmitHandler", command)
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

    command_type: ClassVar[object] = Prioritize

    def __init__(self, context: HandlerContext) -> None:
        self._context = context

    async def handle(self, command: Command) -> dict[str, object]:
        if not isinstance(command, Prioritize):
            reject_wrong_command("PrioritizeHandler", command)
        task = require_task(self._context, command.task_id)
        if task.status is not Status.QUEUED:
            message = f"{task.id} can only be prioritized while queued"
            raise CommandRejectedError(message)
        self._context.tasks.set_priority(task.id, command.priority)
        return {"task_id": task.id, "priority": command.priority}


class PauseHandler:
    """Set a task pause hold, optionally interrupting its current turn."""

    command_type: ClassVar[object] = Pause

    def __init__(self, context: HandlerContext) -> None:
        self._context = context

    async def handle(self, command: Command) -> dict[str, object]:
        if not isinstance(command, Pause):
            reject_wrong_command("PauseHandler", command)
        task = require_task(self._context, command.task_id)
        reject_terminal(task)
        if command.now and task.status in (Status.RUNNING, Status.INTEGRATING):
            await self._context.sessions.interrupt(task)
            await self._context.cancel_task(task.id)
        if Hold.PAUSED not in task.holds:
            self._context.tasks.set_holds(task.id, task.holds | {Hold.PAUSED})
            self._context.events.append(
                EventKind.HOLD_SET,
                task_id=task.id,
                message=f"{task.id} paused",
                data={"hold": Hold.PAUSED.value, "now": command.now},
            )
        return {"task_id": task.id, "paused": True, "now": command.now}


class ResumeHandler:
    """Clear task holds and continue an operator-interrupted live session."""

    command_type: ClassVar[object] = Resume

    def __init__(self, context: HandlerContext) -> None:
        self._context = context

    async def handle(self, command: Command) -> dict[str, object]:
        if not isinstance(command, Resume):
            reject_wrong_command("ResumeHandler", command)
        task = require_task(self._context, command.task_id)
        reject_terminal(task)
        holds = task.holds
        self._context.tasks.set_holds(task.id, frozenset())
        for hold in holds:
            self._context.events.append(
                EventKind.HOLD_CLEARED,
                task_id=task.id,
                message=f"{task.id} {hold.value} hold cleared",
                data={"hold": hold.value},
            )
        if Hold.INTERVENTION in holds and task.step is not None:
            role = ACTING_ROLE[task.step]
            self._context.sessions.clear_intervention(task.id, role)
            await self._context.sessions.inject_line(task, role, "continue")
        if holds & {Hold.PAUSED, Hold.INTERVENTION} and task.status in (
            Status.RUNNING,
            Status.INTEGRATING,
        ):
            await self._context.start_task(task.id)
        return {"task_id": task.id, "paused": False, "intervention": False}


class CancelHandler:
    """Cancel a task, interrupting its current turn and retaining its branch."""

    command_type: ClassVar[object] = Cancel

    def __init__(self, context: HandlerContext) -> None:
        self._context = context

    async def handle(self, command: Command) -> dict[str, object]:
        if not isinstance(command, Cancel):
            reject_wrong_command("CancelHandler", command)
        task = require_task(self._context, command.task_id)
        reject_terminal(task)
        if task.status in (Status.RUNNING, Status.INTEGRATING):
            await self._context.sessions.interrupt(task)
            await self._context.cancel_task(task.id)
        current = self._context.tasks.get(task.id)
        if current is None:
            message = f"task {task.id} disappeared while cancelling"
            raise CommandRejectedError(message)
        self._context.tasks.apply_transition(
            task.id,
            Transition.stay(current, step=current.step, status=Status.CANCELLED),
        )
        self._context.tasks.set_slot(task.id, None)
        self._context.events.append(
            EventKind.TASK_CANCELLED,
            task_id=task.id,
            message=f"{task.id} cancelled",
            data={"reason": "cancelled by operator"},
        )
        updated = self._context.tasks.get(task.id)
        if updated is None:
            message = f"task {task.id} disappeared after cancellation"
            raise CommandRejectedError(message)
        await self._context.finish_task(updated)
        return {"task_id": task.id, "status": Status.CANCELLED.value}


class RetryHandler:
    """Cancel the current step and requeue it for an idempotent retry."""

    command_type: ClassVar[object] = Retry

    def __init__(self, context: HandlerContext) -> None:
        self._context = context

    async def handle(self, command: Command) -> dict[str, object]:
        if not isinstance(command, Retry):
            reject_wrong_command("RetryHandler", command)
        task = require_task(self._context, command.task_id)
        if task.status in (Status.DONE, Status.CANCELLED):
            message = f"{task.id} is already {task.status.value}"
            raise CommandRejectedError(message)
        if task.status in (Status.RUNNING, Status.INTEGRATING):
            await self._context.sessions.interrupt(task)
            await self._context.cancel_task(task.id)
        current = self._context.tasks.get(task.id)
        if current is None:
            message = f"task {task.id} disappeared while retrying"
            raise CommandRejectedError(message)
        counters = current.counters.model_copy(update={"consults": 0, "conflict_rounds": 0})
        transition = Transition(
            step=current.step,
            status=Status.QUEUED,
            iteration=current.iteration,
            counters=counters,
            rung_index=dict(current.rung_index),
            overrides=dict(current.overrides),
        )
        self._context.tasks.apply_transition(current.id, transition)
        self._context.tasks.set_requeued(current.id)
        return {"task_id": current.id, "status": Status.QUEUED.value}


class SetRungHandler:
    """Apply a role rung override to the next task turn."""

    command_type: ClassVar[object] = SetRung

    def __init__(self, context: HandlerContext) -> None:
        self._context = context

    async def handle(self, command: Command) -> dict[str, object]:
        if not isinstance(command, SetRung):
            reject_wrong_command("SetRungHandler", command)
        task = require_task(self._context, command.task_id)
        reject_terminal(task)
        overrides = dict(task.overrides)
        old = overrides.get(command.role)
        overrides[command.role] = command.rung
        transition = Transition(
            step=task.step,
            status=task.status,
            iteration=task.iteration,
            counters=task.counters,
            rung_index=dict(task.rung_index),
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
        return {"task_id": task.id, "role": command.role.value, "model": command.rung.model}
