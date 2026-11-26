"""The auto-merge request GitHub reports for a pull request, read from `gh pr view`."""

from __future__ import annotations

import json

import pytest

from shikiru.workspace.github import AutoMergeRequest, parse_auto_merge_request


@pytest.mark.parametrize(
    ("view", "expected"),
    [
        ({"autoMergeRequest": None}, None),
        (
            {
                "autoMergeRequest": {
                    "mergeMethod": "SQUASH",
                    "commitHeadline": "feat: change (T-1)",
                    "commitBody": "Whole change.\n\nBREAKING CHANGE: none",
                }
            },
            AutoMergeRequest(
                "SQUASH", "feat: change (T-1)", "Whole change.\n\nBREAKING CHANGE: none"
            ),
        ),
        (
            {
                "autoMergeRequest": {
                    "mergeMethod": "SQUASH",
                    "commitHeadline": "feat: change (T-1)",
                    "commitBody": None,
                }
            },
            AutoMergeRequest("SQUASH", "feat: change (T-1)", ""),
        ),
    ],
    ids=["none held", "body present", "empty body"],
)
def test_the_held_auto_merge_request_is_read_as_gh_reports_it(
    view: dict[str, object], expected: AutoMergeRequest | None
) -> None:
    assert parse_auto_merge_request(json.dumps(view)) == expected
