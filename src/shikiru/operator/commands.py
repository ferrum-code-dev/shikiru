"""Operator command acknowledgements over the typed command queue (06 §1)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import timedelta
from enum import StrEnum, auto
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

from shikiru.domain.commands import (
    COMMAND_POLL_SECONDS,
    Cancel,
    HandBack,
    Pause,
    Prioritize,
    Resolution,
    Resolve,
    Resume,
    Retry,
    Say,
    SetRung,
    Stop,
    Submit,
    TakeOver,
)
from shikiru.domain.tasks import TASK_TITLE_MAX_LENGTH
from shikiru.errors import ConfigError, DatabaseError
from shikiru.store.commands import CommandQueue, CommandStatus
from shikiru.store.database import OperationalDatabaseError

if TYPE_CHECKING:
    from shikiru.agents.contracts import Rung
    from shikiru.clock import Clock
    from shikiru.config.settings import ComplexityName
    from shikiru.domain.commands import Command
    from shikiru.domain.tasks import Mode, Role
    from shikiru.operator.queries import OperatorQueries, TaskActionAvailability

COMMAND_ACK_WAIT_SECONDS = 10.0
QUEUED_MESSAGE = "applies when the engine starts"
ACK_TIMEOUT_MESSAGE = (
    f"engine did not acknowledge the command within {COMMAND_ACK_WAIT_SECONDS:g} seconds"
)

EngineState = Callable[[], bool]


class AckStatus(StrEnum):
    """The operator-visible command lifecycle, including an engine-down queue state."""

    DONE = auto()
    REJECTED = auto()
    QUEUED = auto()


class Ack(BaseModel):
    """The result returned after a command is queued or handled by the engine."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: int
    status: AckStatus
    result: dict[str, Any] | None = None
    message: str | None = None


class OperatorCommands:
    """Append typed commands and wait briefly for a running engine's acknowledgement.

    A database that stays locked past its busy timeout raises `DatabaseError`; nothing is queued.
    """

    def __init__(
        self,
        queue: CommandQueue,
        clock: Clock,
        *,
        engine_up: EngineState,
        queries: OperatorQueries | None = None,
    ) -> None:
        """Use `queue`, `clock` and current queries for command and action state boundaries."""
        self._queue = queue
        self._clock = clock
        self._engine_up = engine_up
        self._queries = queries

    def task_action_availability(
        self, task_id: str, role: Role
    ) -> tuple[TaskActionAvailability, ...]:
        """Return current task action availability from the injected operator query service."""
        if self._queries is None:
            message = "task action availability requires operator queries"
            raise ConfigError(message)
        return self._queries.task_action_availability(task_id, role)

    async def send(self, command: Command) -> Ack:
        """Queue `command`, returning done/rejected or queued when the engine is down."""
        try:
            queued = self._queue.append(command)
        except OperationalDatabaseError as error:
            message = f"the state database is busy and the command was not queued: {error}"
            raise DatabaseError(message) from error
        if not self._engine_up():
            return _queued_ack(queued.id, queued.command, QUEUED_MESSAGE)
        deadline = self._clock.now() + timedelta(seconds=COMMAND_ACK_WAIT_SECONDS)
        while True:
            outcome = self._queue.status(queued.id)
            if outcome is not None and outcome.status is not CommandStatus.PENDING:
                status = (
                    AckStatus.DONE if outcome.status is CommandStatus.DONE else AckStatus.REJECTED
                )
                message = _result_message(outcome.result)
                return Ack(
                    id=outcome.id,
                    status=status,
                    result=outcome.result,
                    message=message,
                )
            if self._clock.now() >= deadline:
                return _queued_ack(queued.id, queued.command, ACK_TIMEOUT_MESSAGE)
            await self._clock.sleep(COMMAND_POLL_SECONDS)

    async def submit_task(
        self,
        request: str,
        *,
        title: str | None = None,
        mode: Mode | None = None,
        complexity: ComplexityName | None = None,
        priority: int = 0,
        depends_on: Sequence[str] = (),
        leader: Rung | None = None,
        implementer: Rung | None = None,
    ) -> Ack:
        """Queue a `Submit` command, allocating its task id atomically in the store."""
        task_title = title if title is not None else _title_from_request(request)
        return await self.send(
            Submit(
                title=task_title,
                request=request,
                mode=mode,
                complexity=complexity,
                priority=priority,
                depends_on=list(depends_on),
                leader=leader,
                implementer=implementer,
            )
        )

    async def prioritize_task(self, task_id: str, priority: int) -> Ack:
        """Queue a `Prioritize` command."""
        return await self.send(Prioritize(task_id=task_id, priority=priority))

    async def pause_task(self, task_id: str, *, now: bool = False) -> Ack:
        """Queue a `Pause` command."""
        return await self.send(Pause(task_id=task_id, now=now))

    async def resume_task(self, task_id: str) -> Ack:
        """Queue a `Resume` command."""
        return await self.send(Resume(task_id=task_id))

    async def cancel_task(self, task_id: str) -> Ack:
        """Queue a `Cancel` command."""
        return await self.send(Cancel(task_id=task_id))

    async def retry_task(self, task_id: str) -> Ack:
        """Queue a `Retry` command."""
        return await self.send(Retry(task_id=task_id))

    async def set_rung(self, task_id: str, role: Role, rung: Rung) -> Ack:
        """Queue a `SetRung` command."""
        return await self.send(SetRung(task_id=task_id, role=role, rung=rung))

    async def send_message(self, task_id: str, role: Role, text: str) -> Ack:
        """Queue a `Say` command for the role's next turn."""
        return await self.send(Say(task_id=task_id, role=role, text=text))

    async def take_over(self, task_id: str, role: Role) -> Ack:
        """Queue a `TakeOver` command for the role's pane."""
        return await self.send(TakeOver(task_id=task_id, role=role))

    async def hand_back(self, task_id: str, role: Role) -> Ack:
        """Queue a `HandBack` command returning the role's session to the engine."""
        return await self.send(HandBack(task_id=task_id, role=role))

    async def resolve_escalation(self, escalation_id: str, resolution: Resolution) -> Ack:
        """Queue a `Resolve` command for one open escalation."""
        return await self.send(Resolve(escalation_id=escalation_id, resolution=resolution))

    async def stop_engine(self) -> Ack:
        """Queue the orderly engine `Stop` command."""
        return await self.send(Stop())


def _title_from_request(request: str) -> str:
    first_line = request.splitlines()[0] if request.splitlines() else request
    return first_line[:TASK_TITLE_MAX_LENGTH]


def _queued_ack(command_id: int, command: Command, message: str) -> Ack:
    result = {"task_id": command.task_id} if isinstance(command, Submit) else None
    return Ack(id=command_id, status=AckStatus.QUEUED, result=result, message=message)


def _result_message(result: dict[str, Any] | None) -> str | None:
    if result is None:
        return None
    error = result.get("error")
    return str(error) if error is not None else None
