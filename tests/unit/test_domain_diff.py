"""Rules for classifying the lines of a Git patch."""

from __future__ import annotations

import pytest

from shikiru.domain.diff import DiffLineKind, classify_diff_lines

_HEADER = ("diff --git a/f b/f", "--- a/f", "+++ b/f")
_CONTEXT = DiffLineKind.CONTEXT


@pytest.mark.parametrize(
    ("lines", "kinds"),
    [
        (_HEADER, (_CONTEXT, _CONTEXT, _CONTEXT)),
        (
            ("@@ -1,2 +1,2 @@", " kept", "-old", "+new", "\\ No newline at end of file"),
            (
                DiffLineKind.HUNK,
                _CONTEXT,
                DiffLineKind.REMOVED,
                DiffLineKind.ADDED,
                _CONTEXT,
            ),
        ),
        (
            ("@@ -1 +1 @@", "--- comment", "+++ counter"),
            (DiffLineKind.HUNK, DiffLineKind.REMOVED, DiffLineKind.ADDED),
        ),
        (
            ("@@ -1 +0,0 @@", "-old", *_HEADER),
            (DiffLineKind.HUNK, DiffLineKind.REMOVED, _CONTEXT, _CONTEXT, _CONTEXT),
        ),
    ],
    ids=["file-header", "hunk", "header-like-content", "second-file-header"],
)
def test_classify_diff_lines(lines: tuple[str, ...], kinds: tuple[DiffLineKind, ...]) -> None:
    assert classify_diff_lines(lines) == kinds
