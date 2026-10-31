from __future__ import annotations

import pytest

from shikiru.domain.acceptance import (
    acceptance_criteria_from_request,
    request_without_criteria_section,
)

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
    ("Acceptance criteria\n- a\n", ("a",)),
    ("Acceptance criteria:\n- a\n", ("a",)),
    ("**Acceptance criteria**\n- a\n", ("a",)),
    ("**Acceptance criteria:**\n- a\n", ("a",)),
    ("**Acceptance criteria**:\n- a\n", ("a",)),
    ("## **Acceptance criteria:**\n- a\n", ("a",)),
    ("**acceptance CRITERIA:**\n- a\n", ("a",)),
    ("We set Acceptance criteria: to be strict.\n- not a criterion\n", ()),
    ("The acceptance criteria are:\n- not a criterion\n", ()),
    ("Acceptance criteria for parsing\n- not a criterion\n", ()),
    ("**Acceptance criteria\n- not a criterion\n", ()),
    ("```\n**Acceptance criteria:**\n- example\n```\n", ()),
    ("## Acceptance criteria\nNo bullets here.\n- not a criterion\n", ()),
    ("## Acceptance criteria\n", ()),
    (
        (
            "## Acceptance criteria\n- Reject invalid input and\n  preserve the original file.\n"
            "- Keep valid input.\n"
        ),
        ("Reject invalid input and preserve the original file.", "Keep valid input."),
    ),
    ("## Acceptance criteria\n- a\n  b\n## Scope\n- not a criterion\n", ("a b",)),
    ("## Acceptance criteria\n- a\n  b\nNotes\n- not a criterion\n", ("a b",)),
    ("## Acceptance criteria\n- a\n```\n- example\n```\n- not a criterion\n", ("a",)),
    ("```markdown\n## Acceptance criteria\n- example only\n```\n", ()),
    (
        (
            "```markdown\n## Acceptance criteria\n- example only\n```\n\n"
            "## Acceptance criteria\n- real\n"
        ),
        ("real",),
    ),
    ("````markdown\n```\n## Acceptance criteria\n- example\n```\n````\n", ()),
]


@pytest.mark.parametrize(("request_text", "criteria"), _CASES)
def test_criteria_are_the_bullets_and_their_continuations_under_the_heading_outside_code(
    request_text: str, criteria: tuple[str, ...]
) -> None:
    assert acceptance_criteria_from_request(request_text) == criteria


_WITHOUT_SECTION_CASES: list[tuple[str, str]] = [
    ("Make it strict.\n\n## Acceptance criteria\n- one\n", "Make it strict."),
    ("Before.\n\n## Acceptance criteria\n- a\n  b\n\nAfter.\n", "Before.\n\nAfter."),
    ("Acceptance criteria\n- a\n\nAfter the section.\n", "After the section."),
    ("Before.\n\n**Acceptance criteria:**\n- a\n", "Before."),
    (
        "Before.\n\n```\nexample\n```\n\nAcceptance criteria:\n- a\n",
        "Before.\n\n```\nexample\n```",
    ),
    ("## Acceptance criteria\n- a\n", ""),
    (
        "Before.\n\n## Acceptance criteria\n- a\n```\nexample\n```\n",
        "Before.\n\n```\nexample\n```",
    ),
    (
        "```\n## Acceptance criteria\n- example\n```\n\n## Acceptance criteria\n- real\n",
        "```\n## Acceptance criteria\n- example\n```",
    ),
    (
        "We set Acceptance criteria: to be strict.\n- not a criterion\n",
        "We set Acceptance criteria: to be strict.\n- not a criterion\n",
    ),
    (
        "## Acceptance criteria\nNo bullets here.\n- not a criterion\n",
        "## Acceptance criteria\nNo bullets here.\n- not a criterion\n",
    ),
    (
        "Make it strict.\n\n- a list that is not criteria\n",
        "Make it strict.\n\n- a list that is not criteria\n",
    ),
]


@pytest.mark.parametrize(("request_text", "remaining"), _WITHOUT_SECTION_CASES)
def test_the_criteria_section_is_removed_and_every_other_line_kept(
    request_text: str, remaining: str
) -> None:
    assert request_without_criteria_section(request_text) == remaining
