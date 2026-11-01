"""The rule deciding when fresh quota evidence releases a provider quota block."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.agents.contracts import is_exhausted

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from shikiru.agents.contracts import QuotaWindow


def block_released(
    *,
    blocked_at: datetime,
    stored: Sequence[QuotaWindow],
    reading: Sequence[QuotaWindow],
    now: datetime,
) -> bool:
    """Return whether `reading` proves the block set at `blocked_at` no longer holds.

    Only samples observed after the block count. The fresh reading must have a usable window,
    show none exhausted and cover every window `stored` still holds exhausted before its reset.
    """
    fresh = [window for window in reading if window.observed_at > blocked_at]
    if not fresh or any(is_exhausted(window) for window in fresh):
        return False
    covered = {window.name for window in fresh}
    return all(
        window.name in covered
        for window in stored
        if is_exhausted(window) and window.resets_at is not None and window.resets_at > now
    )
