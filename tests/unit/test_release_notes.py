"""Release notes are the changelog section of the version; a missing or empty section fails."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from scripts import release_notes

if TYPE_CHECKING:
    from pathlib import Path

CHANGELOG = """# Changelog

## [Unreleased]

## [0.2.0] - 2026-12-01

### Added

- Newer feature.

## [0.1.1] - 2026-11-20

### Fixed

- Older fix.

## [0.1.0] - 2026-11-07

### Added

- First release.

## [0.1] - 2026-10-01

### Added

- Early preview.

## [0.3.0] - 2027-01-01

## [0.0.9] - 2026-09-01

### Added

- Pre-release.
"""


def write_changelog(tmp_path: Path) -> Path:
    path = tmp_path / "CHANGELOG.md"
    path.write_text(CHANGELOG, encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("0.1.1", "### Fixed\n\n- Older fix."),
        ("0.2.0", "### Added\n\n- Newer feature."),
        ("0.0.9", "### Added\n\n- Pre-release."),
        ("0.1.0", "### Added\n\n- First release."),
        ("0.1", "### Added\n\n- Early preview."),
    ],
)
def test_prints_the_section_body_without_its_heading(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], version: str, expected: str
) -> None:
    changelog = write_changelog(tmp_path)

    status = release_notes.main([version, "--changelog", str(changelog)])

    assert status == release_notes.EXIT_SUCCESS
    assert capsys.readouterr().out == f"{expected}\n"


@pytest.mark.parametrize("version", ["9.9.9", "0.3.0"])
def test_fails_with_a_message_when_the_section_is_missing_or_empty(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], version: str
) -> None:
    changelog = write_changelog(tmp_path)

    status = release_notes.main([version, "--changelog", str(changelog)])

    captured = capsys.readouterr()
    assert status == release_notes.EXIT_FAILURE
    assert captured.out == ""
    assert version in captured.err
