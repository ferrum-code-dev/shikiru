"""Agent run model: one row per task role's session lineage (docs/design/05-data.md §2)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum, auto

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
    TAKEN_OVER = auto()
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
    last_message: str | None = None
    status: RunStatus
    usage: Usage = NO_USAGE
    started_at: datetime
    ended_at: datetime | None = None


MISSING_TITLE_VALUE = "\N{EN DASH}"


def context_title(usage: Usage) -> str:
    """Format `usage`'s context share for a title (`42%`), or a dash if unreported (06 §5.3)."""
    if usage.context_used_pct is None:
        return MISSING_TITLE_VALUE
    return f"{round(usage.context_used_pct)}%"


def cost_title(usage: Usage) -> str:
    """Format `usage`'s cost for a title (`$1.84`), or a dash if unreported (06 §5.3)."""
    if usage.cost_usd is None:
        return MISSING_TITLE_VALUE
    return f"${usage.cost_usd:.2f}"
