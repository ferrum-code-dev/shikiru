"""The agent plugin contract (docs/design/04-agents.md §1).

Only the types earlier tasks need ahead of the rest of the contract exist so far:
`Rung`, `FailureKind`, `Usage`, `QuotaWindow` and `PaneHandle`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, auto
from typing import TYPE_CHECKING, ClassVar, Literal, Protocol

from pydantic import ConfigDict

from shikiru.errors import ConfigError

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime
    from pathlib import Path

_RUNG_SEPARATOR = ":"
_VALID_RUNG_PART_COUNTS = (2, 3)

CostBasis = Literal["billed", "api_equivalent", "unknown"]


class FailureKind(StrEnum):
    """Why a turn failed, as mapped by the agent plugin from its own error format (04 §1)."""

    QUOTA = auto()
    UNAVAILABLE = auto()
    CONTEXT = auto()
    AUTH = auto()
    OTHER = auto()


@dataclass(frozen=True)
class Rung:
    """One `{agent, model, effort}` entry of a role's ladder (04 §1)."""

    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    agent: str
    model: str
    effort: str | None = None


@dataclass(frozen=True)
class Usage:
    """A session's cumulative token and cost usage, as `AgentSession.usage()` reports it (04 §1)."""

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: float | None
    cost_basis: CostBasis
    context_used_pct: float | None


@dataclass(frozen=True)
class QuotaWindow:
    """One quota window an agent plugin's `quota()` reports (04 §1)."""

    name: str
    used_pct: float
    resets_at: datetime | None
    observed_at: datetime


class PaneHandle(Protocol):
    """The single tmux pane an agent session lives in (04 §1)."""

    async def respawn(self, argv: Sequence[str], env: Mapping[str, str | None], cwd: Path) -> None:
        """Restart the pane's process with `argv`, `env` and `cwd`."""
        ...

    async def capture_visible(self) -> str:
        """Return the pane's currently visible text."""
        ...

    async def is_alive(self) -> bool:
        """Return whether the pane's process is still running."""
        ...


def parse_rung(text: str) -> Rung:
    """Parse `agent:model` or `agent:model:effort`, raising `ConfigError` on malformed input."""
    parts = text.split(_RUNG_SEPARATOR)
    if len(parts) not in _VALID_RUNG_PART_COUNTS or not all(parts):
        message = f"{text!r} is not a valid rung; expected agent:model or agent:model:effort"
        raise ConfigError(message)
    effort = parts[2] if len(parts) == _VALID_RUNG_PART_COUNTS[1] else None
    return Rung(agent=parts[0], model=parts[1], effort=effort)
