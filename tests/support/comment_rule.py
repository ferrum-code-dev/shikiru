"""The no-comments rule of docs/design/07-engineering.md §2.8, checked mechanically."""

from __future__ import annotations

import io
import re
import sys
import tokenize
from dataclasses import dataclass
from pathlib import Path

CHECKED_ROOTS = ("src", "tests", "scripts")
IGNORED_DIRECTORIES = frozenset({".venv", "__pycache__"})
EXCLUSION_MARKER = "keep:"

_SHEBANG_LINE = 1
_ENCODING_LINES = 2
_SHEBANG = re.compile(r"^#!")
_ENCODING = re.compile(r"^#.*coding[:=]")
_CODE = r"[A-Z]+\d+"
_DIRECTIVES = (
    rf"noqa:\s*{_CODE}(?:\s*,\s*{_CODE})*(?:\s+--\s+\S.*)?",
    r"type:\s*ignore(?:\[[\w, -]+\])?",
    r"pyright:\s*(?:ignore|basic|standard|strict)(?:\[[\w, ]+\])?",
    r"pragma:\s*no (?:cover|branch)",
    r"fmt:\s*(?:off|on|skip)",
    rf"{EXCLUSION_MARKER}\s*\S.*",
)
_ALLOWED_SEGMENT = re.compile(rf"^\s*(?:{'|'.join(_DIRECTIVES)})\s*$")


@dataclass(frozen=True)
class CommentViolation:
    path: Path
    line: int
    comment: str


def _is_allowed(comment: str, line: int) -> bool:
    if line == _SHEBANG_LINE and _SHEBANG.match(comment):
        return True
    if line <= _ENCODING_LINES and _ENCODING.match(comment):
        return True
    segments = comment.split("#")[1:]
    return all(_ALLOWED_SEGMENT.match(segment) for segment in segments)


def find_violations(source: str, path: Path) -> list[CommentViolation]:
    """Return every COMMENT token that is neither a tool directive nor a `keep: <reason>`."""
    tokens = tokenize.generate_tokens(io.StringIO(source).readline)
    return [
        CommentViolation(path, token.start[0], token.string)
        for token in tokens
        if token.type == tokenize.COMMENT and not _is_allowed(token.string, token.start[0])
    ]


def _python_files(roots: tuple[str, ...]) -> list[Path]:
    return sorted(
        path
        for root in roots
        for path in Path(root).rglob("*.py")
        if not IGNORED_DIRECTORIES.intersection(path.parts)
    )


def main() -> int:
    violations = [
        violation
        for path in _python_files(CHECKED_ROOTS)
        for violation in find_violations(path.read_text(encoding="utf-8"), path)
    ]
    for violation in violations:
        sys.stderr.write(f"{violation.path}:{violation.line}: {violation.comment}\n")
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
