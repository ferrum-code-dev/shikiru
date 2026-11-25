"""Reading and comparing the version a tool prints."""

from __future__ import annotations

import re

VERSION_PATTERN = re.compile(r"(\d+)\.(\d+)")

Version = tuple[int, int]


def parse_version(text: str) -> Version | None:
    """Return the first `major.minor` in `text`, ignoring suffixes like the `a` of `3.3a`."""
    found = VERSION_PATTERN.search(text)
    if found is None:
        return None
    major, minor = found.groups()
    return int(major), int(minor)


def version_shortfall(text: str, minimum: Version) -> str | None:
    """Return why the version `text` reports does not meet `minimum`, or None when it does."""
    found = parse_version(text)
    if found is None:
        return f"{text}: no version found"
    if found < minimum:
        return f"{text}: {minimum[0]}.{minimum[1]} or newer is required"
    return None
