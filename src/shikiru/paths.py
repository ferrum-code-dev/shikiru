"""Project-local paths defined by the runtime layout (02 §5)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

SHIKIRU_DIR_NAME = ".shikiru"
PROJECT_CONFIG_RELATIVE_PATH = f"{SHIKIRU_DIR_NAME}/config.toml"
PROMPT_OVERRIDES_RELATIVE_PATH = f"{SHIKIRU_DIR_NAME}/prompts"
STATE_DB_FILENAME = "state.db"
ENGINE_LOCK_FILENAME = "engine.lock"
ENGINE_LOG_DIR = "logs"
ENGINE_LOG_FILENAME = "engine.log"
WORKTREES_DIR_NAME = "worktrees"
RUNS_DIR_NAME = "runs"
TURNS_DIR_NAME = "turns"
MESSAGES_DIR_NAME = "messages"
EXCLUDE_LINE = f"/{SHIKIRU_DIR_NAME}/"


def project_state_dir(project_root: Path) -> Path:
    """Return the project's `.shikiru/` state directory."""
    return project_root / SHIKIRU_DIR_NAME


def project_config_path(project_root: Path) -> Path:
    """Return the project's settings file path."""
    return project_root / PROJECT_CONFIG_RELATIVE_PATH


def state_db_path(project_root: Path) -> Path:
    """Return the project's SQLite state database path."""
    return project_state_dir(project_root) / STATE_DB_FILENAME


def engine_lock_path(project_root: Path) -> Path:
    """Return the project's engine liveness lock path."""
    return project_state_dir(project_root) / ENGINE_LOCK_FILENAME


def engine_log_path(project_root: Path) -> Path:
    """Return the project's engine log path."""
    return project_state_dir(project_root) / ENGINE_LOG_DIR / ENGINE_LOG_FILENAME


def run_dir(project_root: Path, task_id: str) -> Path:
    """Return `task_id`'s `.shikiru/runs/` directory."""
    return project_state_dir(project_root) / RUNS_DIR_NAME / task_id


def turns_dir(project_root: Path, task_id: str) -> Path:
    """Return `task_id`'s turn artifacts directory."""
    return run_dir(project_root, task_id) / TURNS_DIR_NAME


def task_root(project_root: Path, task_id: str) -> Path:
    """Return `task_id`'s `.shikiru/worktrees/` directory."""
    return project_state_dir(project_root) / WORKTREES_DIR_NAME / task_id


def queued_messages_dir(project_root: Path, task_id: str, role: str) -> Path:
    """Return the directory holding `role`'s queued operator messages for `task_id`."""
    return run_dir(project_root, task_id) / MESSAGES_DIR_NAME / role
