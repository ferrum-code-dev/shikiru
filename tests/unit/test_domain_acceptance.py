from __future__ import annotations

import pytest

from shikiru.domain.acceptance import acceptance_criteria_from_request

_CASES: list[tuple[str, tuple[str, ...]]] = [
    (
        "## Acceptance criteria\n- rejects bad input\n- keeps good input\n",
        ("rejects bad input", "keeps good input"),
    ),
    ("Make it strict.\n\n## Acceptance criteria\n- one\n", ("one",)),
    (
        "### acceptance CRITERIA  \n* heading any level, any case\n",
        ("heading any level, any case",),
    ),
    ("## Acceptance criteria\n1. first\n2) second\n", ("first", "second")),
    ("## Acceptance criteria\n- a\n\n- b\n", ("a", "b")),
    ("## Acceptance criteria\n- a\n## Scope\n- not a criterion\n", ("a",)),
    ("## Acceptance criteria\n- a\n\nNotes\n- not a criterion\n", ("a",)),
    ("## Scope\n- not a criterion\n## Acceptance criteria\n- a\n", ("a",)),
    ("## Scope\n- not a criterion\n", ()),
    ("Acceptance criteria\n- no heading marker\n", ()),
    ("## Acceptance criteria\nNo bullets here.\n- not a criterion\n", ()),
    ("## Acceptance criteria\n", ()),
]


@pytest.mark.parametrize(("request_text", "criteria"), _CASES)
def test_criteria_are_the_bullets_under_the_heading_up_to_the_first_non_bullet_line(
    request_text: str, criteria: tuple[str, ...]
) -> None:
    assert acceptance_criteria_from_request(request_text) == criteria
