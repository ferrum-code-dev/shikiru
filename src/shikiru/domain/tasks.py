"""Core task model and its enums (docs/design/03-workflow.md §1; docs/design/05-data.md §2)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum, auto

from pydantic import BaseModel, ConfigDict, Field

from shikiru.agents.contracts import Rung
from shikiru.config.settings import ComplexityName


class TaskOrigin(StrEnum):
    """Where a task came from (03 §1.1)."""

    OPERATOR = auto()
    LEADER = auto()


class Status(StrEnum):
    """A task's lifecycle status (03 §1.2)."""

    QUEUED = auto()
    RUNNING = auto()
    INTEGRATING = auto()
    ESCALATED = auto()
    DONE = auto()
    CANCELLED = auto()


ACTIVE_STATUSES = (Status.RUNNING, Status.INTEGRATING, Status.ESCALATED)
"""The statuses that remain active in a task window (03 §1.2)."""


class StatusReason(StrEnum):
    """Why a terminal `status` was reached outside the ordinary happy path (03 §2, §6)."""

    SUPERSEDED = auto()


class Step(StrEnum):
    """The current step of a running task (03 §2)."""

    TRIAGE = auto()
    PLAN = auto()
    IMPLEMENT = auto()
    CONSULT = auto()
    VERIFY = auto()
    INTEGRATE = auto()


class Mode(StrEnum):
    """What triage decided the request needs (03 §1.1, §2)."""

    IMPLEMENT = auto()
    PLAN = auto()


class Hold(StrEnum):
    """A task-scoped hold (03 §1.2, §9)."""

    PAUSED = auto()
    INTERVENTION = auto()


class Role(StrEnum):
    """The three agent roles (04 §1; 05 §1)."""

    LEADER = auto()
    IMPLEMENTER = auto()
    HELPER = auto()


class EscalationReason(StrEnum):
    """Why a task was escalated (03 §8)."""

    DECISION_REQUIRED = auto()
    ITERATIONS_EXHAUSTED = auto()
    INTEGRATION_CONFLICT = auto()
    INTEGRATION_BLOCKED = auto()
    UNCOMMITTED_WORK = auto()
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
    """Persisted per-task counters and their reset rules (03 §1.1)."""

    rejections_on_rung: int = 0
    consults: int = 0
    conflict_rounds: int = 0


class RepoState(DomainModel):
    """The task's checkout state (03 §1.1, §5; mirrors `tasks.repo`, 05 §2)."""

    base: str
    base_sha: str
    branch: str


class Task(DomainModel):
    """The task fields the domain rules need (03 §1.1; mirrors the `tasks` table, 05 §2)."""

    id: str
    seq: int
    parent_id: str | None = None
    origin: TaskOrigin
    title: str
    request: str
    mode: Mode | None = None
    complexity: ComplexityName = "normal"
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
