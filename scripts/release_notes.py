"""Print the changelog section of one release, without its heading, as the release notes."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

DEFAULT_CHANGELOG = Path("CHANGELOG.md")
SECTION_OPEN = "## ["
SECTION_CLOSE = "]"
HEADING_MARK = "## "
EXIT_SUCCESS = 0
EXIT_FAILURE = 1


class SectionError(Exception):
    """The changelog has no usable section for the version; the message says why."""


def section_body(changelog: str, version: str) -> str:
    heading = f"{SECTION_OPEN}{version}{SECTION_CLOSE}"
    lines = changelog.splitlines()
    headings = [index for index, line in enumerate(lines) if line.startswith(heading)]
    if not headings:
        message = f"the changelog has no section for {version}"
        raise SectionError(message)
    body_lines: list[str] = []
    for line in lines[headings[0] + 1 :]:
        if line.startswith(HEADING_MARK):
            break
        body_lines.append(line)
    body = "\n".join(body_lines).strip()
    if not body:
        message = f"the changelog section for {version} is empty"
        raise SectionError(message)
    return body


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print the changelog section of one release.")
    parser.add_argument("version")
    parser.add_argument("--changelog", type=Path, default=DEFAULT_CHANGELOG)
    arguments = parser.parse_args(argv)
    try:
        body = section_body(arguments.changelog.read_text(encoding="utf-8"), arguments.version)
    except SectionError as error:
        sys.stderr.write(f"{error}\n")
        return EXIT_FAILURE
    sys.stdout.write(f"{body}\n")
    return EXIT_SUCCESS


if __name__ == "__main__":
    raise SystemExit(main())
