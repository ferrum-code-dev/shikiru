"""Agent run model: one row per task role's session lineage (docs/design/05-data.md §2)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum, auto
from typing import Any

from pydantic import Field

from shikiru.domain.tasks import DomainModel, Role


class RunStatus(StrEnum):
    """A run's lifecycle (05 §2)."""

    LIVE = auto()
    EXITED = auto()
    CLOSED = auto()


class Usage(DomainModel):
    """A run's token and cost usage, as last reported by `AgentSession.usage()` (04 §1)."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float | None = None
    cost_basis: str | None = None
    context_used_pct: float | None = None


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
    usage: Usage = Field(default_factory=Usage)
    started_at: datetime
    ended_at: datetime | None = None
