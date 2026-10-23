"""Pure parsing for the initializer's comma-separated ladder field."""

from __future__ import annotations

from dataclasses import dataclass

from shikiru.agents.contracts import Rung

ENTRY_SEPARATOR = ","
PART_SEPARATOR = ":"
REQUIRED_PART_COUNT = 2
OPTIONAL_EFFORT_PART_COUNT = 3


@dataclass(frozen=True, slots=True)
class LadderTextError(ValueError):
    """One malformed entry in an edited ladder field."""

    entry_index: int
    detail: str

    def __str__(self) -> str:
        return self.detail


def parse_ladder_text(value: str) -> tuple[Rung, ...]:
    """Parse ordered `agent:model[:effort]` entries without changing the source text."""
    if not value.strip():
        return ()

    rungs: list[Rung] = []
    for index, raw_entry in enumerate(value.split(ENTRY_SEPARATOR)):
        entry = raw_entry.strip()
        parts = tuple(part.strip() for part in entry.split(PART_SEPARATOR))
        if len(parts) not in {REQUIRED_PART_COUNT, OPTIONAL_EFFORT_PART_COUNT}:
            detail = "use agent:model or agent:model:effort"
            raise LadderTextError(index, detail)
        if any(not part for part in parts):
            detail = "each agent, model and effort value must be non-empty"
            raise LadderTextError(index, detail)
        agent, model = parts[:REQUIRED_PART_COUNT]
        effort = parts[-1] if len(parts) == OPTIONAL_EFFORT_PART_COUNT else None
        rungs.append(Rung(agent=agent, model=model, effort=effort))
    return tuple(rungs)
