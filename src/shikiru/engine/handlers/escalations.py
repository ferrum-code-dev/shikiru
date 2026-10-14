"""Handlers for escalation resolution options (03 §8)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.domain.commands import (
    COMMAND_ID_KEY,
    EVENT_TEXT_KEY,
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
    command_replayed,
    finish_task_quietly,
    interrupt_task,
    kept_worktree_result,
    refresh_titles,
    require_command_id,
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
        if command_replayed(self._context, EventKind.ESCALATION_RESOLVED, command):
            escalation = self._escalation(command.escalation_id)
        else:
            escalation = self._open_escalation(command)
            await self._apply(command, escalation)
        task = require_task(self._context, escalation.task_id)
        await refresh_titles(self._context, task.id)
        kept = (
            await finish_task_quietly(self._context, task)
            if isinstance(command.resolution, CancelResolution)
            else None
        )
        return {
            "escalation_id": escalation.id,
            "task_id": task.id,
            "status": task.status.value,
            **kept_worktree_result(kept),
        }

    async def _apply(self, command: Resolve, escalation: Escalation) -> None:
        task = require_task(self._context, escalation.task_id)
        if task.status is not Status.ESCALATED:
            message = (
                f"{escalation.id} no longer applies: {task.id} is {task.status.value}, "
                "not escalated"
            )
            raise CommandRejectedError(message)
        transition = self._transition(task, escalation, command)
        if not isinstance(command.resolution, AnswerResolution):
            await interrupt_task(self._context, task, even_if_inactive=True)
        with self._context.atomically():
            self._apply_transition(task, escalation, command, transition)
            self._context.escalations.resolve(escalation.id, self._resolution_text(command))
            self._context.events.append(
                EventKind.ESCALATION_RESOLVED,
                task_id=task.id,
                message=f"{escalation.id} resolved with {command.resolution.kind.value}",
                data={
                    "escalation_id": escalation.id,
                    "resolution": command.resolution.kind.value,
                    **self._answer_text(command),
                    COMMAND_ID_KEY: require_command_id(command),
                },
            )

    def _escalation(self, escalation_id: str) -> Escalation:
        escalation = self._context.escalations.get(escalation_id)
        if escalation is None:
            message = f"escalation {escalation_id} does not exist"
            raise CommandRejectedError(message)
        return escalation

    def _open_escalation(self, command: Resolve) -> Escalation:
        escalation = self._escalation(command.escalation_id)
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
        if transition.status is Status.CANCELLED:
            self._context.tasks.finish(
                task.id,
                transition,
                EventKind.TASK_CANCELLED,
                message=f"{task.id} cancelled",
                data={"reason": "cancelled by operator"},
            )
            return
        self._context.tasks.apply_transition(task.id, transition)
        self._context.tasks.set_requeued(task.id)

    def _drop_cancelled_dependencies(self, task_id: str, dependencies: list[str]) -> None:
        keep: list[str] = []
        for dependency_id in dependencies:
            dependency = self._context.tasks.get(dependency_id)
            if dependency is not None and dependency.status is not Status.CANCELLED:
                keep.append(dependency_id)
        self._context.tasks.set_depends_on(task_id, keep)

    @staticmethod
    def _answer_text(command: Resolve) -> dict[str, str]:
        resolution = command.resolution
        return {EVENT_TEXT_KEY: resolution.text} if isinstance(resolution, AnswerResolution) else {}

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
