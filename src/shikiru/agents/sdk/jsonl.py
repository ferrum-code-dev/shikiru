"""`JsonlTail`: an incremental, offset-tracked reader for growing JSONL files (04 §2)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class JsonlEntry:
    """One decoded JSONL line, with the byte offset just past its trailing newline."""

    offset: int
    value: Any


class JsonlTail:
    """Reads whole JSON lines from a file that another process may still be appending to."""

    def __init__(self, path: Path) -> None:
        """Read lines from `path`, which need not exist yet."""
        self._path = path

    def read_from(self, offset: int) -> Iterator[JsonlEntry]:
        """Yield every complete line at or after `offset`, never a line still being written."""
        if not self._path.is_file():
            return
        with self._path.open("rb") as handle:
            handle.seek(offset)
            position = offset
            for raw_line in handle:
                if not raw_line.endswith(b"\n"):
                    return
                position += len(raw_line)
                yield JsonlEntry(offset=position, value=json.loads(raw_line))
