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
