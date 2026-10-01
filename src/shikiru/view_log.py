"""The role view log: its path, row keys and the pane command tailing it (05 §5, 06 §5.1)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.agents.contracts import (
    SessionStarted,
    ToolFinished,
    ToolStarted,
    TurnEnded,
    TurnFailed,
    role_dir,
    shikiru_argv,
)

if TYPE_CHECKING:
    from pathlib import Path

VIEW_LOG_NAME = "view.jsonl"
VIEW_TIME_KEY = "ts"
VIEW_TURN_KEY = "turn"
VIEW_RUNG_KEY = "rung"
VIEW_SIGNAL_KEY = "signal"
VIEW_SESSION_STARTED_SIGNAL = SessionStarted.__name__
VIEW_TOOL_STARTED_SIGNAL = ToolStarted.__name__
VIEW_TOOL_FINISHED_SIGNAL = ToolFinished.__name__
VIEW_TURN_ENDED_SIGNAL = TurnEnded.__name__
VIEW_TURN_FAILED_SIGNAL = TurnFailed.__name__
TURN_DIR_SEPARATOR = "-"
TURN_DIR_ITERATION_MARKER = "-i"
CLI_VIEW_COMMAND = "view"
CLI_PROJECT_FLAG = "--project"


def ordered_turn_dirs(turns_root: Path) -> list[Path]:
    """Return the turn directories of `turns_root` ordered by turn number, however many digits."""
    return sorted(
        (entry for entry in turns_root.iterdir() if entry.is_dir()),
        key=lambda entry: int(entry.name.partition(TURN_DIR_SEPARATOR)[0]),
    )


def view_argv(project_root: Path, task_id: str, role: str) -> tuple[str, ...]:
    """Return the argv of `role`'s read-only view pane for `task_id` (06 §5.1)."""
    return shikiru_argv(CLI_PROJECT_FLAG, str(project_root), CLI_VIEW_COMMAND, task_id, role)


def view_log_path(run_dir: Path, role: str) -> Path:
    """Return `role`'s live view log, one JSON row per agent signal (05 §5)."""
    return role_dir(run_dir, role) / VIEW_LOG_NAME
