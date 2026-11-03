"""The durable progress of one PR feedback round and the pure rules that move it."""

from __future__ import annotations

from enum import StrEnum, auto
from typing import TYPE_CHECKING

from pydantic import Field

from shikiru.domain.feedback import FeedbackSnapshot, FeedbackSourceKind
from shikiru.domain.reports import FeedbackAction, FeedbackAnalysis
from shikiru.domain.tasks import DomainModel

if TYPE_CHECKING:
    from collections.abc import Sequence

    from shikiru.domain.reports import TestRun

MARKER_PREFIX = "shikiru-feedback"
MARKER_SEPARATOR = ":"
REPLY_SEPARATOR = "\n\n"
REVIEW_LINK_PREFIX = "In reply to"
FIXED_PREFIX = "Fixed in"
CHECKS_PREFIX = "Verified by"
CHECKS_SEPARATOR = "; "
SHORT_SHA_LENGTH = 12


class FeedbackStage(StrEnum):
    """Where a feedback round stands; the first five stages are the unfinished ones."""

    ANALYSIS = auto()
    IMPLEMENTATION = auto()
    VERIFICATION = auto()
    PUBLICATION = auto()
    REPLIES = auto()
    HANDLED = auto()
    ABANDONED = auto()


UNFINISHED_STAGES = frozenset(
    (
        FeedbackStage.ANALYSIS,
        FeedbackStage.IMPLEMENTATION,
        FeedbackStage.VERIFICATION,
        FeedbackStage.PUBLICATION,
        FeedbackStage.REPLIES,
    )
)


class ReplyState(StrEnum):
    """How far one reply got: stored, possibly sent (its outcome unknown), or confirmed sent."""

    PLANNED = auto()
    SENDING = auto()
    SENT = auto()


class PublicationAction(StrEnum):
    """What to do with the verified tip given where the remote branch is."""

    PUSH = auto()
    ALREADY_PUBLISHED = auto()
    REMOTE_MOVED = auto()


class Alignment(StrEnum):
    """How the task branch relates to the remote branch before a round edits it."""

    ALIGNED = auto()
    FAST_FORWARD = auto()
    DIVERGED = auto()


class FeedbackRound(DomainModel):
    """One batch of review feedback and everything done about it so far."""

    id: int
    task_id: str
    pull_request_url: str
    review_ids: list[str] = Field(min_length=1)
    snapshot: FeedbackSnapshot
    stage: FeedbackStage
    analysis: FeedbackAnalysis | None = None
    attempts: int = 0
    blocked: bool = False
    expected_remote_sha: str
    verified_sha: str | None = None
    published_sha: str | None = None
    checks: str = ""
    verified_replies: dict[str, str] = Field(default_factory=dict)


class FeedbackReply(DomainModel):
    """The immutable reply to one source item, with the marker that finds it again."""

    round_id: int
    source_id: str
    kind: FeedbackSourceKind
    marker: str
    body: str
    state: ReplyState = ReplyState.PLANNED
    posted_url: str | None = None


def reply_marker(task_id: str, round_id: int, source_id: str) -> str:
    """Return the stable marker that identifies the reply to `source_id` in `round_id`."""
    parts = (MARKER_PREFIX, task_id, str(round_id), source_id)
    return f"<!-- {MARKER_SEPARATOR.join(parts)} -->"


def reply_body(
    *,
    reply: str,
    marker: str,
    review_url: str | None,
    fixed_sha: str | None,
    checks: str,
) -> str:
    """Compose a reply: the link of a review-body source, the verified text, the fix, the marker."""
    parts: list[str] = []
    if review_url is not None:
        parts.append(f"{REVIEW_LINK_PREFIX} {review_url}")
    parts.append(reply.strip())
    if fixed_sha is not None:
        fixed = f"{FIXED_PREFIX} {fixed_sha[:SHORT_SHA_LENGTH]}."
        parts.append(f"{fixed} {CHECKS_PREFIX} {checks}." if checks else fixed)
    parts.append(marker)
    return REPLY_SEPARATOR.join(parts)


def describe_checks(tests: Sequence[TestRun]) -> str:
    """Name the checks an Implementer ran and how each ended."""
    return CHECKS_SEPARATOR.join(f"{test.name} {test.result}" for test in tests)


def fixed_source_ids(analysis: FeedbackAnalysis) -> set[str]:
    """Return the sources whose decision changed code."""
    return {
        decision.source_id
        for decision in analysis.decisions
        if decision.action is FeedbackAction.ADDRESS
    }


def plan_publication(*, expected: str, remote_head: str, local_tip: str) -> PublicationAction:
    """Decide how to publish `local_tip` when the remote branch is at `remote_head`.

    The remote only ever matches what the round captured (`expected`) or the tip itself; any
    other head is somebody else's work that a push must not overwrite.
    """
    if remote_head == local_tip:
        return PublicationAction.ALREADY_PUBLISHED
    if remote_head != expected:
        return PublicationAction.REMOTE_MOVED
    return PublicationAction.PUSH


def align_with_remote(*, remote_in_local: bool, local_in_remote: bool) -> Alignment:
    """Relate the task branch to the remote branch from their ancestry."""
    if remote_in_local:
        return Alignment.ALIGNED
    if local_in_remote:
        return Alignment.FAST_FORWARD
    return Alignment.DIVERGED
