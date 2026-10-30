"""Acceptance criteria a task request states under its acceptance-criteria heading."""

from __future__ import annotations

import re
from typing import Final

ACCEPTANCE_CRITERIA_HEADING: Final = "Acceptance criteria"

_HEADING: Final = re.compile(
    rf"#{{1,6}}\s+{re.escape(ACCEPTANCE_CRITERIA_HEADING)}\s*", re.IGNORECASE
)
_CRITERION: Final = re.compile(r"\s*(?:[-*+]|\d+[.)])\s+(?P<text>\S.*?)\s*")


def acceptance_criteria_from_request(request: str) -> tuple[str, ...]:
    """Return the bullets under the request's acceptance-criteria heading, in order.

    The section starts after the first heading line reading `ACCEPTANCE_CRITERIA_HEADING` and
    ends at the first non-blank line that is not a bullet, so a following heading, prose or
    another list closes it. A request without that heading has no criteria.
    """
    lines = request.splitlines()
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
        if match is None:
            break
        criteria.append(match["text"])
    return tuple(criteria)
