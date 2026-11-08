"""Release rules: versions compare by number, and the files change only where their version is."""

from __future__ import annotations

import pytest
from scripts import release

CURRENT = (0, 1, 9)
NEW = (0, 1, 10)

PYPROJECT = '[project]\nname = "shikiru"\nversion = "0.1.9"\nrequires-python = ">=3.14"\n'
LOCKFILE = (
    '[[package]]\nname = "fake-agent"\nversion = "0.1.9"\n\n'
    '[[package]]\nname = "shikiru"\nversion = "0.1.9"\nsource = { editable = "." }\n'
)
README = (
    "Install v0.1.9 from releases/download/v0.1.9/shikiru-0.1.9-py3-none-any.whl.\n"
    "Versions 0.1.90, 0.1.9.1 and 10.1.9 stay.\n"
    "Version 0.1.9.\n"
)


@pytest.mark.parametrize(
    ("request_", "expected"),
    [
        pytest.param("patch", "0.1.10", id="patch-compares-numbers"),
        pytest.param("minor", "0.2.0", id="minor-resets-patch"),
        pytest.param("major", "1.0.0", id="major-resets-minor-and-patch"),
        pytest.param("0.1.10", "0.1.10", id="explicit-newer-by-number"),
        pytest.param("0.2.0", "0.2.0", id="explicit-minor"),
    ],
)
def test_next_version_is_newer_by_number(request_: str, expected: str) -> None:
    assert release.format_version(release.next_version(CURRENT, request_)) == expected


@pytest.mark.parametrize(
    ("request_", "reason"),
    [
        pytest.param("0.1.9", "not newer", id="equal-is-refused"),
        pytest.param("0.1.8", "not newer", id="lower-is-refused"),
        pytest.param("v0.2.0", "is not patch", id="tag-prefix-is-refused"),
        pytest.param("0.2", "is not patch", id="incomplete-is-refused"),
        pytest.param("latest", "is not patch", id="word-is-refused"),
    ],
)
def test_next_version_refuses_anything_not_newer(request_: str, reason: str) -> None:
    with pytest.raises(release.ReleaseError, match=reason):
        release.next_version(CURRENT, request_)


def test_pyproject_changes_only_its_version_line() -> None:
    rewritten = release.rewrite_pyproject(PYPROJECT, CURRENT, NEW)

    assert rewritten == PYPROJECT.replace('version = "0.1.9"', 'version = "0.1.10"')


def test_lockfile_changes_only_the_project_entry() -> None:
    rewritten = release.rewrite_lockfile(LOCKFILE, CURRENT, NEW)

    assert rewritten == LOCKFILE.replace(
        'name = "shikiru"\nversion = "0.1.9"', 'name = "shikiru"\nversion = "0.1.10"'
    )


def test_lockfile_refuses_a_project_entry_at_another_version() -> None:
    lockfile = LOCKFILE.replace(
        'name = "shikiru"\nversion = "0.1.9"', 'name = "shikiru"\nversion = "0.1.8"'
    )

    with pytest.raises(release.ReleaseError, match=r"uv\.lock must hold the current version"):
        release.rewrite_lockfile(lockfile, CURRENT, NEW)


def test_readme_changes_whole_version_tokens_in_urls_and_prose() -> None:
    rewritten = release.rewrite_readme(README, CURRENT, NEW)

    assert rewritten == (
        "Install v0.1.10 from releases/download/v0.1.10/shikiru-0.1.10-py3-none-any.whl.\n"
        "Versions 0.1.90, 0.1.9.1 and 10.1.9 stay.\n"
        "Version 0.1.10.\n"
    )
