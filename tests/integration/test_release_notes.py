"""Release notes list the commits between two release tags, grouped by Conventional Commit type."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
from scripts import release_notes

from shikiru.process import SubprocessRunner
from tests.support.git_repo import commit_all, init_repo, run_git

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

REPO = "ferrum-code-dev/shikiru"
COMMIT_URL = f"https://github.com/{REPO}/commit"
INITIAL_COMMIT = "initial commit"
SHORT_SHA_LENGTH = 7
BREAKING = release_notes.Section.BREAKING
FEATURES = release_notes.Section.FEATURES
FIXES = release_notes.Section.FIXES
OTHER = release_notes.Section.OTHER


@dataclass(frozen=True)
class Tag:
    name: str


type Step = str | Tag


async def build_history(path: Path, steps: Sequence[Step]) -> dict[str, str]:
    """Commit `steps` in order on a fresh repo whose first commit is `INITIAL_COMMIT`.

    Returns the full sha of each commit, keyed by its subject line.
    """
    runner = SubprocessRunner()
    await init_repo(runner, path)
    await commit_all(runner, path, INITIAL_COMMIT, allow_empty=True)
    shas: dict[str, str] = {}
    for step in steps:
        if isinstance(step, Tag):
            await run_git(runner, path, "tag", step.name)
        else:
            await commit_all(runner, path, step, allow_empty=True)
            shas[step.partition("\n")[0]] = await run_git(runner, path, "rev-parse", "HEAD")
    return shas


def grouped(notes: str) -> dict[str, list[str]]:
    """Map each non-empty section heading to the subjects listed under it, in order."""
    groups: dict[str, list[str]] = {}
    heading = ""
    for line in notes.splitlines():
        if line.startswith("### "):
            heading = line
            groups[heading] = []
        elif line.startswith("* "):
            groups[heading].append(line.partition("): ")[2])
    return {heading: subjects for heading, subjects in groups.items() if subjects}


def entry(sha: str, subject: str) -> str:
    return f"* [{sha[:SHORT_SHA_LENGTH]}]({COMMIT_URL}/{sha}): {subject}"


GROUPING_ROWS = [
    pytest.param(
        ["feat(api)!: rename the id field"],
        {BREAKING: ["feat(api)!: rename the id field"]},
        id="bang-marks-breaking",
    ),
    pytest.param(
        ["refactor(store): key rows by id\n\nBREAKING CHANGE: rows are keyed by id", "fix: close"],
        {BREAKING: ["refactor(store): key rows by id"], FIXES: ["fix: close"]},
        id="body-line-marks-breaking",
    ),
    pytest.param(
        ["Drop the old flag\n\nBREAKING CHANGE: --old is gone"],
        {BREAKING: ["Drop the old flag"]},
        id="body-line-breaks-a-non-conventional-subject",
    ),
    pytest.param(
        ["feat(cli): add ls", "fix(parser): stop on blank input"],
        {FEATURES: ["feat(cli): add ls"], FIXES: ["fix(parser): stop on blank input"]},
        id="features-and-fixes",
    ),
    pytest.param(
        ["refactor: split the store", "docs: explain the layout"],
        {OTHER: ["docs: explain the layout", "refactor: split the store"]},
        id="other-types-as-written",
    ),
    pytest.param(
        ["Speed up the dashboard"],
        {OTHER: ["Speed up the dashboard"]},
        id="non-conventional-subject-as-written",
    ),
    pytest.param(
        ["chore: release 0.1.1"],
        {},
        id="release-commits-are-skipped",
    ),
]


@pytest.mark.parametrize(("commits", "expected"), GROUPING_ROWS)
async def test_groups_commits_by_conventional_type(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    commits: list[str],
    expected: dict[str, list[str]],
) -> None:
    await build_history(tmp_path, [Tag("v0.1.0"), *commits, Tag("v0.1.1")])
    monkeypatch.chdir(tmp_path)

    status = release_notes.main(["v0.1.1", "--repo", REPO])

    assert status == release_notes.EXIT_SUCCESS
    assert grouped(capsys.readouterr().out) == expected


async def test_full_notes_order_sections_and_link_commits_compare_and_install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    shas = await build_history(
        tmp_path,
        [
            Tag("v0.1.0"),
            "docs: explain the layout",
            "fix(parser): stop on blank input",
            "feat(cli): add ls",
            "Tidy the readme",
            "feat(api)!: rename the id field",
            "chore: release 0.1.1",
            Tag("v0.1.1"),
        ],
    )
    monkeypatch.chdir(tmp_path)

    status = release_notes.main(["v0.1.1", "--repo", REPO])

    breaking = entry(shas["feat(api)!: rename the id field"], "feat(api)!: rename the id field")
    feature = entry(shas["feat(cli): add ls"], "feat(cli): add ls")
    fix = entry(shas["fix(parser): stop on blank input"], "fix(parser): stop on blank input")
    tidy = entry(shas["Tidy the readme"], "Tidy the readme")
    docs = entry(shas["docs: explain the layout"], "docs: explain the layout")
    wheel = f"https://github.com/{REPO}/releases/download/v0.1.1/shikiru-0.1.1-py3-none-any.whl"
    blocks = [
        f"{BREAKING.value}\n\n{breaking}",
        f"{FEATURES.value}\n\n{feature}",
        f"{FIXES.value}\n\n{fix}",
        f"{OTHER.value}\n\n{tidy}\n{docs}",
        f"**Full Changelog**: [v0.1.0...v0.1.1](https://github.com/{REPO}/compare/v0.1.0...v0.1.1)",
        f"### Install\n\n```sh\nuv tool install {wheel}\n```",
    ]
    assert status == release_notes.EXIT_SUCCESS
    assert capsys.readouterr().out == "\n\n".join(blocks) + "\n"


async def test_previous_tag_is_chosen_by_number_not_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await build_history(
        tmp_path,
        [
            Tag("v0.1.9"),
            "fix: before the tenth",
            Tag("v0.1.10"),
            "feat: after the tenth",
            Tag("v0.1.11"),
        ],
    )
    monkeypatch.chdir(tmp_path)

    status = release_notes.main(["v0.1.11", "--repo", REPO])

    output = capsys.readouterr().out
    assert status == release_notes.EXIT_SUCCESS
    assert grouped(output) == {FEATURES: ["feat: after the tenth"]}
    assert "**Full Changelog**: [v0.1.10...v0.1.11]" in output


async def test_first_release_lists_every_commit_up_to_the_tag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await build_history(tmp_path, ["feat: start", Tag("v0.1.0")])
    monkeypatch.chdir(tmp_path)

    status = release_notes.main(["v0.1.0", "--repo", REPO])

    output = capsys.readouterr().out
    assert status == release_notes.EXIT_SUCCESS
    assert grouped(output) == {OTHER: [INITIAL_COMMIT], FEATURES: ["feat: start"]}
    assert "Full Changelog" not in output


ERROR_ROWS = [
    pytest.param(["1.2.3", "--repo", REPO], "1.2.3", id="not-a-release-tag"),
    pytest.param(["v9.9.9", "--repo", REPO], "v9.9.9", id="unknown-tag"),
    pytest.param(["v0.1.0"], "--repo", id="no-repository"),
]


@pytest.mark.parametrize(("arguments", "fragment"), ERROR_ROWS)
async def test_fails_with_a_message_and_prints_no_notes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    arguments: list[str],
    fragment: str,
) -> None:
    await build_history(tmp_path, [Tag("v0.1.0")])
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(release_notes.REPOSITORY_ENVIRONMENT, raising=False)

    status = release_notes.main(arguments)

    captured = capsys.readouterr()
    assert status == release_notes.EXIT_FAILURE
    assert captured.out == ""
    assert fragment in captured.err
