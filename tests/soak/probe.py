"""The operator reading state through the real CLI while everything else is breaking."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from tests.soak.model import Violation

if TYPE_CHECKING:
    import random

    from tests.soak.arena import Arena


class StatusProbe:
    """Asks `shikiru status` for the project and for one task; any failure is a finding."""

    def __init__(self, arena: Arena, rng: random.Random) -> None:
        self._arena = arena
        self._rng = rng

    async def check(self) -> list[Violation]:
        reader = self._arena.reader
        assert reader is not None
        tasks = reader.list_tasks()
        targets: list[tuple[str, ...]] = [()]
        if tasks:
            targets.append((self._rng.choice(tasks).id,))
        found: list[Violation] = []
        for target in targets:
            code, stdout, stderr = await self._arena.cli("--json", "status", *target)
            problem = _problem(code, stdout, stderr)
            if problem is not None:
                found.append(
                    Violation(0, target[0] if target else None, f"status {target}: {problem}")
                )
        return found


def _problem(code: int, stdout: str, stderr: str) -> str | None:
    if code != 0:
        return f"exit {code}: {stderr.strip()[-300:]}"
    try:
        value = json.loads(stdout)
    except json.JSONDecodeError as error:
        return f"output is not JSON: {error}"
    return None if isinstance(value, dict) else "output is not a JSON object"
