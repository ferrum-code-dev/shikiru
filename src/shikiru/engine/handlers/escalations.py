"""Handlers for escalation resolution options (03 §8)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.domain.commands import (
    AcceptResolution,
    AnswerResolution,
    CancelResolution,
    ResolutionKind,
    Resolve,
    RetryResolution,
)
from shikiru.domain.escalation import EscalationStatus, transition_for_resolution
from shikiru.domain.events import EventKind
from shikiru.domain.steps import ACTING_ROLE, Transition
from shikiru.domain.tasks import EscalationReason, Status
from shikiru.engine.handlers.base import (
    HandlerContext,
    interrupt_task,
    refresh_titles,
    require_task,
)
from shikiru.errors import CommandRejectedError

if TYPE_CHECKING:
    from shikiru.domain.escalation import Escalation
    from shikiru.domain.tasks import Task


class ResolveHandler:
    """Apply `answer`, `retry`, `accept` or `cancel` to an open escalation."""

    command_type: type[Resolve] = Resolve

    def __init__(self, context: HandlerContext) -> None:
        self._context = context

    async def handle(self, command: Resolve) -> dict[str, object]:
        escalation = self._open_escalation(command)
        task = require_task(self._context, escalation.task_id)
        transition = self._transition(task, escalation, command)
        if not isinstance(command.resolution, AnswerResolution):
            await interrupt_task(self._context, task, even_if_inactive=True)
        self._apply_transition(task, escalation, command, transition)
        await refresh_titles(self._context, task.id)
        self._context.escalations.resolve(escalation.id, self._resolution_text(command))
        self._context.events.append(
            EventKind.ESCALATION_RESOLVED,
            task_id=task.id,
            message=f"{escalation.id} resolved with {command.resolution.kind.value}",
            data={"escalation_id": escalation.id, "resolution": command.resolution.kind.value},
        )
        if isinstance(command.resolution, CancelResolution):
            await self._finish_cancel(task)
        return {
            "escalation_id": escalation.id,
            "task_id": task.id,
            "status": transition.status.value,
        }

    def _open_escalation(self, command: Resolve) -> Escalation:
        escalation = self._context.escalations.get(command.escalation_id)
        if escalation is None:
            message = f"escalation {command.escalation_id} does not exist"
            raise CommandRejectedError(message)
        if escalation.status is not EscalationStatus.OPEN:
            message = f"escalation {command.escalation_id} is already resolved"
            raise CommandRejectedError(message)
        if command.resolution.kind not in {option.label for option in escalation.options}:
            message = (
                f"resolution {command.resolution.kind.value} is not valid for "
                f"{escalation.reason.value}"
            )
            raise CommandRejectedError(message)
        return escalation

    def _transition(self, task: Task, escalation: Escalation, command: Resolve) -> Transition:
        acting_role = ACTING_ROLE.get(escalation.step) if escalation.step is not None else None
        rung = command.resolution.rung if isinstance(command.resolution, RetryResolution) else None
        try:
            return transition_for_resolution(
                task,
                escalation.reason,
                step=escalation.step,
                resolution=command.resolution.kind,
                rung=rung,
                role=acting_role,
            )
        except ValueError as error:
            raise CommandRejectedError(str(error)) from error

    def _apply_transition(
        self,
        task: Task,
        escalation: Escalation,
        command: Resolve,
        transition: Transition,
    ) -> None:
        if (
            isinstance(command.resolution, RetryResolution)
            and escalation.reason is EscalationReason.DEPENDENCY_CANCELLED
        ):
            self._drop_cancelled_dependencies(task.id, task.depends_on)
        self._context.tasks.apply_transition(task.id, transition)
        if transition.status is Status.CANCELLED:
            self._context.tasks.set_slot(task.id, None)
        if transition.status is not Status.CANCELLED:
            self._context.tasks.set_requeued(task.id)

    async def _finish_cancel(self, task: Task) -> None:
        updated = self._context.tasks.get(task.id)
        if updated is None:
            message = f"task {task.id} disappeared after cancellation"
            raise CommandRejectedError(message)
        self._context.events.append(
            EventKind.TASK_CANCELLED,
            task_id=task.id,
            message=f"{task.id} cancelled",
            data={"reason": "cancelled by operator"},
        )
        await self._context.finish_task(updated)

    def _drop_cancelled_dependencies(self, task_id: str, dependencies: list[str]) -> None:
        keep: list[str] = []
        for dependency_id in dependencies:
            dependency = self._context.tasks.get(dependency_id)
            if dependency is not None and dependency.status is not Status.CANCELLED:
                keep.append(dependency_id)
        self._context.tasks.set_depends_on(task_id, keep)

    @staticmethod
    def _resolution_text(command: Resolve) -> str:
        resolution = command.resolution
        if isinstance(resolution, AnswerResolution):
            return f"{ResolutionKind.ANSWER.value} {resolution.text}"
        if isinstance(resolution, RetryResolution):
            rung = resolution.rung
            return "retry" + (f" {rung.agent}:{rung.model}" if rung else "")
        if isinstance(resolution, AcceptResolution):
            return "accept"
        return "cancel"
