"""Operator command models, discriminated by `kind` for storage (docs/design/05-data.md §2)."""

from __future__ import annotations

from enum import StrEnum, auto
from typing import Annotated, Literal

from pydantic import Field

from shikiru.agents.contracts import Rung
from shikiru.config.settings import ComplexityName
from shikiru.domain.tasks import DomainModel, Mode, Role

COMMAND_ID_KEY = "command_id"
EVENT_TEXT_KEY = "text"
COMMAND_POLL_SECONDS = 0.25
COMMAND_RESULT_ERROR_KEY = "error"
COMMAND_REJECTED_FALLBACK = "engine rejected the command"


class CommandStatus(StrEnum):
    """The `commands.status` lifecycle (05 §2)."""

    PENDING = auto()
    DONE = auto()
    REJECTED = auto()


class CommandKind(StrEnum):
    """The `commands.kind` discriminator (05 §2)."""

    SUBMIT = auto()
    PRIORITIZE = auto()
    PAUSE = auto()
    RESUME = auto()
    CANCEL = auto()
    RETRY = auto()
    SET_RUNG = auto()
    SAY = auto()
    TAKE_OVER = auto()
    HAND_BACK = auto()
    RESOLVE = auto()
    STOP = auto()


class IdentifiedCommand(DomainModel):
    """A command whose effects are recognised on replay by the queue row id it is read with."""

    command_id: int | None = None


class Submit(DomainModel):
    """`submit "<request>" [flags]`: the queue allocates the task id when appending it."""

    kind: Literal[CommandKind.SUBMIT] = CommandKind.SUBMIT
    task_id: str | None = None
    title: str
    request: str
    mode: Mode | None = None
    complexity: ComplexityName | None = None
    priority: int = 0
    depends_on: list[str] = Field(default_factory=list)
    leader: Rung | None = None
    implementer: Rung | None = None


class Prioritize(DomainModel):
    """`prioritize T-n N`: sets a task's dispatch priority."""

    kind: Literal[CommandKind.PRIORITIZE] = CommandKind.PRIORITIZE
    task_id: str
    priority: int


class Pause(DomainModel):
    """`pause T-n [--now]`: holds a task; `now` also interrupts and cancels the current step."""

    kind: Literal[CommandKind.PAUSE] = CommandKind.PAUSE
    task_id: str
    now: bool = False


class Resume(DomainModel):
    """`resume T-n`: clears the `paused` hold, or injects `continue` when it was `intervention`."""

    kind: Literal[CommandKind.RESUME] = CommandKind.RESUME
    task_id: str


class Cancel(IdentifiedCommand):
    """`cancel T-n`: cancels a task and keeps its branch."""

    kind: Literal[CommandKind.CANCEL] = CommandKind.CANCEL
    task_id: str


class Retry(IdentifiedCommand):
    """`retry T-n`: cancels and re-runs the task's current step."""

    kind: Literal[CommandKind.RETRY] = CommandKind.RETRY
    task_id: str


class SetRung(DomainModel):
    """`rung T-n <role> <agent:model:effort>`: overrides a role's rung from the next turn."""

    kind: Literal[CommandKind.SET_RUNG] = CommandKind.SET_RUNG
    task_id: str
    role: Role
    rung: Rung


class Say(IdentifiedCommand):
    """`say T-n <role> "<text>"`: queues a message for the role's next turn.

    The queue fills `command_id` so a replayed delivery is recognised.
    """

    kind: Literal[CommandKind.SAY] = CommandKind.SAY
    task_id: str
    role: Role
    text: str


class TakeOver(IdentifiedCommand):
    """`take-over T-n <role>`: stops the turn and opens the role's session in its pane."""

    kind: Literal[CommandKind.TAKE_OVER] = CommandKind.TAKE_OVER
    task_id: str
    role: Role


class HandBack(IdentifiedCommand):
    """`hand-back T-n <role>`: returns a taken-over session to the engine.

    The queue fills `command_id` so a replayed delivery is recognised.
    """

    kind: Literal[CommandKind.HAND_BACK] = CommandKind.HAND_BACK
    task_id: str
    role: Role


class ResolutionKind(StrEnum):
    """The `resolve` answer's discriminator."""

    ANSWER = auto()
    RETRY = auto()
    ACCEPT = auto()
    CANCEL = auto()


class AnswerResolution(DomainModel):
    """`resolve E-n <answer TEXT>`: runs a consult turn with the Operator's text."""

    kind: Literal[ResolutionKind.ANSWER] = ResolutionKind.ANSWER
    text: str


class RetryResolution(DomainModel):
    """`resolve E-n retry [--rung a:m:e]`: re-runs the failed step, optionally on a new rung."""

    kind: Literal[ResolutionKind.RETRY] = ResolutionKind.RETRY
    rung: Rung | None = None


class AcceptResolution(DomainModel):
    """`resolve E-n accept`: integrates the task as-is."""

    kind: Literal[ResolutionKind.ACCEPT] = ResolutionKind.ACCEPT


class CancelResolution(DomainModel):
    """`resolve E-n cancel`: cancels the task and keeps its branch."""

    kind: Literal[ResolutionKind.CANCEL] = ResolutionKind.CANCEL


Resolution = Annotated[
    AnswerResolution | RetryResolution | AcceptResolution | CancelResolution,
    Field(discriminator="kind"),
]


class Resolve(IdentifiedCommand):
    """`resolve E-n <...>`: resolves an open escalation."""

    kind: Literal[CommandKind.RESOLVE] = CommandKind.RESOLVE
    escalation_id: str
    resolution: Resolution


class Stop(DomainModel):
    """`stop`: stops the engine; panes and their turns keep running."""

    kind: Literal[CommandKind.STOP] = CommandKind.STOP


Command = Annotated[
    Submit
    | Prioritize
    | Pause
    | Resume
    | Cancel
    | Retry
    | SetRung
    | Say
    | TakeOver
    | HandBack
    | Resolve
    | Stop,
    Field(discriminator="kind"),
]


def command_rejection_cause(result: dict[str, object] | None) -> str:
    """Return the engine's `error` text from a rejected command's result, else a fixed phrase."""
    error = None if result is None else result.get(COMMAND_RESULT_ERROR_KEY)
    return error if isinstance(error, str) else COMMAND_REJECTED_FALLBACK
