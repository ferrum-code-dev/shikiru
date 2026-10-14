"""Handlers for the Operator's direct access to a task's agents (03 §9)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.domain.commands import COMMAND_ID_KEY, EVENT_TEXT_KEY, HandBack, Say, TakeOver
from shikiru.domain.events import EventKind
from shikiru.engine.handlers.base import (
    HandlerContext,
    reject_terminal,
    require_command_id,
    require_task,
)
from shikiru.workspace.messages import command_message_key

if TYPE_CHECKING:
    from shikiru.engine.takeover import Takeover
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
        command_id = require_command_id(command)
        path = self._messages.queue(
            task.id, command.role, command.text, command_message_key(command_id)
        )
        if not self._context.events.command_applied(EventKind.OPERATOR_MESSAGE, command_id):
            self._context.events.append(
                EventKind.OPERATOR_MESSAGE,
                task_id=task.id,
                message=f"{task.id} message queued for {command.role.value}",
                data={
                    "role": command.role.value,
                    "path": str(path),
                    EVENT_TEXT_KEY: command.text,
                    COMMAND_ID_KEY: command_id,
                },
            )
        return {"task_id": task.id, "role": command.role.value, "path": str(path)}


class TakeOverHandler:
    """Hand a role's session to the Operator in its pane, holding only that task."""

    command_type: type[TakeOver] = TakeOver

    def __init__(self, context: HandlerContext, takeover: Takeover) -> None:
        self._context = context
        self._takeover = takeover

    async def handle(self, command: TakeOver) -> dict[str, object]:
        task = require_task(self._context, command.task_id)
        reject_terminal(task)
        await self._takeover.take_over(task, command.role, require_command_id(command))
        return {"task_id": task.id, "role": command.role.value}


class HandBackHandler:
    """Return a taken-over role's session to the engine."""

    command_type: type[HandBack] = HandBack

    def __init__(self, context: HandlerContext, takeover: Takeover) -> None:
        self._context = context
        self._takeover = takeover

    async def handle(self, command: HandBack) -> dict[str, object]:
        task = require_task(self._context, command.task_id)
        await self._takeover.hand_back(task, command.role, require_command_id(command))
        return {"task_id": task.id, "role": command.role.value}
