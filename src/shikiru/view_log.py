"""The role view log: its path, row keys and the pane command tailing it (05 §5, 06 §5.1)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.agents.contracts import role_dir, shikiru_argv

if TYPE_CHECKING:
    from pathlib import Path

VIEW_LOG_NAME = "view.jsonl"
VIEW_TIME_KEY = "ts"
VIEW_TURN_KEY = "turn"
VIEW_RUNG_KEY = "rung"
VIEW_SIGNAL_KEY = "signal"
CLI_VIEW_COMMAND = "view"
CLI_PROJECT_FLAG = "--project"


def view_argv(project_root: Path, task_id: str, role: str) -> tuple[str, ...]:
    """Return the argv of `role`'s read-only view pane for `task_id` (06 §5.1)."""
    return shikiru_argv(CLI_PROJECT_FLAG, str(project_root), CLI_VIEW_COMMAND, task_id, role)


def view_log_path(run_dir: Path, role: str) -> Path:
    """Return `role`'s live view log, one JSON row per agent signal (05 §5)."""
    return role_dir(run_dir, role) / VIEW_LOG_NAME
