"""Notice new review feedback on a waiting pull request and start a round for it."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.domain.check_repair import (
    CheckFailure,
    ExhaustionEvidence,
    exhaustion_question,
)
from shikiru.domain.events import EventKind
from shikiru.domain.feedback import FeedbackSnapshot
from shikiru.domain.feedback_rounds import FeedbackStage
from shikiru.engine.feedback_snapshot import (
    build_snapshot,
    new_discussion_comments,
    new_reviews,
)
from shikiru.engine.integration import PullRequestStalled, describe_pr_stall
from shikiru.errors import InvariantError, ShikiruError
from shikiru.workspace.github import PullRequestState

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.config.settings import PullRequestFeedbackScope
    from shikiru.domain.feedback_rounds import FeedbackRound
    from shikiru.engine.fault_reporter import FaultReporter
    from shikiru.store.events import EventLog
    from shikiru.store.feedback import FeedbackStore
    from shikiru.workspace.github import GitHub, PullRequestStatus

FEEDBACK_FAULT_SCOPE = "pull request feedback poll"


class FeedbackIntake:
    """Start rounds for review feedback and settled failing pull request checks."""

    def __init__(
        self,
        *,
        github: GitHub,
        store: FeedbackStore,
        events: EventLog,
        faults: FaultReporter,
        project_root: Path,
        scope: PullRequestFeedbackScope,
    ) -> None:
        """Read feedback through `github`; record rounds in `store` and announce them in `events`.

        `scope` decides which reviews and comments start a round. A failed read is recorded
        through `faults` and tried again at the next poll.
        """
        self._github = github
        self._store = store
        self._events = events
        self._faults = faults
        self._project_root = project_root
        self._scope: PullRequestFeedbackScope = scope

    async def arrived(self, task_id: str, url: str) -> bool | None:
        """Return whether `task_id`'s pull request at `url` has feedback awaiting a round.

        A round that is already unfinished counts. Otherwise the reviews and the discussion
        comments under the configured scope, which no earlier round covered, are frozen into a
        new one. Return `None` when feedback could not be read.
        """
        if self._store.active(task_id) is not None:
            return True
        try:
            feedback = await self._github.pull_request_feedback(self._project_root, url)
        except ShikiruError as error:
            self._faults.report(error, scope=FEEDBACK_FAULT_SCOPE, task_id=task_id)
            return None
        self._faults.resolve(FEEDBACK_FAULT_SCOPE, task_id=task_id)
        if feedback.state is not PullRequestState.OPEN:
            return None
        reviews = new_reviews(feedback, self._store.seen_review_ids(task_id, url), self._scope)
        comments = new_discussion_comments(
            feedback, self._store.seen_comment_ids(task_id, url), self._scope
        )
        if not reviews and not comments:
            return False
        started = self._store.start(
            task_id=task_id,
            pull_request_url=url,
            review_ids=[review.id for review in reviews],
            comment_ids=[comment.id for comment in comments],
            snapshot=build_snapshot(feedback, reviews, comments),
            expected_remote_sha=feedback.head_sha,
        )
        self._events.append(
            EventKind.FEEDBACK_STARTED,
            task_id=task_id,
            message=f"{task_id} received review feedback on {url}",
            data={
                "url": url,
                "round": started.id,
                "reviews": list(started.review_ids),
                "comments": list(started.comment_ids),
            },
        )
        return True

    def checks_arrived(self, task_id: str, url: str, status: PullRequestStatus, limit: int) -> bool:
        """Start one round for a settled failing head, or wait for the published head."""
        if status.checks_pending:
            return False
        if status.checks_failing and (
            status.evidence_error or not status.head_sha or not status.failing_checks
        ):
            evidence = describe_pr_stall(status, expired=False)
            if evidence is None:
                message = f"{task_id} has failing checks without a stall description"
                raise InvariantError(message)
            raise PullRequestStalled(url, evidence)
        rounds = self._store.check_rounds(task_id, url)
        if rounds and not self._published_head_observed(task_id, url, status, rounds[-1]):
            return False
        if not status.checks_failing:
            return False
        evidence = describe_pr_stall(status, expired=False)
        if evidence is None:
            message = f"{task_id} has failing checks without a stall description"
            raise InvariantError(message)
        if status.head_sha is None:
            message = f"{task_id} has failing checks without a head"
            raise InvariantError(message)
        if any(round_.snapshot.head_sha == status.head_sha for round_ in rounds):
            return False
        failures = [
            CheckFailure(
                name=check.name,
                result=check.result,
                kind=check.kind,
                details_url=check.details_url,
            )
            for check in status.failing_checks
        ]
        if len(rounds) >= limit:
            exhausted = ExhaustionEvidence(head_sha=status.head_sha, failures=failures)
            question = exhaustion_question(len(rounds), evidence, exhausted)
            raise PullRequestStalled(url, question)
        snapshot = FeedbackSnapshot(
            pull_request_url=url,
            head_sha=status.head_sha,
            failures=failures,
        )
        started = self._store.start_check(task_id, snapshot)
        self._events.append(
            EventKind.FEEDBACK_STARTED,
            task_id=task_id,
            message=f"{task_id} started repairing checks on {url}",
            data={
                "url": url,
                "round": started.id,
                "head": status.head_sha,
                "failures": [failure.model_dump() for failure in snapshot.failures],
            },
        )
        return True

    def _published_head_observed(
        self, task_id: str, url: str, status: PullRequestStatus, last: FeedbackRound
    ) -> bool:
        if last.stage is not FeedbackStage.HANDLED or last.published_sha is None:
            return False
        expected = self._store.latest_published_sha(task_id, url)
        if expected is None:
            message = f"{task_id} lost its published check repair tip"
            raise InvariantError(message)
        if (
            status.head_sha in (last.snapshot.head_sha, last.published_sha)
            and status.head_sha != expected
        ):
            return False
        if status.head_sha != expected:
            raise PullRequestStalled(
                url, f"remote head changed to {status.head_sha}; expected {expected}"
            )
        return True
