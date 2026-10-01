"""Unit tests for the no-comments rule checker (docs/design/07-engineering.md §2.8)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support.comment_rule import find_violations


def _flagged(source: str) -> list[str]:
    return [violation.comment for violation in find_violations(source, Path("x.py"))]


@pytest.mark.parametrize(
    "source",
    [
        "#!/usr/bin/env python\nx = 1\n",
        "# -*- coding: utf-8 -*-\nx = 1\n",
        "import os  # noqa: F401\n",
        "import os  # noqa: S701 -- reason text\n",
        "x = f()  # noqa: SLF001  # pyright: ignore[reportAttributeAccessIssue]\n",
        "x = f()  # type: ignore[attr-defined]\n",
        "if a:  # pragma: no cover\n    pass\n",
        "# fmt: off\nx = 1\n",
        "x = 1  # keep: the registry reads this name\n",
        "x = '#id { color: red }'\n",
    ],
)
def test_directives_markers_and_hashes_inside_strings_are_allowed(source: str) -> None:
    assert _flagged(source) == []


@pytest.mark.parametrize(
    ("source", "flagged"),
    [
        ("x = 1  # sets x\n", ["# sets x"]),
        ("x = 1  # keep:\n", ["# keep:"]),
        ("x = 1  # noqa: S606  # and then prose\n", ["# noqa: S606  # and then prose"]),
        ("x = 1  # noqa: S606 because prose\n", ["# noqa: S606 because prose"]),
        ("x = 1  # noqa\n", ["# noqa"]),
        ("x = 1  # noqa: S606 --\n", ["# noqa: S606 --"]),
        ("x = 1  # pyright: ignore[reportX] explained\n", ["# pyright: ignore[reportX] explained"]),
        ("x = 1  # pragma: no cover because\n", ["# pragma: no cover because"]),
        ("x = 1  # pyright: whatever\n", ["# pyright: whatever"]),
        ("x = 1\n#!/not/a/shebang\n\n\n", ["#!/not/a/shebang"]),
    ],
)
def test_plain_and_reasonless_comments_are_rejected(source: str, flagged: list[str]) -> None:
    assert _flagged(source) == flagged


def test_violation_reports_the_comment_line() -> None:
    (violation,) = find_violations("x = 1\ny = 2  # why\n", Path("a/b.py"))
    assert (violation.path, violation.line) == (Path("a/b.py"), 2)
