"""Command handler contracts and dependency context."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, TypeVar

from shikiru.domain.commands import Command, IdentifiedCommand
from shikiru.domain.events import EventKind
from shikiru.domain.tasks import Status, Task
from shikiru.errors import CommandRejectedError

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

    from shikiru.store.escalations import EscalationStore
    from shikiru.store.events import EventLog
    from shikiru.store.runs import RunStore
    from shikiru.store.tasks import TaskStore


TaskCallback = Callable[[str], Awaitable[None]]
TerminalCallback = Callable[[Task], Awaitable[Path | None]]
RefreshCallback = Callable[[Task], Awaitable[None]]

KEPT_WORKTREE_KEY = "kept_worktree"
_LOGGER = logging.getLogger(__name__)


@dataclass
class HandlerContext:
    """Collaborators shared by command handlers."""

    tasks: TaskStore
    escalations: EscalationStore
    events: EventLog
    runs: RunStore
    project_root: Path
    cancel_task: TaskCallback
    start_task: TaskCallback
    finish_task: TerminalCallback
    stop_engine: Callable[[], None]
    refresh_titles: RefreshCallback
    atomically: Callable[[], AbstractContextManager[object]]


CommandT = TypeVar("CommandT", bound=Command)


class CommandHandler(Protocol[CommandT]):
    """Handle one typed domain command and return a JSON-compatible result."""

    command_type: type[CommandT]

    async def handle(self, command: CommandT) -> dict[str, object]:
        """Apply `command` and return its acknowledgement result."""
        ...


class HandlerRegistry:
    """Dispatches each command model to exactly one registered handler."""

    def __init__(self, handlers: list[CommandHandler[Any]]) -> None:
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


def reject_terminal(task: Task) -> None:
    """Reject a command that targets a terminal task."""
    if task.status in (Status.DONE, Status.CANCELLED):
        message = f"{task.id} is already {task.status.value}"
        raise CommandRejectedError(message)


async def refresh_titles(context: HandlerContext, task_id: str) -> None:
    """Refresh one task's tmux titles; a presentation failure is logged, never a rejection."""
    task = context.tasks.get(task_id)
    if task is None:
        message = f"task {task_id} disappeared while refreshing titles"
        raise CommandRejectedError(message)
    try:
        await context.refresh_titles(task)
    except Exception:
        _LOGGER.exception("title refresh failed for %s", task_id)


async def finish_task_quietly(context: HandlerContext, task: Task) -> Path | None:
    """Run terminal cleanup after a committed terminal state; a failure never rejects the command.

    Reconcile repeats the cleanup of every terminal task whose root remains (03 §7).
    """
    try:
        return await context.finish_task(task)
    except Exception as error:
        _LOGGER.exception("terminal cleanup failed for %s", task.id)
        context.events.append(
            EventKind.ENGINE_FAULT,
            task_id=task.id,
            message=f"{task.id} cleanup after {task.status.value} failed: {error}",
            data={"error": type(error).__name__, "recoverable": True},
        )
        return None


async def interrupt_task(
    context: HandlerContext, task: Task, *, even_if_inactive: bool = False
) -> None:
    """Stop a task flow before a handler changes its current step or status."""
    if even_if_inactive or task.status in (Status.RUNNING, Status.INTEGRATING):
        await context.cancel_task(task.id)


def kept_worktree_result(kept: Path | None) -> dict[str, object]:
    """Tell the operator where uncommitted work kept a cancelled task's worktree (03 §8)."""
    return {} if kept is None else {KEPT_WORKTREE_KEY: str(kept)}


def command_replayed(context: HandlerContext, kind: EventKind, command: IdentifiedCommand) -> bool:
    """Report whether `command` already took effect, which only a `kind` event naming it proves."""
    return context.events.command_applied(kind, require_command_id(command))


def require_command_id(command: IdentifiedCommand) -> int:
    """Return the id the command queue gave `command`, which every replay of it shares."""
    if command.command_id is None:
        message = f"{type(command).__name__} command has no queue id"
        raise ValueError(message)
    return command.command_id
