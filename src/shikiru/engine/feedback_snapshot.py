"""Choose the reviews that start a feedback round and freeze them into a snapshot."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.domain.feedback import (
    FeedbackMessage,
    FeedbackSnapshot,
    FeedbackSource,
    FeedbackSourceKind,
)
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


def build_snapshot(feedback: PullRequestFeedback, reviews: Sequence[Review]) -> FeedbackSnapshot:
    """Freeze `reviews` with their inline discussions, each discussion once and whole."""
    sources: list[FeedbackSource] = []
    listed: set[str] = set()
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
                sources.append(_thread_source(thread))
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


def _thread_source(thread: ReviewThread) -> FeedbackSource:
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
        messages=[_message(comment) for comment in thread.comments],
    )


def _message(comment: PullRequestComment) -> FeedbackMessage:
    return FeedbackMessage(author=comment.author, body=comment.body, url=comment.url)
