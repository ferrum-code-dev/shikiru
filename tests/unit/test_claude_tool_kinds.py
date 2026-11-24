"""Claude tool classification into timeline kinds."""

from __future__ import annotations

from shikiru.agents.contracts import ToolKind
from shikiru.plugins.agents.claude.turn import tool_kind

TOOL_KIND_ROWS = (
    ("Bash", ToolKind.RAN),
    ("Edit", ToolKind.EDITED),
    ("MultiEdit", ToolKind.EDITED),
    ("Write", ToolKind.EDITED),
    ("NotebookEdit", ToolKind.EDITED),
    ("Read", ToolKind.READ),
    ("Grep", ToolKind.SEARCHED),
    ("Glob", ToolKind.SEARCHED),
    ("WebSearch", ToolKind.SEARCHED),
    ("WebFetch", ToolKind.READ),
)

UNKNOWN_TOOL = "shikiru report"


def test_claude_tools_classify_into_timeline_kinds() -> None:
    for name, expected in TOOL_KIND_ROWS:
        assert tool_kind(name) == expected, name


def test_unknown_claude_tool_has_no_kind() -> None:
    assert tool_kind(UNKNOWN_TOOL) is None
