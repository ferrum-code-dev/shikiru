"""Codex tool classification into timeline kinds."""

from __future__ import annotations

from shikiru.agents.contracts import ToolKind
from shikiru.plugins.agents.codex.turn import tool_kind

TOOL_KIND_ROWS = (
    ("command", ToolKind.RAN),
    ("file_change", ToolKind.EDITED),
    ("web_search", ToolKind.SEARCHED),
)

UNKNOWN_TOOL = "mcp_tool"


def test_codex_tools_classify_into_timeline_kinds() -> None:
    for name, expected in TOOL_KIND_ROWS:
        assert tool_kind(name) == expected, name


def test_unknown_codex_tool_has_no_kind() -> None:
    assert tool_kind(UNKNOWN_TOOL) is None
