"""The role view log: its path, row keys and the pane command tailing it."""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import TYPE_CHECKING

from shikiru.agents.contracts import (
    Message,
    SessionStarted,
    ToolFinished,
    ToolStarted,
    TurnEnded,
    TurnFailed,
    role_dir,
    shikiru_argv,
)
from shikiru.domain.quota_waits import QuotaAction
from shikiru.private import TEXT_ENCODING, make_private_dir, write_private_text

if TYPE_CHECKING:
    from pathlib import Path

VIEW_LOG_NAME = "view.jsonl"
VIEW_TIME_KEY = "ts"
VIEW_TURN_KEY = "turn"
VIEW_RUNG_KEY = "rung"
VIEW_SIGNAL_KEY = "signal"
VIEW_MESSAGE_SIGNAL = Message.__name__
VIEW_MESSAGE_TEXT_KEY = "text"
VIEW_TURN_ENDED_TEXT_KEY = "last_message"
VIEW_SESSION_STARTED_SIGNAL = SessionStarted.__name__
VIEW_TOOL_STARTED_SIGNAL = ToolStarted.__name__
VIEW_TOOL_FINISHED_SIGNAL = ToolFinished.__name__
VIEW_TURN_ENDED_SIGNAL = TurnEnded.__name__
VIEW_TURN_FAILED_SIGNAL = TurnFailed.__name__
QUOTA_STATUS_NAME = "quota-status.json"
QUOTA_STATUS_ACTION_KEY = "action"
QUOTA_STATUS_AT_KEY = "at"
TURN_DIR_SEPARATOR = "-"
TURN_DIR_ITERATION_MARKER = "-i"
CLI_VIEW_COMMAND = "view"
CLI_PROJECT_FLAG = "--project"
PARTIAL_SUFFIX = ".partial"
_TAIL_BLOCK_BYTES = 65536
_LINE_BREAK = b"\n"


def ordered_turn_dirs(turns_root: Path) -> list[Path]:
    """Return the turn directories of `turns_root` ordered by turn number, however many digits."""
    return sorted(
        (entry for entry in turns_root.iterdir() if entry.is_dir()),
        key=lambda entry: int(entry.name.partition(TURN_DIR_SEPARATOR)[0]),
    )


def turn_iteration(turn_name: str) -> int | None:
    """Return the task iteration a turn directory name (`NN-<step>-i<n>`) was started in."""
    _, marker, iteration = turn_name.rpartition(TURN_DIR_ITERATION_MARKER)
    return int(iteration) if marker and iteration.isdecimal() else None


def view_argv(project_root: Path, task_id: str, role: str) -> tuple[str, ...]:
    """Return the argv of `role`'s read-only view pane for `task_id`."""
    return shikiru_argv(CLI_PROJECT_FLAG, str(project_root), CLI_VIEW_COMMAND, task_id, role)


def view_log_path(run_dir: Path, role: str) -> Path:
    """Return `role`'s live view log, one JSON row per agent signal."""
    return role_dir(run_dir, role) / VIEW_LOG_NAME


def quota_status_path(run_dir: Path, role: str) -> Path:
    """Return the file holding `role`'s current quota status, present only during a quota wait."""
    return role_dir(run_dir, role) / QUOTA_STATUS_NAME


def write_quota_status(path: Path, action: QuotaAction | None, at: datetime | None) -> None:
    """Publish a quota wait for `path`'s view pane, replacing any earlier one in a single step."""
    payload = {
        QUOTA_STATUS_ACTION_KEY: action.value if action is not None else None,
        QUOTA_STATUS_AT_KEY: at.isoformat() if at is not None else None,
    }
    make_private_dir(path.parent)
    partial = path.with_name(f"{path.name}{PARTIAL_SUFFIX}")
    write_private_text(partial, json.dumps(payload))
    partial.replace(path)


def clear_quota_status(path: Path) -> None:
    """Remove the quota status of `path`'s view pane; nothing happens when none is published."""
    path.unlink(missing_ok=True)


def read_quota_status(path: Path) -> tuple[QuotaAction | None, datetime | None] | None:
    """Return the published quota status as its action and time, or none outside a quota wait."""
    try:
        text = path.read_text(encoding=TEXT_ENCODING)
    except FileNotFoundError:
        return None
    payload = json.loads(text)
    action = payload[QUOTA_STATUS_ACTION_KEY]
    at = payload[QUOTA_STATUS_AT_KEY]
    return (
        QuotaAction(action) if action is not None else None,
        datetime.fromisoformat(at) if at is not None else None,
    )


def read_tail_lines(path: Path, count: int) -> list[str]:
    """Return the last `count` lines of `path`, reading no more of the file than they need."""
    blocks: list[bytes] = []
    line_breaks = 0
    with path.open("rb") as handle:
        position = handle.seek(0, os.SEEK_END)
        while position > 0 and line_breaks <= count:
            size = min(_TAIL_BLOCK_BYTES, position)
            position -= size
            handle.seek(position)
            block = handle.read(size)
            blocks.append(block)
            line_breaks += block.count(_LINE_BREAK)
    text = b"".join(reversed(blocks)).decode(TEXT_ENCODING, errors="replace")
    return text.splitlines()[-count:]
