"""In-memory contextual form drafts kept while a palette caller is visible (06 §5.5)."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from shikiru.dashboard.contract import PaletteDraft, PaletteRequest

MAX_CONTEXTUAL_PALETTE_DRAFTS = 16


class PaletteDrafts:
    """Remember the latest draft per task, action and role, dropping the oldest beyond a bound."""

    def __init__(self) -> None:
        self._drafts: dict[tuple[str, str, str | None], PaletteDraft] = {}

    def remember(self, draft: PaletteDraft) -> None:
        """Keep `draft` as the latest for its task, action and role."""
        key = (draft.task_id, draft.action, draft.role)
        self._drafts.pop(key, None)
        self._drafts[key] = draft
        if len(self._drafts) > MAX_CONTEXTUAL_PALETTE_DRAFTS:
            self._drafts.pop(next(iter(self._drafts)))

    def attach(self, request: PaletteRequest) -> PaletteRequest:
        """Return `request` carrying the drafts remembered for its task."""
        drafts = tuple(
            draft for (task_id, _, _), draft in self._drafts.items() if task_id == request.task_id
        )
        return replace(request, drafts=drafts) if drafts else request
