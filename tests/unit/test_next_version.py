"""The next release version follows from the highest change among the commits since the release."""

from __future__ import annotations

import pytest
from scripts import next_version, release_notes

SHA = "0" * 40


def commit(message: str) -> release_notes.Commit:
    subject, _, body = message.partition("\n")
    return release_notes.Commit(SHA, subject, body.strip())


PROPOSAL_ROWS = [
    pytest.param((0, 0, 0), ["feat: start"], (0, 0, 1), id="no-tag-starts-from-zero"),
    pytest.param((0, 1, 0), ["fix: a fix"], (0, 1, 1), id="fix-patches-below-one"),
    pytest.param((0, 1, 0), ["perf: faster"], (0, 1, 1), id="perf-patches-below-one"),
    pytest.param((0, 1, 0), ["feat: add ls"], (0, 1, 1), id="feature-patches-below-one"),
    pytest.param((0, 1, 4), ["feat!: drop ls"], (0, 2, 0), id="bang-minors-below-one"),
    pytest.param(
        (0, 1, 4),
        ["Drop ls\n\nBREAKING CHANGE: ls is gone"],
        (0, 2, 0),
        id="body-line-minors-below-one",
    ),
    pytest.param(
        (0, 1, 4),
        ["fix: a fix", "feat!: drop ls"],
        (0, 2, 0),
        id="highest-bump-wins-below-one",
    ),
    pytest.param((1, 2, 3), ["fix: a fix"], (1, 2, 4), id="fix-patches-from-one"),
    pytest.param((1, 2, 3), ["perf: faster"], (1, 2, 4), id="perf-patches-from-one"),
    pytest.param((1, 2, 3), ["feat: add ls", "fix: a fix"], (1, 3, 0), id="feature-minors"),
    pytest.param((1, 2, 3), ["feat!: drop ls", "fix: a fix"], (2, 0, 0), id="bang-majors"),
    pytest.param(
        (1, 2, 3),
        ["Drop ls\n\nBREAKING CHANGE: ls is gone"],
        (2, 0, 0),
        id="body-line-majors",
    ),
    pytest.param(
        (1, 2, 3),
        ["feat: add ls", "feat!: drop ls"],
        (2, 0, 0),
        id="breaking-beats-feature",
    ),
    pytest.param(
        (1, 2, 3),
        ["refactor(api)!: rename ids\n\nBREAKING CHANGE: ids are strings"],
        (2, 0, 0),
        id="scoped-bang-with-body-line",
    ),
]

NOTHING_ROWS = [
    pytest.param((1, 2, 3), ["docs: explain", "test: cover it"], id="only-docs-and-tests"),
    pytest.param((1, 2, 3), ["chore: bump deps", "refactor: split"], id="only-other-types"),
    pytest.param((1, 2, 3), ["Speed up the dashboard"], id="non-conventional-subject"),
    pytest.param((0, 1, 0), [], id="no-commits-since-the-release"),
]


@pytest.mark.parametrize(("last", "messages", "expected"), PROPOSAL_ROWS)
def test_proposes_the_version_the_rule_gives(
    last: next_version.Version,
    messages: list[str],
    expected: next_version.Version,
) -> None:
    proposed = next_version.propose(last, [commit(message) for message in messages])

    assert proposed == expected


@pytest.mark.parametrize(("last", "messages"), NOTHING_ROWS)
def test_nothing_to_release_when_no_commit_changes_the_version(
    last: next_version.Version,
    messages: list[str],
) -> None:
    with pytest.raises(next_version.NothingToRelease):
        next_version.propose(last, [commit(message) for message in messages])
