from __future__ import annotations

import re

import pytest

from shikiru.domain.fencing import fence


@pytest.mark.parametrize(
    ("content", "expected_fence"),
    [
        ("plain text", "```"),
        ("a fenced ``` block", "````"),
        ("a longer ```` fence", "`````"),
        ("````\n```\n````", "`````"),
        ("", "```"),
    ],
    ids=[
        "no-backticks-gets-the-minimum-fence",
        "one-triple-backtick-run-forces-a-longer-fence",
        "one-quadruple-backtick-run-forces-a-longer-fence-still",
        "the-longest-run-in-the-content-wins",
        "empty-content-gets-the-minimum-fence",
    ],
)
def test_fence_picks_a_marker_longer_than_every_backtick_run_in_the_content(
    content: str, expected_fence: str
) -> None:
    fenced = fence(content, "text")

    lines = fenced.splitlines()
    assert lines[0] == f"{expected_fence}text"
    assert lines[-1] == expected_fence


@pytest.mark.parametrize(
    "content",
    [
        "plain text",
        "a fenced ``` block",
        "````\n```\n````",
        '```json\n{"a": 1}\n```',
    ],
)
def test_fence_round_trips_the_content_between_one_opening_and_closing_marker(
    content: str,
) -> None:
    fenced = fence(content, "text")

    opening_marker = re.match(r"^`+", fenced)
    assert opening_marker is not None
    marker = opening_marker.group()

    assert fenced == f"{marker}text\n{content}\n{marker}"
    longest_run_in_content = max((len(run) for run in re.findall(r"`+", content)), default=0)
    assert len(marker) > longest_run_in_content
