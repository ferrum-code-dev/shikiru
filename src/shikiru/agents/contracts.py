"""The agent plugin contract (docs/design/04-agents.md §1). Only `Rung` exists so far."""

from __future__ import annotations

from dataclasses import dataclass

from shikiru.errors import ConfigError

_RUNG_FIELD_COUNT_WITH_EFFORT = 3
_RUNG_FIELD_COUNT_WITHOUT_EFFORT = 2
_RUNG_SEPARATOR = ":"


@dataclass(frozen=True)
class Rung:
    """One `{agent, model, effort}` entry of a role's ladder (04 §1)."""

    agent: str
    model: str
    effort: str | None


def parse_rung(text: str) -> Rung:
    """Parse `agent:model` or `agent:model:effort`, raising `ConfigError` on malformed input."""
    parts = text.split(_RUNG_SEPARATOR)
    if len(parts) == _RUNG_FIELD_COUNT_WITHOUT_EFFORT:
        agent, model = parts
        effort = None
    elif len(parts) == _RUNG_FIELD_COUNT_WITH_EFFORT:
        agent, model, effort = parts
    else:
        message = f"{text!r} is not a valid rung; expected agent:model or agent:model:effort"
        raise ConfigError(message)
    if not agent or not model or (effort is not None and not effort):
        message = f"{text!r} is not a valid rung; expected agent:model or agent:model:effort"
        raise ConfigError(message)
    return Rung(agent=agent, model=model, effort=effort)
