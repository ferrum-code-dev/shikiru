"""Durable evidence and progress of a pull request check repair round."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum, auto

from pydantic import Field, ValidationError

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


UNFINISHED_CHECK_STAGES = frozenset(
    (
        CheckRepairStage.ANALYSIS,
        CheckRepairStage.IMPLEMENTATION,
        CheckRepairStage.VERIFICATION,
        CheckRepairStage.PUBLICATION,
    )
)

CHECK_STAGE_LABELS = {
    CheckRepairStage.ANALYSIS: "analysing",
    CheckRepairStage.IMPLEMENTATION: "implementing fix",
    CheckRepairStage.VERIFICATION: "verifying fix",
    CheckRepairStage.PUBLICATION: "publishing fix",
    CheckRepairStage.PUBLISHED: "published",
    CheckRepairStage.ABANDONED: "abandoned",
}


def active_check_round(rounds: list[CheckRepairRound]) -> CheckRepairRound | None:
    """Return the round still being repaired, if one is unfinished."""
    return next((item for item in rounds if item.stage in UNFINISHED_CHECK_STAGES), None)


def check_repair_attempt(rounds: list[CheckRepairRound], current: CheckRepairRound) -> int:
    """Return the 1-based attempt number of `current` among `rounds`."""
    return rounds.index(current) + 1


EXHAUSTION_MARKER = "check repair attempts exhausted after"
_EVIDENCE_LINE = "\ncheck repair evidence: "


class ExhaustionEvidence(DomainModel):
    """The latest failing head and checks recorded by an exhaustion escalation."""

    head_sha: str
    failures: list[CheckFailure] = Field(min_length=1)


def exhaustion_question(attempts: int, summary: str, evidence: ExhaustionEvidence) -> str:
    """Return the escalation question for an exhausted repair, on exactly two lines.

    The readable account is flattened onto one line, so a check name with a line break cannot
    start a heading of the escalation packet. The exact `evidence` follows on the second line as
    ASCII JSON, which escapes every line break and separator that a name may contain.
    """
    readable = " ".join(summary.splitlines())
    payload = json.dumps(evidence.model_dump(mode="json"))
    return f"{EXHAUSTION_MARKER} {attempts}: {readable}{_EVIDENCE_LINE}{payload}"


def exhaustion_reason(question: str) -> str:
    """Return the readable account of an exhaustion question, without its evidence line."""
    reason, separator, _ = question.rpartition(_EVIDENCE_LINE)
    return reason if separator else question


@dataclass(frozen=True)
class CheckRepairProgress:
    """How an unfinished repair round currently stands for its task."""

    current: CheckRepairRound | None
    active: bool
    interrupted: bool


def check_repair_progress(
    rounds: list[CheckRepairRound], *, escalated: bool, held: bool
) -> CheckRepairProgress:
    """Return the unfinished round and whether it is proceeding or interrupted.

    A round is active only while its task progresses: an escalated or held task
    interrupts an unfinished round instead of actively fixing it.
    """
    unfinished = active_check_round(rounds)
    if unfinished is None:
        return CheckRepairProgress(None, False, False)
    interrupted = escalated or held
    return CheckRepairProgress(unfinished, not interrupted, interrupted)


def parse_exhaustion_evidence(question: str) -> ExhaustionEvidence | None:
    """Return the exact triggering head and checks recorded in an exhaustion question, if any."""
    _, separator, payload = question.rpartition(_EVIDENCE_LINE)
    if not separator:
        return None
    try:
        return ExhaustionEvidence.model_validate_json(payload.strip())
    except ValidationError:
        return None
