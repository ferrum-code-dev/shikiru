"""Command handler contracts and dependency context."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, NoReturn, Protocol

from shikiru.domain.tasks import Status, Task
from shikiru.errors import CommandRejectedError

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.domain.commands import Command
    from shikiru.engine.sessions import SessionManager
    from shikiru.store.escalations import EscalationStore
    from shikiru.store.events import EventLog
    from shikiru.store.messages import MessageStore
    from shikiru.store.tasks import TaskStore


TaskCallback = Callable[[str], Awaitable[None]]
TerminalCallback = Callable[[Task], Awaitable[None]]
ConsultCallback = Callable[[str, str], None]


@dataclass
class HandlerContext:
    """Collaborators shared by command handlers."""

    tasks: TaskStore
    escalations: EscalationStore
    events: EventLog
    messages: MessageStore
    sessions: SessionManager
    project_root: Path
    cancel_task: TaskCallback
    start_task: TaskCallback
    finish_task: TerminalCallback
    set_consult_question: ConsultCallback
    stop_engine: Callable[[], None]


class CommandHandler(Protocol):
    """Handle one typed domain command and return a JSON-compatible result."""

    command_type: ClassVar[object]

    async def handle(self, command: Command) -> dict[str, object]:
        """Apply `command` and return its acknowledgement result."""
        ...


class HandlerRegistry:
    """Dispatches each command model to exactly one registered handler."""

    def __init__(self, handlers: list[CommandHandler]) -> None:
        """Index handlers by their concrete command model type."""
        self._handlers = {handler.command_type: handler for handler in handlers}

    async def handle(self, command: Command) -> dict[str, object]:
        """Handle `command`, rejecting an unregistered model explicitly."""
        handler = self._handlers.get(type(command))
        if handler is None:
            message = f"no handler registered for {type(command).__name__}"
            raise CommandRejectedError(message)
        return await handler.handle(command)


def require_task(context: HandlerContext, task_id: str) -> Task:
    """Load `task_id` or reject the command with an operator-facing error."""
    task = context.tasks.get(task_id)
    if task is None:
        message = f"task {task_id} does not exist"
        raise CommandRejectedError(message)
    return task


def reject_wrong_command(handler: str, command: Command) -> NoReturn:
    """Reject a handler call whose model does not match its registry entry."""
    message = f"{handler} received the wrong command model: {type(command).__name__}"
    raise CommandRejectedError(message)


def reject_terminal(task: Task) -> None:
    """Reject a command that targets a terminal task."""
    if task.status in (Status.DONE, Status.CANCELLED):
        message = f"{task.id} is already {task.status.value}"
        raise CommandRejectedError(message)
