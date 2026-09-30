"""The outcome of one executed agent turn, as the flow and its collaborators pass it around."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.agents.contracts import Rung, Usage
    from shikiru.domain.recovery import TurnResult
    from shikiru.domain.tasks import Role


@dataclass
class TurnExecution:
    """A turn's result with the role, rung, directory and usage it ran with."""

    result: TurnResult
    task_id: str
    role: Role
    turn_dir: Path
    rung: Rung
    usage_before: Usage
    usage_after: Usage
    quota_wait_exhausted: bool = False
