"""Markdown code fencing that survives backticks already present in its content."""

from __future__ import annotations

_BACKTICK = "`"
_MIN_FENCE_LENGTH = 3


def fence(content: str, info: str = "") -> str:
    """Wrap `content` in a backtick fence longer than any backtick run already inside it.

    `info` is the fence's info string (e.g. `text` or `json`). Quoting untrusted content
    (an agent's report, a request or a pane screen) this way keeps a `` ``` `` or `` ```` ``
    the content already contains from closing the fence early and turning the rest of it
    into instructions the agent reads as ours.
    """
    length = max(_MIN_FENCE_LENGTH, _longest_backtick_run(content) + 1)
    marker = _BACKTICK * length
    return f"{marker}{info}\n{content}\n{marker}"


def _longest_backtick_run(content: str) -> int:
    longest = 0
    current = 0
    for character in content:
        if character == _BACKTICK:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest
