"""`HookRecorder`: appends one hook payload per line to a role's `hooks.jsonl` (04 §2)."""

from __future__ import annotations

import fcntl
import json
from typing import TYPE_CHECKING, Any

from shikiru.agents.sdk.jsonl import JsonlTail

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from pathlib import Path

    from shikiru.agents.sdk.jsonl import JsonlEntry

HOOKS_FILENAME = "hooks.jsonl"


class HookRecorder:
    """Appends compact JSON lines to one role's `hooks.jsonl`, serialised with `flock`."""

    def __init__(self, role_dir: Path) -> None:
        """Record hooks for the role whose files live under `role_dir`."""
        self._path = role_dir / HOOKS_FILENAME

    def record(self, payload: Mapping[str, Any]) -> None:
        """Append `payload` as one compact JSON line, under an exclusive lock on the file."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        with self._path.open("a", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                handle.write(line + "\n")
                handle.flush()
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def tail(self, offset: int = 0) -> Iterator[JsonlEntry]:
        """Yield every hook entry recorded at or after `offset`."""
        yield from JsonlTail(self._path).read_from(offset)
