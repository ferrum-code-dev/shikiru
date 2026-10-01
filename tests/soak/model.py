"""The vocabulary the soak parts share: what is injected, what is violated, what is planned."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum, auto
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from shikiru.domain.tasks import Mode


FakeStep = dict[str, Any]
Scripts = dict[str, list[FakeStep]]


class Timed(Protocol):
    @property
    def offset_seconds(self) -> float: ...


class FaultKind(StrEnum):
    ENGINE_KILL = auto()
    ENGINE_KILL_LOCKED = auto()
    ENGINE_KILL_INTEGRATING = auto()
    ENGINE_KILL_ESCALATING = auto()
    ENGINE_TERM = auto()
    ENGINE_INT = auto()
    ENGINE_STOP_START = auto()
    ENGINE_FREEZE = auto()
    TMUX_LOSS = auto()
    TMUX_LOSS_TAKEOVER = auto()
    REMOTE_DOWN = auto()
    TURN_KILL = auto()
    DB_LOCK = auto()
    GH_FAIL = auto()
    GIT_HOOK_FAIL = auto()
    HOOK_STUCK = auto()
    HOOK_FAIL = auto()


class IntegrationMode(StrEnum):
    MERGE = "merge"
    PR = "pr"


class TaskKind(StrEnum):
    SIMPLE = auto()
    REJECTED = auto()
    ROUNDS_EXHAUSTED = auto()
    AUTH_FAILURE = auto()
    OTHER_FAILURE = auto()
    DECISION = auto()
    PLAN = auto()
    SUPERSEDED = auto()
    UNCOMMITTED = auto()
    UNAVAILABLE = auto()
    QUOTA = auto()
    PROTOCOL = auto()
    STALL = auto()


@dataclass(frozen=True, slots=True)
class ScheduledFault:
    offset_seconds: float
    kind: FaultKind
    duration_seconds: float


class OperatorKind(StrEnum):
    SAY = auto()
    PRIORITIZE = auto()
    PAUSE = auto()
    RESUME = auto()
    SET_RUNG = auto()
    TAKE_OVER = auto()
    HAND_BACK = auto()
    CANCEL = auto()
    RETRY = auto()


@dataclass(frozen=True, slots=True)
class ScheduledOperation:
    offset_seconds: float
    kind: OperatorKind
    pick: float


@dataclass(frozen=True, slots=True)
class TaskPlan:
    kind: TaskKind
    request: str
    mode: Mode | None
    priority: int
    after: tuple[str, ...]
    scripts: Scripts


@dataclass(frozen=True, slots=True)
class Violation:
    invariant: int
    task_id: str | None
    message: str

    def __str__(self) -> str:
        subject = self.task_id or "project"
        return f"invariant {self.invariant} [{subject}]: {self.message}"


@dataclass(slots=True)
class Tally:
    submitted: int = 0
    commands: dict[str, int] = field(default_factory=dict)
    faults: dict[str, int] = field(default_factory=dict)

    def command(self, kind: str) -> None:
        self.commands[kind] = self.commands.get(kind, 0) + 1

    def fault(self, kind: FaultKind) -> None:
        self.faults[kind.value] = self.faults.get(kind.value, 0) + 1
