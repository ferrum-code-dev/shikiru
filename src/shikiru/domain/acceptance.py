"""Acceptance criteria a task request states under its acceptance-criteria heading."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

ACCEPTANCE_CRITERIA_HEADING: Final = "Acceptance criteria"

_PHRASE: Final = re.escape(ACCEPTANCE_CRITERIA_HEADING)
_HEADING: Final = re.compile(
    rf"(?:#{{1,6}}\s+)?(?:\*\*{_PHRASE}(?::\*\*|\*\*:?)|{_PHRASE}:?)\s*", re.IGNORECASE
)
_ANY_HEADING: Final = re.compile(r"\s*#{1,6}\s")
_FENCE: Final = re.compile(r"\s{0,3}(?P<marker>`{3,}|~{3,})")
_CRITERION: Final = re.compile(r"\s*(?:[-*+]|\d+[.)])\s+(?P<text>\S.*?)\s*")
_INDENTED: Final = re.compile(r"\s+\S.*")


@dataclass(frozen=True)
class _Section:
    start: int
    end: int
    criteria: tuple[str, ...]


def acceptance_criteria_from_request(request: str) -> tuple[str, ...]:
    """Return the bullets under the request's acceptance-criteria heading, in order.

    Fenced code is an example, not a requirement, so its lines are never read as a heading or
    a criterion. The section starts after the first line that is only the heading
    `ACCEPTANCE_CRITERIA_HEADING`, written with optional `#` marks, optional bold markers and an
    optional trailing colon, in any letter case. An indented line continues the bullet above it.
    The section ends at the first other non-blank line: prose, a heading or a code fence. A request
    without that heading has no criteria.
    """
    section = _section(request.splitlines())
    return () if section is None else section.criteria


def request_without_criteria_section(request: str) -> str:
    """Return the request without its acceptance-criteria section, keeping all other text.

    The removed section is the one `acceptance_criteria_from_request` reads: its heading line,
    its bullets and their continuations, and the blank lines among them. A request without
    criteria is returned unchanged.
    """
    lines = request.splitlines()
    section = _section(lines)
    if section is None:
        return request
    return "\n".join([*lines[: section.start], *lines[section.end :]]).strip()


def _section(lines: list[str]) -> _Section | None:
    visible = _blank_code_blocks(lines)
    start = next(
        (index for index, line in enumerate(visible) if _HEADING.fullmatch(line)),
        None,
    )
    if start is None:
        return None
    criteria: list[str] = []
    end = len(visible)
    for index in range(start + 1, len(visible)):
        line = visible[index]
        if not line.strip():
            continue
        match = _CRITERION.fullmatch(line)
        if match is not None:
            criteria.append(match["text"])
        elif criteria and _is_continuation(line):
            criteria[-1] = f"{criteria[-1]} {line.strip()}"
        else:
            end = index
            break
    if not criteria:
        return None
    return _Section(start=start, end=end, criteria=tuple(criteria))


def _blank_code_blocks(lines: list[str]) -> list[str]:
    visible: list[str] = []
    fence: str | None = None
    for line in lines:
        if fence is not None:
            visible.append("")
            if _closes_fence(line, fence):
                fence = None
            continue
        visible.append(line)
        opening = _FENCE.match(line)
        if opening is not None:
            fence = opening["marker"]
    return visible


def _closes_fence(line: str, fence: str) -> bool:
    stripped = line.strip()
    return len(stripped) >= len(fence) and stripped == fence[0] * len(stripped)


def _is_continuation(line: str) -> bool:
    return (
        _INDENTED.fullmatch(line) is not None
        and _ANY_HEADING.match(line) is None
        and _FENCE.match(line) is None
    )
