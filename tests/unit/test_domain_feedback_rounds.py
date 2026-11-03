"""Pure rules of PR feedback rounds: publication, alignment, reply composition."""

from __future__ import annotations

import pytest

from shikiru.domain import reports
from shikiru.domain.feedback_rounds import (
    Alignment,
    PublicationAction,
    align_with_remote,
    describe_checks,
    plan_publication,
    reply_body,
    reply_marker,
)

_TIP = "tip"
_EXPECTED = "expected"
_OTHER = "other"


@pytest.mark.parametrize(
    ("remote_head", "local_tip", "action"),
    [
        (_EXPECTED, _TIP, PublicationAction.PUSH),
        (_TIP, _TIP, PublicationAction.ALREADY_PUBLISHED),
        (_OTHER, _TIP, PublicationAction.REMOTE_MOVED),
        (_EXPECTED, _EXPECTED, PublicationAction.ALREADY_PUBLISHED),
    ],
    ids=["push", "published before a restart", "someone else pushed", "nothing changed"],
)
def test_publication_never_overwrites_a_head_the_round_did_not_expect(
    remote_head: str, local_tip: str, action: PublicationAction
) -> None:
    assert (
        plan_publication(expected=_EXPECTED, remote_head=remote_head, local_tip=local_tip) is action
    )


@pytest.mark.parametrize(
    ("remote_in_local", "local_in_remote", "alignment"),
    [
        (True, True, Alignment.ALIGNED),
        (True, False, Alignment.ALIGNED),
        (False, True, Alignment.FAST_FORWARD),
        (False, False, Alignment.DIVERGED),
    ],
)
def test_the_branch_is_aligned_only_when_no_remote_work_would_be_lost(
    remote_in_local: bool, local_in_remote: bool, alignment: Alignment
) -> None:
    assert (
        align_with_remote(remote_in_local=remote_in_local, local_in_remote=local_in_remote)
        is alignment
    )


def test_a_fixed_reply_names_the_commit_and_the_checks_and_ends_with_its_marker() -> None:
    marker = reply_marker("T-1", 3, "thread")
    body = reply_body(
        reply="Renamed.", marker=marker, review_url=None, fixed_sha="a" * 40, checks="unit passed"
    )

    assert body.startswith("Renamed.")
    assert f"Fixed in {'a' * 12}. Verified by unit passed." in body
    assert body.endswith(marker)


def test_a_review_body_reply_links_the_review_and_claims_no_fix() -> None:
    body = reply_body(
        reply="Disagree.", marker="m", review_url="https://x/review", fixed_sha=None, checks=""
    )

    assert body.startswith("In reply to https://x/review")
    assert "Fixed in" not in body


def test_checks_name_each_run_and_its_result() -> None:
    runs = [
        reports.TestRun(name="unit", result="passed"),
        reports.TestRun(name="lint", result="failed"),
    ]

    assert describe_checks(runs) == "unit passed; lint failed"
