"""Pure classification of unified-diff lines for colouring."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

FILE_HEADER_PREFIX = "diff --git "
HUNK_PREFIX = "@@"
ADDED_PREFIX = "+"
REMOVED_PREFIX = "-"


class DiffLineKind(StrEnum):
    """The colour role of one diff line."""

    ADDED = "added"
    REMOVED = "removed"
    HUNK = "hunk"
    CONTEXT = "context"


def classify_diff_lines(lines: Sequence[str]) -> tuple[DiffLineKind, ...]:
    """Classify each line of a Git patch as added, removed, hunk or context.

    Only lines inside a hunk are added or removed, so the `---` and `+++` file headers stay
    context while a removed or added line starting with those characters does not.
    """
    kinds: list[DiffLineKind] = []
    in_hunk = False
    for line in lines:
        if line.startswith(HUNK_PREFIX):
            in_hunk = True
            kinds.append(DiffLineKind.HUNK)
            continue
        if line.startswith(FILE_HEADER_PREFIX):
            in_hunk = False
        if in_hunk and line.startswith(ADDED_PREFIX):
            kinds.append(DiffLineKind.ADDED)
        elif in_hunk and line.startswith(REMOVED_PREFIX):
            kinds.append(DiffLineKind.REMOVED)
        else:
            kinds.append(DiffLineKind.CONTEXT)
    return tuple(kinds)
