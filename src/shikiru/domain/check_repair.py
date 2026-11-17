"""Durable evidence and progress of a pull request check repair round."""

from __future__ import annotations

from enum import StrEnum, auto

from pydantic import Field

from shikiru.domain.tasks import DomainModel


class CheckRepairStage(StrEnum):
    """The durable stages of one repair of a settled failing head."""

    ANALYSIS = auto()
    IMPLEMENTATION = auto()
    VERIFICATION = auto()
    PUBLICATION = auto()
    PUBLISHED = auto()
    ABANDONED = auto()


class CheckFailure(DomainModel):
    """The failure evidence supplied by GitHub for one check."""

    name: str
    result: str
    kind: str
    details_url: str | None


class CheckRepairRound(DomainModel):
    """A single attempt to repair the failures observed on a particular head."""

    id: int
    task_id: str
    pull_request_url: str
    head_sha: str
    failures: list[CheckFailure] = Field(min_length=1)
    stage: CheckRepairStage
    verified_sha: str | None = None
    published_sha: str | None = None
