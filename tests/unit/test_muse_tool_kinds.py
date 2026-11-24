"""Muse tool classification into timeline kinds."""

from __future__ import annotations

from shikiru.agents.contracts import ToolKind
from shikiru.plugins.agents.muse.turn import tool_kind

TOOL_KIND_ROWS = (
    ("bash", ToolKind.RAN),
    ("bash_input", ToolKind.RAN),
    ("read_file", ToolKind.READ),
    ("read_skill", ToolKind.READ),
    ("edit_file", ToolKind.EDITED),
    ("write_file", ToolKind.EDITED),
    ("search", ToolKind.SEARCHED),
    ("WebSearch", ToolKind.SEARCHED),
    ("web_search", ToolKind.SEARCHED),
    ("WebFetch", ToolKind.READ),
    ("web_fetch", ToolKind.READ),
)

UNKNOWN_TOOL = "write_todos"


def test_muse_tools_classify_into_timeline_kinds() -> None:
    for name, expected in TOOL_KIND_ROWS:
        assert tool_kind(name) == expected, name


def test_unknown_muse_tool_has_no_kind() -> None:
    assert tool_kind(UNKNOWN_TOOL) is None
