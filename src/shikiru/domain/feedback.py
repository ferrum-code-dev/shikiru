"""The frozen PR review feedback a Leader analyses, and the rules its reports must satisfy."""

from __future__ import annotations

from enum import StrEnum, auto
from typing import Annotated

from pydantic import Field, StringConstraints, model_validator

from shikiru.domain.reports import (
    DECISION_ACCEPT,
    FeedbackAction,
    FeedbackAnalysis,
    FeedbackVerdict,
    Report,
)
from shikiru.domain.tasks import DomainModel

_Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class FeedbackContractError(ValueError):
    """A feedback report does not account for its snapshot or cannot be acted on."""


class FeedbackSourceKind(StrEnum):
    """Where a source item of feedback was written."""

    REVIEW_BODY = auto()
    DISCUSSION = auto()


class FeedbackStep(StrEnum):
    """What the engine may do next with a checked analysis."""

    ESCALATE = auto()
    IMPLEMENT = auto()
    VERIFY = auto()


class FeedbackMessage(DomainModel):
    """One comment of a source item, in the order it was written.

    `new` marks a comment this round must answer; the others were handled by earlier rounds
    or are our own replies.
    """

    author: str | None
    body: str
    url: str
    new: bool = False


class FeedbackSource(DomainModel):
    """A review body or an original inline discussion, with the replies that followed it."""

    id: _Text
    kind: FeedbackSourceKind
    url: str
    location: str = ""
    outdated: bool = False
    resolved: bool = False
    messages: list[FeedbackMessage] = Field(min_length=1)


class FeedbackSnapshot(DomainModel):
    """The feedback of one round, fixed when the round starts."""

    pull_request_url: str
    head_sha: str
    sources: list[FeedbackSource] = Field(min_length=1)

    @model_validator(mode="after")
    def _source_ids_are_unique(self) -> FeedbackSnapshot:
        ids = self.source_ids
        if len(set(ids)) != len(ids):
            message = "sources: duplicate ids"
            raise ValueError(message)
        return self

    @property
    def source_ids(self) -> list[str]:
        """The identities of the sources, in snapshot order."""
        return [source.id for source in self.sources]

    def only(self, source_ids: set[str]) -> FeedbackSnapshot:
        """Return the snapshot restricted to `source_ids`."""
        return self.model_copy(
            update={"sources": [source for source in self.sources if source.id in source_ids]}
        )


class FeedbackInput(DomainModel):
    """What a feedback turn is given: snapshot, analysis once made, round limit, tip verified."""

    snapshot: FeedbackSnapshot
    analysis: FeedbackAnalysis | None = None
    final_round: bool = False
    tip: str | None = None


def check_analysis(snapshot: FeedbackSnapshot, analysis: FeedbackAnalysis) -> None:
    """Require one decision for each source of `snapshot` and no other."""
    expected = set(snapshot.source_ids)
    decided = {decision.source_id for decision in analysis.decisions}
    unknown = decided - expected
    if unknown:
        message = f"decisions name unknown source ids {sorted(unknown)}"
        raise FeedbackContractError(message)
    missing = expected - decided
    if missing:
        message = f"decisions leave source ids {sorted(missing)} without a decision"
        raise FeedbackContractError(message)


def next_feedback_step(analysis: FeedbackAnalysis) -> FeedbackStep:
    """Choose the step after `analysis`: an Operator question first, then work, then replies.

    A decision that needs the Operator is never read as approval to carry out the work of
    the others, and a round with no accepted work needs no Implementer turn.
    """
    if operator_questions(analysis):
        return FeedbackStep.ESCALATE
    if analysis.work:
        return FeedbackStep.IMPLEMENT
    return FeedbackStep.VERIFY


def operator_questions(analysis: FeedbackAnalysis) -> list[tuple[str, str]]:
    """Return each (source id, question) the Operator must answer."""
    return [
        (decision.source_id, decision.detail)
        for decision in analysis.decisions
        if decision.action is FeedbackAction.NEEDS_OPERATOR
    ]


def check_verdict(
    snapshot: FeedbackSnapshot, analysis: FeedbackAnalysis, verdict: FeedbackVerdict
) -> None:
    """Require an accepting verdict to verify exactly the replies the analysis drafted."""
    check_analysis(snapshot, analysis)
    if verdict.decision != DECISION_ACCEPT:
        return
    if operator_questions(analysis):
        message = "decision: 'accept' is not available while a decision needs the Operator"
        raise FeedbackContractError(message)
    expected = {
        decision.source_id
        for decision in analysis.decisions
        if decision.action is not FeedbackAction.NEEDS_OPERATOR
    }
    verified = {reply.source_id for reply in verdict.replies}
    if verified != expected:
        message = (
            f"replies must cover exactly source ids {sorted(expected)}, got {sorted(verified)}"
        )
        raise FeedbackContractError(message)


def check_feedback_report(feedback: FeedbackInput, report: Report) -> None:
    """Check `report` against the frozen `feedback` its turn was given."""
    if isinstance(report, FeedbackAnalysis):
        check_analysis(feedback.snapshot, report)
    elif isinstance(report, FeedbackVerdict):
        if feedback.analysis is None:
            message = "a feedback verdict needs the analysis it verifies"
            raise FeedbackContractError(message)
        check_verdict(feedback.snapshot, feedback.analysis, report)


def publishable_replies(
    snapshot: FeedbackSnapshot, analysis: FeedbackAnalysis, verdict: FeedbackVerdict
) -> dict[str, str]:
    """Return the verified reply of each source, or fail when the round is not accepted."""
    if verdict.decision != DECISION_ACCEPT:
        message = f"a '{verdict.decision}' verdict leaves no reply publishable"
        raise FeedbackContractError(message)
    check_verdict(snapshot, analysis, verdict)
    return {reply.source_id: reply.reply for reply in verdict.replies}
