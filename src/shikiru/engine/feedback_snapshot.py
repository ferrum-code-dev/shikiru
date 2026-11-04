"""Choose the reviews and discussion comments that start a feedback round and freeze them."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.domain.feedback import (
    FeedbackMessage,
    FeedbackSnapshot,
    FeedbackSource,
    FeedbackSourceKind,
)
from shikiru.domain.feedback_rounds import is_own_reply
from shikiru.workspace.github import ReviewState

if TYPE_CHECKING:
    from collections.abc import Sequence

    from shikiru.workspace.github import (
        PullRequestComment,
        PullRequestFeedback,
        Review,
        ReviewThread,
    )

LOCATION_SEPARATOR = ":"


def new_change_requests(feedback: PullRequestFeedback, seen: set[str]) -> list[Review]:
    """Return the submitted changes-requested reviews no round was started for, oldest first."""
    return [
        review
        for review in feedback.reviews
        if review.state is ReviewState.CHANGES_REQUESTED
        and review.submitted_at is not None
        and review.id not in seen
    ]


def new_discussion_comments(
    feedback: PullRequestFeedback, seen: set[str]
) -> list[PullRequestComment]:
    """Return the comments no round covered in discussions holding a changes-requested comment.

    A discussion is followed while any of its comments belongs to a review that still requests
    changes, resolved or not; a dismissed review no longer qualifies. Our own replies never count.
    """
    requesting = {
        review.id for review in feedback.reviews if review.state is ReviewState.CHANGES_REQUESTED
    }
    return [
        comment
        for thread in feedback.threads
        if any(comment.review_id in requesting for comment in thread.comments)
        for comment in thread.comments
        if comment.id not in seen and not is_own_reply(comment.body)
    ]


def build_snapshot(
    feedback: PullRequestFeedback,
    reviews: Sequence[Review],
    comments: Sequence[PullRequestComment],
) -> FeedbackSnapshot:
    """Freeze `reviews` and the discussions of `reviews` and `comments`, each discussion whole.

    The `comments` are marked new in their discussions.
    """
    sources: list[FeedbackSource] = []
    listed: set[str] = set()
    fresh = {comment.id for comment in comments}
    for review in reviews:
        threads = [
            thread
            for thread in feedback.threads
            if any(comment.review_id == review.id for comment in thread.comments)
        ]
        if review.body.strip() or not threads:
            sources.append(_review_source(review))
        for thread in threads:
            if thread.id not in listed:
                listed.add(thread.id)
                sources.append(_thread_source(thread, fresh))
    for thread in feedback.threads:
        if thread.id not in listed and any(comment.id in fresh for comment in thread.comments):
            listed.add(thread.id)
            sources.append(_thread_source(thread, fresh))
    return FeedbackSnapshot(
        pull_request_url=feedback.url, head_sha=feedback.head_sha, sources=sources
    )


def _review_source(review: Review) -> FeedbackSource:
    return FeedbackSource(
        id=review.id,
        kind=FeedbackSourceKind.REVIEW_BODY,
        url=review.url,
        messages=[FeedbackMessage(author=review.author, body=review.body, url=review.url)],
    )


def _thread_source(thread: ReviewThread, fresh: set[str]) -> FeedbackSource:
    location = thread.path
    if thread.line is not None:
        location = f"{thread.path}{LOCATION_SEPARATOR}{thread.line}"
    return FeedbackSource(
        id=thread.id,
        kind=FeedbackSourceKind.DISCUSSION,
        url=thread.comments[0].url,
        location=location,
        outdated=thread.is_outdated,
        resolved=thread.is_resolved,
        messages=[_message(comment, fresh) for comment in thread.comments],
    )


def _message(comment: PullRequestComment, fresh: set[str]) -> FeedbackMessage:
    return FeedbackMessage(
        author=comment.author, body=comment.body, url=comment.url, new=comment.id in fresh
    )
