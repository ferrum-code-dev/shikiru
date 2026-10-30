"""Acceptance criteria a task request states under its acceptance-criteria heading."""

from __future__ import annotations

import re
from typing import Final

ACCEPTANCE_CRITERIA_HEADING: Final = "Acceptance criteria"

_HEADING: Final = re.compile(
    rf"#{{1,6}}\s+{re.escape(ACCEPTANCE_CRITERIA_HEADING)}\s*", re.IGNORECASE
)
_ANY_HEADING: Final = re.compile(r"\s*#{1,6}\s")
_FENCE: Final = re.compile(r"\s{0,3}(?P<marker>`{3,}|~{3,})")
_CRITERION: Final = re.compile(r"\s*(?:[-*+]|\d+[.)])\s+(?P<text>\S.*?)\s*")
_INDENTED: Final = re.compile(r"\s+\S.*")


def acceptance_criteria_from_request(request: str) -> tuple[str, ...]:
    """Return the bullets under the request's acceptance-criteria heading, in order.

    Fenced code is an example, not a requirement, so its lines are never read as a heading or
    a criterion. The section starts after the first heading line reading
    `ACCEPTANCE_CRITERIA_HEADING`. An indented line continues the bullet above it. The section
    ends at the first other non-blank line: prose, a heading or a code fence. A request without
    that heading has no criteria.
    """
    lines = _outside_code_blocks(request.splitlines())
    start = next(
        (index + 1 for index, line in enumerate(lines) if _HEADING.fullmatch(line)),
        None,
    )
    if start is None:
        return ()
    criteria: list[str] = []
    for line in lines[start:]:
        if not line.strip():
            continue
        match = _CRITERION.fullmatch(line)
        if match is not None:
            criteria.append(match["text"])
        elif criteria and _is_continuation(line):
            criteria[-1] = f"{criteria[-1]} {line.strip()}"
        else:
            break
    return tuple(criteria)


def _outside_code_blocks(lines: list[str]) -> list[str]:
    kept: list[str] = []
    fence: str | None = None
    for line in lines:
        if fence is not None:
            if _closes_fence(line, fence):
                fence = None
            continue
        kept.append(line)
        opening = _FENCE.match(line)
        if opening is not None:
            fence = opening["marker"]
    return kept


def _closes_fence(line: str, fence: str) -> bool:
    stripped = line.strip()
    return len(stripped) >= len(fence) and stripped == fence[0] * len(stripped)


def _is_continuation(line: str) -> bool:
    return (
        _INDENTED.fullmatch(line) is not None
        and _ANY_HEADING.match(line) is None
        and _FENCE.match(line) is None
    )
