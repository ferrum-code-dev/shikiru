"""Stall and operator-prompt detection (docs/design/03-workflow.md §4.2, §9)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Collection
    from datetime import datetime
    from pathlib import Path

    from shikiru.config.settings import TimeoutsConfig


def is_stalled(
    last_activity: datetime, tool_running: bool, now: datetime, timeouts: TimeoutsConfig
) -> bool:
    """Decide whether `now` is past the timeout for the kind of activity last seen (03 §4.2).

    A running tool gets the longer `timeouts.tool` budget; anything else uses `timeouts.stall`.
    """
    threshold = timeouts.tool if tool_running else timeouts.stall
    return (now - last_activity).total_seconds() >= threshold


def is_operator_prompt(text: str, run_dir: Path, pending_lines: Collection[str]) -> bool:
    """Decide whether `text` was typed by the Operator, not injected by Shikiru (03 §9).

    Every pointer, nudge, `continue`, handoff and `say` injection references a path under the
    task's run dir; a line still awaiting its `before_turn` injection is excluded by name.
    """
    if str(run_dir) in text:
        return False
    return text not in pending_lines
