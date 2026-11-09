"""Notice new review feedback on a waiting pull request and start a round for it."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.domain.events import EventKind
from shikiru.engine.feedback_snapshot import (
    build_snapshot,
    new_discussion_comments,
    new_reviews,
)
from shikiru.errors import ShikiruError
from shikiru.workspace.github import PullRequestState

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.config.settings import PullRequestFeedbackScope
    from shikiru.engine.fault_reporter import FaultReporter
    from shikiru.store.events import EventLog
    from shikiru.store.feedback import FeedbackStore
    from shikiru.workspace.github import GitHub

FEEDBACK_FAULT_SCOPE = "pull request feedback poll"


class FeedbackIntake:
    """Turns the reviews and discussion comments found on a waiting pull request into a round."""

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

    async def arrived(self, task_id: str, url: str) -> bool:
        """Return whether `task_id`'s pull request at `url` has feedback awaiting a round.

        A round that is already unfinished counts. Otherwise the reviews and the discussion
        comments under the configured scope, which no earlier round covered, are frozen into a
        new one.
        """
        if self._store.active(task_id) is not None:
            return True
        try:
            feedback = await self._github.pull_request_feedback(self._project_root, url)
        except ShikiruError as error:
            self._faults.report(error, scope=FEEDBACK_FAULT_SCOPE, task_id=task_id)
            return False
        self._faults.resolve(FEEDBACK_FAULT_SCOPE, task_id=task_id)
        if feedback.state is not PullRequestState.OPEN:
            return False
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
