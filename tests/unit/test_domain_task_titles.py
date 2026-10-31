from __future__ import annotations

import pytest

from shikiru.domain.tasks import TASK_TITLE_MAX_LENGTH, title_from_request


@pytest.mark.parametrize(
    ("request_text", "expected"),
    [
        pytest.param("", "", id="empty-request-kept"),
        pytest.param("Add retry to parser", "Add retry to parser", id="short-line-unchanged"),
        pytest.param(
            "Show the acceptance criteria once in the task description is",
            "Show the acceptance criteria once in the task description is",
            id="exactly-max-length-unchanged",
        ),
        pytest.param(
            "Dashboard: show the acceptance criteria once in the task description",
            "Dashboard: show the acceptance criteria once in the task…",
            id="long-line-cut-at-word-boundary",
        ),
        pytest.param(
            "a" * 58 + " " + "b" * 10,
            "a" * 58 + "…",
            id="space-at-window-end-cuts-before-it",
        ),
        pytest.param("a" * 70, "a" * 59 + "…", id="unbroken-token-hard-cut"),
        pytest.param(
            "Fix the parser\nsecond line is ignored",
            "Fix the parser",
            id="multi-line-uses-first-line-only",
        ),
    ],
)
def test_title_from_request_cuts_at_a_word_boundary_within_the_limit(
    request_text: str, expected: str
) -> None:
    title = title_from_request(request_text)

    assert title == expected
    assert len(title) <= TASK_TITLE_MAX_LENGTH
