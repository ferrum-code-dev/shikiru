"""Core task model and its enums."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum, auto

from pydantic import BaseModel, ConfigDict, Field

from shikiru.agents.contracts import Rung
from shikiru.config.settings import ComplexityName

TASK_TITLE_MAX_LENGTH = 60
TASK_TITLE_ELLIPSIS = "…"


class TaskOrigin(StrEnum):
    """Where a task came from."""

    OPERATOR = auto()
    LEADER = auto()


class Status(StrEnum):
    """A task's lifecycle status."""

    QUEUED = auto()
    RUNNING = auto()
    INTEGRATING = auto()
    ESCALATED = auto()
    DONE = auto()
    CANCELLED = auto()


ACTIVE_STATUSES = (Status.RUNNING, Status.INTEGRATING, Status.ESCALATED)
"""The statuses that remain active in a task window."""


class StatusReason(StrEnum):
    """Why a terminal `status` was reached outside the ordinary happy path."""

    SUPERSEDED = auto()


class Step(StrEnum):
    """The current step of a running task."""

    TRIAGE = auto()
    PLAN = auto()
    IMPLEMENT = auto()
    CONSULT = auto()
    VERIFY = auto()
    INTEGRATE = auto()


class Mode(StrEnum):
    """What triage decided the request needs."""

    IMPLEMENT = auto()
    PLAN = auto()


class Hold(StrEnum):
    """A task-scoped hold."""

    PAUSED = auto()
    INTERVENTION = auto()


class Role(StrEnum):
    """The three agent roles."""

    LEADER = auto()
    IMPLEMENTER = auto()
    HELPER = auto()


class EscalationReason(StrEnum):
    """Why a task was escalated."""

    DECISION_REQUIRED = auto()
    ITERATIONS_EXHAUSTED = auto()
    INTEGRATION_CONFLICT = auto()
    INTEGRATION_BLOCKED = auto()
    FEEDBACK_NEEDS_OPERATOR = auto()
    UNCOMMITTED_WORK = auto()
    OFF_TASK_BRANCH = auto()
    PR_CLOSED = auto()
    AGENT_AUTH = auto()
    AGENT_ERROR = auto()
    AGENT_STUCK = auto()
    AGENT_PROTOCOL = auto()
    LADDER_EXHAUSTED = auto()
    QUOTA_EXHAUSTED = auto()
    DEPENDENCY_CANCELLED = auto()
    ENGINE_ERROR = auto()


class DomainModel(BaseModel):
    """Base for domain models: unknown keys are rejected, fields can't be reassigned.

    `frozen=True` only blocks assigning a new value to a field; a mutable field's own `list` or
    `dict` can still be mutated in place, which is why callers that need to change one (e.g.
    `Transition.stay`) copy it first rather than relying on the model to be deeply immutable.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


class Counters(DomainModel):
    """Persisted per-task counters and their reset rules."""

    rejections_on_rung: int = 0
    consults: int = 0
    conflict_rounds: int = 0


class RepoState(DomainModel):
    """The task's checkout state (mirrors `tasks.repo`)."""

    base: str
    base_ref: str
    base_sha: str
    branch: str


DEFAULT_COMPLEXITY: ComplexityName = "normal"


class Task(DomainModel):
    """The task fields the domain rules need (mirrors the `tasks` table)."""

    id: str
    seq: int
    parent_id: str | None = None
    origin: TaskOrigin
    title: str
    request: str
    mode: Mode | None = None
    complexity: ComplexityName | None = None
    priority: int = 0
    depends_on: list[str] = Field(default_factory=list)
    status: Status
    step: Step | None = None
    status_reason: StatusReason | None = None
    holds: frozenset[Hold] = Field(default_factory=frozenset)
    iteration: int = 0
    counters: Counters = Field(default_factory=Counters)
    rung_index: dict[Role, int] = Field(default_factory=dict)
    overrides: dict[Role, Rung] = Field(default_factory=dict)
    repo: RepoState | None = None
    slot: int | None = None
    window_id: str | None = None
    panes: dict[Role, str] = Field(default_factory=dict)
    wait_reason: str | None = None
    requeued_at: datetime | None = None
    summary: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def held(self) -> bool:
        """Whether a task-scoped hold forbids starting the task's next external effect."""
        return bool(self.holds)


def title_from_request(request: str) -> str:
    """Derive a title from the request's first line, cut at a word boundary within the limit."""
    lines = request.splitlines()
    first_line = lines[0] if lines else request
    if len(first_line) <= TASK_TITLE_MAX_LENGTH:
        return first_line
    kept = first_line[: TASK_TITLE_MAX_LENGTH - len(TASK_TITLE_ELLIPSIS)]
    return kept.rsplit(" ", 1)[0] + TASK_TITLE_ELLIPSIS
