"""The `--shard INDEX/COUNT` rule that splits the test suite across CI runners."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Collection

SHARD_OPTION = "--shard"
_SEPARATOR = "/"
_FIRST_SHARD = 1


@dataclass(frozen=True)
class Shard:
    """One of `count` disjoint parts of the suite, numbered from one."""

    index: int
    count: int

    def select(self, test_ids: Collection[str]) -> frozenset[str]:
        """Return every `count`-th id in sorted order, so each file spreads across the shards."""
        return frozenset(sorted(test_ids)[self.index - _FIRST_SHARD :: self.count])


def parse_shard(text: str) -> Shard:
    """Parse `INDEX/COUNT`, rejecting an index outside `1..COUNT`."""
    index, _, count = text.partition(_SEPARATOR)
    if index.isdecimal() and count.isdecimal() and _FIRST_SHARD <= int(index) <= int(count):
        return Shard(int(index), int(count))
    message = f"{SHARD_OPTION} must be INDEX{_SEPARATOR}COUNT, got {text!r}"
    raise ValueError(message)
