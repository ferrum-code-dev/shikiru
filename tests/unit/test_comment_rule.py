"""Unit tests for the no-comments rule checker (docs/design/07-engineering.md §2.8)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support.comment_rule import find_violations

_ALLOWED: list[str] = [
    "#!/usr/bin/env python\nx = 1\n",
    "import os  # noqa: S701 -- reason text\n",
    "x = f()  # noqa: SLF001  # pyright: ignore[reportAttributeAccessIssue]\n",
    "if a:  # pragma: no cover\n    pass\n",
    "x = 1  # keep: the registry reads this name\n",
    "x = '#id { color: red }'\n",
]
_REJECTED: list[tuple[str, list[str]]] = [
    ("x = 1  # sets x\n", ["# sets x"]),
    ("x = 1  # keep:\n", ["# keep:"]),
    ("x = 1  # noqa: S606 because prose\n", ["# noqa: S606 because prose"]),
    ("x = 1  # noqa\n", ["# noqa"]),
    ("x = 1  # pyright: ignore[reportX] explained\n", ["# pyright: ignore[reportX] explained"]),
    ("x = 1\n#!/not/a/shebang\n\n\n", ["#!/not/a/shebang"]),
]


_CASES = [*((source, list[str]()) for source in _ALLOWED), *_REJECTED]


@pytest.mark.parametrize(("source", "flagged"), _CASES)
def test_only_documented_directive_forms_and_keep_markers_may_stand_as_comments(
    source: str, flagged: list[str]
) -> None:
    found = [violation.comment for violation in find_violations(source, Path("x.py"))]

    assert found == flagged
