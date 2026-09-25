"""Handlers for the Operator's direct access to a task's agents (03 §9)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.domain.commands import Say
from shikiru.domain.events import EventKind
from shikiru.engine.handlers.base import HandlerContext, reject_terminal, require_task

if TYPE_CHECKING:
    from shikiru.workspace.messages import OperatorMessages


class SayHandler:
    """Queue an Operator message for a role's next turn without holding the task."""

    command_type: type[Say] = Say

    def __init__(self, context: HandlerContext, messages: OperatorMessages) -> None:
        self._context = context
        self._messages = messages

    async def handle(self, command: Say) -> dict[str, object]:
        task = require_task(self._context, command.task_id)
        reject_terminal(task)
        path = self._messages.queue(task.id, command.role, command.text)
        self._context.events.append(
            EventKind.OPERATOR_MESSAGE,
            task_id=task.id,
            message=f"{task.id} message queued for {command.role.value}",
            data={"role": command.role.value, "path": str(path)},
        )
        return {"task_id": task.id, "role": command.role.value, "path": str(path)}
