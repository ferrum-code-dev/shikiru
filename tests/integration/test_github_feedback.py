"""GitHub feedback reads and targeted replies, through the subprocess boundary to a fake `gh`."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from shikiru.errors import CommandFailed, ShikiruError
from shikiru.process import SubprocessRunner
from shikiru.workspace.github import (
    GitHub,
    PullRequestComment,
    PullRequestFeedback,
    PullRequestState,
    Review,
    ReviewState,
    ReviewThread,
)
from tests.support import fake_gh
from tests.support.github import comment_node, review_node, thread_node

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path
    from typing import Any

    from tests.support.github import FakeGitHub

_RUNNER = SubprocessRunner()
_HEAD_SHA = "9f1c2e4b7a6d5c3f2e1d0b9a8f7e6d5c4b3a2f1e"
_MARKER = "<!-- shikiru:feedback -->"
_EPOCH = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
_OUTDATED = 1
_BODY_ONLY = 3
_TRICKY_BODIES = (
    "say \"hi\" and 'bye'",
    "$(touch /tmp/shikiru-never-created)",
    "`echo backtick`",
    "first; second && third | fourth > out.txt",
    "line one\nline two\n\n- item",
    "key=value=more",
    "@not-a-file.txt",
    "{owner}/{repo} and {{braces}}",
    "unicode: ünïcødé ✓ 漢字",
)
_REVIEWS = (
    review_node(
        1,
        "PRR_approved",
        "APPROVED",
        body="Looks good.",
        author="alice",
        submitted_at="2026-03-01T10:00:00Z",
    ),
    review_node(
        1, "PRR_empty", "COMMENTED", body="", author="bob", submitted_at="2026-03-01T11:00:00Z"
    ),
    review_node(
        1,
        "PRR_changes",
        "CHANGES_REQUESTED",
        body="Please rename the helper.",
        author="carol",
        submitted_at="2026-03-01T12:00:00Z",
    ),
    review_node(
        1,
        "PRR_body_only",
        "CHANGES_REQUESTED",
        body="The scope is too wide.",
        author="carol",
        submitted_at="2026-03-01T13:00:00Z",
    ),
    review_node(
        1,
        "PRR_dismissed",
        "DISMISSED",
        body="Superseded.",
        author="carol",
        submitted_at="2026-03-01T09:00:00Z",
    ),
    review_node(1, "PRR_pending", "PENDING", body="Draft.", author="me", submitted_at=None),
)
_THREADS = (
    thread_node(
        "PRRT_resolved",
        [
            comment_node(1, 101, "Rename this.", author="carol", review_id="PRR_changes"),
            comment_node(1, 102, "Done in 4f2a1c.", author="alice"),
        ],
        path="src/a.py",
        line=10,
        resolved=True,
    ),
    thread_node(
        "PRRT_outdated",
        [comment_node(1, 103, "Why not a set?", author="carol", review_id="PRR_changes")],
        path="src/b.py",
        line=None,
        outdated=True,
    ),
    thread_node(
        "PRRT_both",
        [comment_node(1, 104, "Nit.", author="bob", review_id="PRR_empty")],
        path="src/c.py",
        line=3,
        outdated=True,
        resolved=True,
    ),
    thread_node(
        "PRRT_standalone",
        [comment_node(1, 105, "Standalone.", author=None)],
        path="src/d.py",
        line=7,
    ),
)


async def _open_with(
    github: GitHub,
    fake_github: FakeGitHub,
    repo_dir: Path,
    *,
    reviews: Sequence[dict[str, Any]] = _REVIEWS,
    threads: Sequence[dict[str, Any]] = _THREADS,
) -> str:
    """Open pull request 1 with `reviews` and `threads` recorded, and return its URL."""
    url = await github.create_pull_request(
        repo_dir, base="main", head="feature", title="Feedback", body=""
    )
    fake_github.set_fields(
        1, headRefOid=_HEAD_SHA, reviews=list(reviews), reviewThreads=list(threads)
    )
    return url


def _review_url(review_id: str) -> str:
    return f"{fake_gh.URL_PREFIX}1#pullrequestreview-{review_id}"


def _comment(comment_id: int, body: str, author: str | None) -> PullRequestComment:
    return PullRequestComment(
        id=f"PRRC_{comment_id}",
        database_id=comment_id,
        url=f"{fake_gh.URL_PREFIX}1#discussion_r{comment_id}",
        author=author,
        body=body,
        created_at=_EPOCH,
    )


async def test_snapshot_has_reviews_discussions_and_head_sha(
    fake_github: FakeGitHub, tmp_path: Path
) -> None:
    github = GitHub(_RUNNER)
    url = await _open_with(github, fake_github, tmp_path)

    feedback = await github.pull_request_feedback(tmp_path, url)

    assert feedback == PullRequestFeedback(
        url=url,
        state=PullRequestState.OPEN,
        head_sha=_HEAD_SHA,
        reviews=(
            Review(
                id="PRR_approved",
                url=_review_url("PRR_approved"),
                state=ReviewState.APPROVED,
                author="alice",
                submitted_at=datetime(2026, 3, 1, 10, tzinfo=UTC),
                body="Looks good.",
            ),
            Review(
                id="PRR_empty",
                url=_review_url("PRR_empty"),
                state=ReviewState.COMMENTED,
                author="bob",
                submitted_at=datetime(2026, 3, 1, 11, tzinfo=UTC),
                body="",
            ),
            Review(
                id="PRR_changes",
                url=_review_url("PRR_changes"),
                state=ReviewState.CHANGES_REQUESTED,
                author="carol",
                submitted_at=datetime(2026, 3, 1, 12, tzinfo=UTC),
                body="Please rename the helper.",
            ),
            Review(
                id="PRR_body_only",
                url=_review_url("PRR_body_only"),
                state=ReviewState.CHANGES_REQUESTED,
                author="carol",
                submitted_at=datetime(2026, 3, 1, 13, tzinfo=UTC),
                body="The scope is too wide.",
            ),
            Review(
                id="PRR_dismissed",
                url=_review_url("PRR_dismissed"),
                state=ReviewState.DISMISSED,
                author="carol",
                submitted_at=datetime(2026, 3, 1, 9, tzinfo=UTC),
                body="Superseded.",
            ),
            Review(
                id="PRR_pending",
                url=_review_url("PRR_pending"),
                state=ReviewState.PENDING,
                author="me",
                submitted_at=None,
                body="Draft.",
            ),
        ),
        threads=(
            ReviewThread(
                id="PRRT_resolved",
                review_id="PRR_changes",
                path="src/a.py",
                line=10,
                is_outdated=False,
                is_resolved=True,
                comments=(
                    _comment(101, "Rename this.", "carol"),
                    _comment(102, "Done in 4f2a1c.", "alice"),
                ),
            ),
            ReviewThread(
                id="PRRT_outdated",
                review_id="PRR_changes",
                path="src/b.py",
                line=None,
                is_outdated=True,
                is_resolved=False,
                comments=(_comment(103, "Why not a set?", "carol"),),
            ),
            ReviewThread(
                id="PRRT_both",
                review_id="PRR_empty",
                path="src/c.py",
                line=3,
                is_outdated=True,
                is_resolved=True,
                comments=(_comment(104, "Nit.", "bob"),),
            ),
            ReviewThread(
                id="PRRT_standalone",
                review_id=None,
                path="src/d.py",
                line=7,
                is_outdated=False,
                is_resolved=False,
                comments=(_comment(105, "Standalone.", None),),
            ),
        ),
    )


async def test_pagination_returns_every_review_and_reply_once_in_order(
    fake_github: FakeGitHub, tmp_path: Path
) -> None:
    github = GitHub(_RUNNER)
    reviews = [
        review_node(1, f"PRR_{number}", "COMMENTED", body=f"review {number}")
        for number in range(1, 6)
    ]
    threads = [
        thread_node(
            "PRRT_long",
            [comment_node(1, 200 + number, f"reply {number}") for number in range(1, 6)],
        ),
        thread_node("PRRT_second", [comment_node(1, 300, "second")]),
        thread_node("PRRT_third", [comment_node(1, 400, "third")]),
    ]
    url = await _open_with(github, fake_github, tmp_path, reviews=reviews, threads=threads)

    feedback = await github.pull_request_feedback(tmp_path, url)

    assert [review.body for review in feedback.reviews] == [f"review {n}" for n in range(1, 6)]
    assert [thread.id for thread in feedback.threads] == ["PRRT_long", "PRRT_second", "PRRT_third"]
    assert [comment.body for comment in feedback.threads[0].comments] == [
        f"reply {number}" for number in range(1, 6)
    ]


@pytest.mark.parametrize("text", _TRICKY_BODIES)
async def test_inline_reply_keeps_its_body_and_lands_in_its_discussion(
    fake_github: FakeGitHub, tmp_path: Path, text: str
) -> None:
    github = GitHub(_RUNNER)
    url = await _open_with(github, fake_github, tmp_path)
    before = await github.pull_request_feedback(tmp_path, url)
    thread = before.threads[_OUTDATED]
    body = f"{_MARKER}\n{text}"

    reply = await github.reply_to_thread(tmp_path, url, thread=thread, body=body)

    after = await github.pull_request_feedback(tmp_path, url)
    assert reply.body == body
    assert after.threads[_OUTDATED].comments == (*thread.comments, reply)
    assert len(after.threads) == len(before.threads)
    assert await github.find_thread_reply(tmp_path, url, thread=thread, marker=_MARKER) == reply


async def test_review_body_response_links_its_review_and_is_found_by_marker(
    fake_github: FakeGitHub, tmp_path: Path
) -> None:
    github = GitHub(_RUNNER)
    url = await _open_with(github, fake_github, tmp_path)
    feedback = await github.pull_request_feedback(tmp_path, url)
    review = feedback.reviews[_BODY_ONLY]
    body = f"{_MARKER}\nAddressed in {review.url}."

    posted = await github.post_conversation_comment(tmp_path, url, body=body)

    assert posted.body == body
    assert await github.find_conversation_comment(tmp_path, url, marker=_MARKER) == posted


async def test_lost_post_response_is_found_by_its_marker_from_a_new_adapter(
    fake_github: FakeGitHub, tmp_path: Path
) -> None:
    url = await _open_with(GitHub(_RUNNER), fake_github, tmp_path)
    fake_github.fail_next("lost-post", 1)
    body = f"{_MARKER}\nlost response"

    with pytest.raises(CommandFailed):
        await GitHub(_RUNNER).post_conversation_comment(tmp_path, url, body=body)

    found = await GitHub(SubprocessRunner()).find_conversation_comment(
        tmp_path, url, marker=_MARKER
    )
    assert found is not None
    assert found.body == body


async def test_find_reports_no_reply_when_no_comment_carries_the_marker(
    fake_github: FakeGitHub, tmp_path: Path
) -> None:
    github = GitHub(_RUNNER)
    url = await _open_with(github, fake_github, tmp_path)
    thread = (await github.pull_request_feedback(tmp_path, url)).threads[_OUTDATED]
    await github.post_conversation_comment(tmp_path, url, body="no marker here")

    assert await github.find_conversation_comment(tmp_path, url, marker=_MARKER) is None
    assert await github.find_thread_reply(tmp_path, url, thread=thread, marker=_MARKER) is None


async def test_failed_reply_records_nothing(fake_github: FakeGitHub, tmp_path: Path) -> None:
    github = GitHub(_RUNNER)
    url = await _open_with(github, fake_github, tmp_path)
    before = await github.pull_request_feedback(tmp_path, url)
    fake_github.fail_next("post", 1)

    with pytest.raises(CommandFailed):
        await github.reply_to_thread(
            tmp_path, url, thread=before.threads[_OUTDATED], body=f"{_MARKER} not sent"
        )

    assert await github.pull_request_feedback(tmp_path, url) == before
    assert (
        await github.find_thread_reply(
            tmp_path, url, thread=before.threads[_OUTDATED], marker=_MARKER
        )
        is None
    )


async def test_failed_feedback_read_surfaces_as_a_command_failure(
    fake_github: FakeGitHub, tmp_path: Path
) -> None:
    github = GitHub(_RUNNER)
    url = await _open_with(github, fake_github, tmp_path)
    fake_github.fail_next("graphql", 1)

    with pytest.raises(CommandFailed):
        await github.pull_request_feedback(tmp_path, url)


async def test_unknown_review_state_is_an_error_not_an_empty_snapshot(
    fake_github: FakeGitHub, tmp_path: Path
) -> None:
    github = GitHub(_RUNNER)
    url = await _open_with(
        github,
        fake_github,
        tmp_path,
        reviews=[review_node(1, "PRR_unknown", "REQUESTED_CHANGES")],
        threads=[],
    )

    with pytest.raises(ShikiruError):
        await github.pull_request_feedback(tmp_path, url)


@pytest.mark.usefixtures("fake_github")
async def test_missing_pull_request_is_an_error_not_an_empty_snapshot(tmp_path: Path) -> None:
    with pytest.raises(ShikiruError):
        await GitHub(_RUNNER).pull_request_feedback(tmp_path, f"{fake_gh.URL_PREFIX}9")


@pytest.mark.usefixtures("fake_github")
async def test_non_pull_request_url_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ShikiruError):
        await GitHub(_RUNNER).pull_request_feedback(
            tmp_path, "https://github.invalid/shikiru/fake/issues/1"
        )
