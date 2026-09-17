"""The agent plugin contract (docs/design/04-agents.md §1). Only `Rung` and `FailureKind` exist."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, auto
from typing import ClassVar

from pydantic import ConfigDict

from shikiru.errors import ConfigError

_RUNG_SEPARATOR = ":"
_VALID_RUNG_PART_COUNTS = (2, 3)


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


def parse_rung(text: str) -> Rung:
    """Parse `agent:model` or `agent:model:effort`, raising `ConfigError` on malformed input."""
    parts = text.split(_RUNG_SEPARATOR)
    if len(parts) not in _VALID_RUNG_PART_COUNTS or not all(parts):
        message = f"{text!r} is not a valid rung; expected agent:model or agent:model:effort"
        raise ConfigError(message)
    effort = parts[2] if len(parts) == _VALID_RUNG_PART_COUNTS[1] else None
    return Rung(agent=parts[0], model=parts[1], effort=effort)
