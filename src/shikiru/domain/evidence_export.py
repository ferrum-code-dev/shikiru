"""Versioned Markdown and JSON rendering for a task evidence export."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from shikiru.domain.token_usage import PROCESSED_TOKENS_NOTE

if TYPE_CHECKING:
    from collections.abc import Mapping

EVIDENCE_EXPORT_FORMAT = "shikiru.task-evidence"
EVIDENCE_EXPORT_VERSION = 3
JSON_INDENT = 2
MINIMUM_MARKDOWN_FENCE = 3
MARKDOWN_NEWLINE = "\n"
MARKDOWN_FENCE_CHARACTER = "`"


def render_evidence_json(document: Mapping[str, object]) -> str:
    """Render a JSON-safe task evidence document with stable, readable indentation."""
    return f"{json.dumps(document, ensure_ascii=False, indent=JSON_INDENT, allow_nan=False)}\n"


def render_evidence_markdown(document: Mapping[str, object]) -> str:
    """Render an evidence document while keeping recorded text inside safe code fences."""
    lines = [
        "# Shikiru task evidence export",
        "",
        "## Export metadata",
        _code_block(document.get("metadata")),
        "",
        "## Request",
        _code_block(document.get("request")),
        "",
        "## Recorded outcome",
        _code_block(document.get("outcome")),
        "",
        "## Task and rung metadata",
        _code_block(document.get("task")),
        "",
        "## Relationships",
        _code_block(document.get("relationships")),
        "",
        "## Validated reports and verdicts",
        _code_block(document.get("reports")),
        "",
        "## Recorded git facts",
        _code_block(document.get("facts")),
        "",
        "## Recorded timeline",
        _code_block(document.get("timeline")),
        "",
        "## Recorded sessions",
        _code_block(document.get("sessions")),
        "",
        "## Task usage summary",
        _code_block(document.get("usage")),
        PROCESSED_TOKENS_NOTE,
        "",
        "## Unavailable sections",
        _code_block(document.get("unavailable_sections")),
        "",
        (
            "This export contains retained Shikiru evidence. It is not a complete agent transcript "
            "and does not recreate a removed worktree."
        ),
        "",
    ]
    return MARKDOWN_NEWLINE.join(lines)


def _code_block(value: object) -> str:
    content = json.dumps(value, ensure_ascii=False, indent=JSON_INDENT, allow_nan=False)
    fence = MARKDOWN_FENCE_CHARACTER * max(
        MINIMUM_MARKDOWN_FENCE,
        _longest_backtick_run(content) + 1,
    )
    return f"{fence}json\n{content}\n{fence}"


def _longest_backtick_run(value: str) -> int:
    longest = 0
    current = 0
    for character in value:
        if character == MARKDOWN_FENCE_CHARACTER:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest
