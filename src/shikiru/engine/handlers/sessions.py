"""Handlers for operator messages sent to live agent sessions."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from shikiru.domain.commands import Say
from shikiru.domain.events import EventKind
from shikiru.domain.tasks import Status
from shikiru.engine.handlers.base import HandlerContext, reject_wrong_command, require_task
from shikiru.errors import CommandRejectedError
from shikiru.workspace.roots import run_dir

if TYPE_CHECKING:
    from shikiru.domain.commands import Command

MESSAGE_FILENAME_WIDTH = 3


class SayHandler:
    """Write and inject one operator message without setting intervention."""

    command_type: ClassVar[object] = Say

    def __init__(self, context: HandlerContext) -> None:
        self._context = context

    async def handle(self, command: Command) -> dict[str, object]:
        if not isinstance(command, Say):
            reject_wrong_command("SayHandler", command)
        task = require_task(self._context, command.task_id)
        if task.status not in (Status.RUNNING, Status.INTEGRATING):
            message = f"{task.id} has no live task turn"
            raise CommandRejectedError(message)
        number = self._context.messages.next_number()
        message_path = (
            run_dir(self._context.project_root, task.id)
            / "messages"
            / (f"{number:0{MESSAGE_FILENAME_WIDTH}d}.md")
        )
        message_path.parent.mkdir(parents=True, exist_ok=True)
        message_path.write_text(command.text)
        line = f"Operator message: read {message_path} and take it into account."
        await self._context.sessions.inject_line(task, command.role, line)
        self._context.events.append(
            EventKind.OPERATOR_MESSAGE,
            task_id=task.id,
            message=command.text,
            data={"role": command.role.value, "path": str(message_path)},
        )
        return {"task_id": task.id, "role": command.role.value, "path": str(message_path)}
