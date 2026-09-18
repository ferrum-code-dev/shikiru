"""Helper output models: a stalled screen's classification and the unrecoverable result (04 §6)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum, auto

from pydantic import Field

from shikiru.domain.tasks import DomainModel


class ScreenState(StrEnum):
    """What a stalled screen shows, as `Helper.classify_screen` names it (04 §6, 03 §7)."""

    WORKING = auto()
    DIALOG = auto()
    QUESTION = auto()
    QUOTA = auto()
    ERROR = auto()


class ScreenClassification(DomainModel):
    """The Helper's classification of a stalled agent screen (04 §6)."""

    state: ScreenState
    keys: list[str] = Field(default_factory=list)
    text: str = ""
    resets_at: datetime | None = None


class Unrecoverable(DomainModel):
    """The Helper's explicit inability to answer, for either call (04 §6)."""

    unrecoverable: str
