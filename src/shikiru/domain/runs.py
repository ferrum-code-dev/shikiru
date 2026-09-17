"""Agent run model: one row per task role's session lineage (docs/design/05-data.md §2)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum, auto
from typing import Any

from pydantic import Field

from shikiru.agents.contracts import Usage
from shikiru.domain.tasks import DomainModel, Role

NO_USAGE = Usage(
    input_tokens=0,
    output_tokens=0,
    cache_read_tokens=0,
    cache_write_tokens=0,
    cost_usd=None,
    cost_basis="unknown",
    context_used_pct=None,
)


class RunStatus(StrEnum):
    """A run's lifecycle (05 §2)."""

    LIVE = auto()
    EXITED = auto()
    CLOSED = auto()


class Run(DomainModel):
    """One agent session for a task role (03 §4.1-4.2; mirrors the `runs` table, 05 §2)."""

    id: int
    task_id: str
    role: Role
    agent: str
    model: str
    effort: str | None = None
    session_id: str | None = None
    resume_command: str | None = None
    agent_state: dict[str, Any] = Field(default_factory=dict)
    last_message: str | None = None
    status: RunStatus
    usage: Usage = NO_USAGE
    started_at: datetime
    ended_at: datetime | None = None
