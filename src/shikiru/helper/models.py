"""Helper output model for the explicit inability to answer (04 §6)."""

from __future__ import annotations

from shikiru.domain.tasks import DomainModel


class Unrecoverable(DomainModel):
    """The Helper's explicit inability to answer, for either call (04 §6)."""

    unrecoverable: str
