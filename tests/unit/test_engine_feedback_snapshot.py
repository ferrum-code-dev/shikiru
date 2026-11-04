"""Which reviews start a feedback round and how they are frozen."""

from __future__ import annotations

from datetime import UTC, datetime

from shikiru.domain.feedback import FeedbackSourceKind
from shikiru.domain.feedback_rounds import reply_marker
from shikiru.engine.feedback_snapshot import (
    build_snapshot,
    new_change_requests,
    new_discussion_comments,
)
from shikiru.workspace.github import (
    PullRequestComment,
    PullRequestFeedback,
    PullRequestState,
    Review,
    ReviewState,
    ReviewThread,
)

_MOMENT = datetime(2026, 1, 1, tzinfo=UTC)


def _review(
    review_id: str, state: ReviewState, *, body: str = "", submitted: bool = True
) -> Review:
    return Review(
        id=review_id,
        url=f"https://x/{review_id}",
        state=state,
        author="r",
        submitted_at=_MOMENT if submitted else None,
        body=body,
    )


def _comment(comment_id: int, review_id: str | None, body: str = "b") -> PullRequestComment:
    return PullRequestComment(
        id=f"c{comment_id}",
        database_id=comment_id,
        review_id=review_id,
        url=f"https://x/c{comment_id}",
        author="r",
        body=body,
        created_at=_MOMENT,
    )


def _feedback(reviews: list[Review], threads: list[ReviewThread]) -> PullRequestFeedback:
    return PullRequestFeedback(
        url="https://x/pr",
        state=PullRequestState.OPEN,
        head_sha="sha",
        reviews=tuple(reviews),
        threads=tuple(threads),
    )


def test_only_submitted_unseen_change_requests_start_a_round() -> None:
    feedback = _feedback(
        [
            _review("new", ReviewState.CHANGES_REQUESTED),
            _review("seen", ReviewState.CHANGES_REQUESTED),
            _review("ok", ReviewState.APPROVED),
            _review("note", ReviewState.COMMENTED),
            _review("gone", ReviewState.DISMISSED),
            _review("draft", ReviewState.PENDING, submitted=False),
        ],
        [],
    )

    assert [r.id for r in new_change_requests(feedback, {"seen"})] == ["new"]


def test_a_discussion_shared_by_two_reviews_is_one_source() -> None:
    thread = ReviewThread(
        id="t",
        path="a.py",
        line=3,
        is_outdated=False,
        is_resolved=False,
        comments=(_comment(1, "r1"), _comment(2, "r2")),
    )
    reviews = [
        _review("r1", ReviewState.CHANGES_REQUESTED),
        _review("r2", ReviewState.CHANGES_REQUESTED, body="also this"),
    ]

    snapshot = build_snapshot(_feedback(reviews, [thread]), reviews, list(thread.comments))

    assert snapshot.source_ids == ["t", "r2"]
    assert snapshot.sources[0].kind is FeedbackSourceKind.DISCUSSION
    assert snapshot.sources[0].location == "a.py:3"


def _thread(*comments: PullRequestComment, resolved: bool = False) -> ReviewThread:
    return ReviewThread(
        id="t",
        path="a.py",
        line=3,
        is_outdated=False,
        is_resolved=resolved,
        comments=comments,
    )


def test_new_comments_of_followed_discussions_start_a_round() -> None:
    own = _comment(3, None, reply_marker("T-1", 1, "t"))
    cases: list[tuple[ReviewState, bool, set[str], list[str]]] = [
        (ReviewState.CHANGES_REQUESTED, False, {"c1"}, ["c2"]),
        (ReviewState.CHANGES_REQUESTED, True, {"c1"}, ["c2"]),
        (ReviewState.CHANGES_REQUESTED, False, {"c1", "c2"}, []),
        (ReviewState.DISMISSED, False, set(), []),
        (ReviewState.COMMENTED, False, set(), []),
        (ReviewState.APPROVED, False, set(), []),
    ]
    for state, resolved, seen, expected in cases:
        thread = _thread(_comment(1, "r"), _comment(2, None), own, resolved=resolved)
        feedback = _feedback([_review("r", state)], [thread])

        found = [c.id for c in new_discussion_comments(feedback, seen)]

        assert found == expected, (state, resolved, seen)


def test_a_discussion_enters_whole_with_only_its_new_comments_marked() -> None:
    own = _comment(2, None, reply_marker("T-1", 1, "t"))
    thread = _thread(_comment(1, "r"), own, _comment(3, None))
    feedback = _feedback([_review("r", ReviewState.CHANGES_REQUESTED)], [thread])

    comments = new_discussion_comments(feedback, {"c1"})
    snapshot = build_snapshot(feedback, [], comments)

    assert snapshot.source_ids == ["t"]
    assert [message.new for message in snapshot.sources[0].messages] == [False, False, True]
