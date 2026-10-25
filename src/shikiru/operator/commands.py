"""Operator command acknowledgements over the typed command queue."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import timedelta
from enum import StrEnum, auto
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, PrivateAttr

from shikiru.domain.commands import (
    COMMAND_POLL_SECONDS,
    AcceptResolution,
    AnswerResolution,
    Cancel,
    CancelResolution,
    HandBack,
    Pause,
    Prioritize,
    Resolution,
    ResolutionKind,
    Resolve,
    Resume,
    Retry,
    RetryResolution,
    Say,
    SetRung,
    Stop,
    Submit,
    TakeOver,
)
from shikiru.domain.tasks import TASK_TITLE_MAX_LENGTH
from shikiru.errors import DatabaseError
from shikiru.store.commands import CommandQueue, CommandStatus
from shikiru.store.database import OperationalDatabaseError

if TYPE_CHECKING:
    from shikiru.agents.contracts import Rung
    from shikiru.clock import Clock
    from shikiru.config.settings import ComplexityName
    from shikiru.domain.commands import Command
    from shikiru.domain.tasks import Mode, Role

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
    _submitted_task_id: str | None = PrivateAttr(default=None)

    @property
    def submitted_task_id(self) -> str | None:
        """Return the task identity allocated for a submitted command, when applicable."""
        return self._submitted_task_id

    @classmethod
    def from_command(
        cls,
        command: Command,
        *,
        command_id: int,
        status: AckStatus,
        result: dict[str, Any] | None,
        message: str | None,
    ) -> Ack:
        """Create an acknowledgement while retaining submitted task identity for human output."""
        ack = cls(id=command_id, status=status, result=result, message=message)
        if isinstance(command, Submit):
            ack._submitted_task_id = command.task_id
        return ack


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
    ) -> None:
        """Use `queue` and `clock` for command and acknowledgement boundaries."""
        self._queue = queue
        self._clock = clock
        self._engine_up = engine_up

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
                return Ack.from_command(
                    queued.command,
                    command_id=outcome.id,
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
        return await self.send(
            self.prepare_submit_task(
                request,
                title=title,
                mode=mode,
                complexity=complexity,
                priority=priority,
                depends_on=depends_on,
                leader=leader,
                implementer=implementer,
            )
        )

    def prepare_submit_task(
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
    ) -> Submit:
        """Prepare the typed submission used by both palette recovery and dispatch."""
        task_title = title if title is not None else _title_from_request(request)
        return Submit(
            title=task_title,
            request=request,
            mode=mode,
            complexity=complexity,
            priority=priority,
            depends_on=list(depends_on),
            leader=leader,
            implementer=implementer,
        )

    async def prioritize_task(self, task_id: str, priority: int) -> Ack:
        """Queue a `Prioritize` command."""
        return await self.send(self.prepare_prioritize_task(task_id, priority))

    def prepare_prioritize_task(self, task_id: str, priority: int) -> Prioritize:
        """Prepare the typed priority command used by the palette and dispatcher."""
        return Prioritize(task_id=task_id, priority=priority)

    async def pause_task(self, task_id: str, *, now: bool = False) -> Ack:
        """Queue a `Pause` command."""
        return await self.send(self.prepare_pause_task(task_id, now=now))

    def prepare_pause_task(self, task_id: str, *, now: bool = False) -> Pause:
        """Prepare the typed pause command used by the palette and dispatcher."""
        return Pause(task_id=task_id, now=now)

    async def resume_task(self, task_id: str) -> Ack:
        """Queue a `Resume` command."""
        return await self.send(self.prepare_resume_task(task_id))

    def prepare_resume_task(self, task_id: str) -> Resume:
        """Prepare the typed resume command used by the palette and dispatcher."""
        return Resume(task_id=task_id)

    async def cancel_task(self, task_id: str) -> Ack:
        """Queue a `Cancel` command."""
        return await self.send(self.prepare_cancel_task(task_id))

    def prepare_cancel_task(self, task_id: str) -> Cancel:
        """Prepare the typed cancel command used by the palette and dispatcher."""
        return Cancel(task_id=task_id)

    async def retry_task(self, task_id: str) -> Ack:
        """Queue a `Retry` command."""
        return await self.send(self.prepare_retry_task(task_id))

    def prepare_retry_task(self, task_id: str) -> Retry:
        """Prepare the typed retry command used by the palette and dispatcher."""
        return Retry(task_id=task_id)

    async def set_rung(self, task_id: str, role: Role, rung: Rung) -> Ack:
        """Queue a `SetRung` command."""
        return await self.send(self.prepare_set_rung(task_id, role, rung))

    def prepare_set_rung(self, task_id: str, role: Role, rung: Rung) -> SetRung:
        """Prepare the typed rung command used by the palette and dispatcher."""
        return SetRung(task_id=task_id, role=role, rung=rung)

    async def send_message(self, task_id: str, role: Role, text: str) -> Ack:
        """Queue a `Say` command for the role's next turn."""
        return await self.send(self.prepare_send_message(task_id, role, text))

    def prepare_send_message(self, task_id: str, role: Role, text: str) -> Say:
        """Prepare the typed guidance command used by the palette and dispatcher."""
        return Say(task_id=task_id, role=role, text=text)

    async def take_over(self, task_id: str, role: Role) -> Ack:
        """Queue a `TakeOver` command for the role's pane."""
        return await self.send(self.prepare_take_over(task_id, role))

    def prepare_take_over(self, task_id: str, role: Role) -> TakeOver:
        """Prepare the typed takeover command used by the palette and dispatcher."""
        return TakeOver(task_id=task_id, role=role)

    async def hand_back(self, task_id: str, role: Role) -> Ack:
        """Queue a `HandBack` command returning the role's session to the engine."""
        return await self.send(self.prepare_hand_back(task_id, role))

    def prepare_hand_back(self, task_id: str, role: Role) -> HandBack:
        """Prepare the typed hand-back command used by the palette and dispatcher."""
        return HandBack(task_id=task_id, role=role)

    async def resolve_escalation(self, escalation_id: str, resolution: Resolution) -> Ack:
        """Queue a `Resolve` command for one open escalation."""
        return await self.send(self.prepare_resolve_escalation(escalation_id, resolution))

    def prepare_resolve_escalation(
        self,
        escalation_id: str,
        resolution: Resolution,
    ) -> Resolve:
        """Prepare the typed resolution command used by the palette and dispatcher."""
        return Resolve(escalation_id=escalation_id, resolution=resolution)

    def prepare_palette_resolution(
        self,
        escalation_id: str,
        kind: ResolutionKind,
        *,
        answer: str,
        retry_rung: Rung | None,
    ) -> Resolve:
        """Prepare one resolution command from the palette's selected form fields."""
        if kind is ResolutionKind.ANSWER:
            resolution: Resolution = AnswerResolution(text=answer)
        elif kind is ResolutionKind.RETRY:
            resolution = RetryResolution(rung=retry_rung)
        elif kind is ResolutionKind.ACCEPT:
            resolution = AcceptResolution()
        else:
            resolution = CancelResolution()
        return self.prepare_resolve_escalation(escalation_id, resolution)

    async def stop_engine(self) -> Ack:
        """Queue the orderly engine `Stop` command."""
        return await self.send(self.prepare_stop_engine())

    def prepare_stop_engine(self) -> Stop:
        """Prepare the typed stop command used by the palette and dispatcher."""
        return Stop()


def _title_from_request(request: str) -> str:
    first_line = request.splitlines()[0] if request.splitlines() else request
    return first_line[:TASK_TITLE_MAX_LENGTH]


def _queued_ack(command_id: int, command: Command, message: str) -> Ack:
    result = {"task_id": command.task_id} if isinstance(command, Submit) else None
    return Ack.from_command(
        command,
        command_id=command_id,
        status=AckStatus.QUEUED,
        result=result,
        message=message,
    )


def _result_message(result: dict[str, Any] | None) -> str | None:
    if result is None:
        return None
    error = result.get("error")
    return str(error) if error is not None else None
