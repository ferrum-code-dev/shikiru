"""Helper output model for the explicit inability to answer."""

from __future__ import annotations

from shikiru.domain.tasks import DomainModel


class Unrecoverable(DomainModel):
    """The Helper's explicit inability to answer, for either call."""

    unrecoverable: str
