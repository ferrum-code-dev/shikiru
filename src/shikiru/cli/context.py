"""Project resolution and global CLI state."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from shikiru.errors import NotInitialisedError
from shikiru.paths import SHIKIRU_DIR_NAME, project_config_path, project_state_dir

if TYPE_CHECKING:
    import typer


@dataclass(frozen=True)
class CliState:
    """Global options carried from Typer's callback to one command."""

    project: Path | None
    json_output: bool
    overrides: tuple[str, ...]


def state_from(context: typer.Context) -> CliState:
    """Return the callback state stored in `context`."""
    state = context.obj
    if not isinstance(state, CliState):
        message = "CLI context is not initialized"
        raise TypeError(message)
    return state


def project_root(state: CliState, *, require_config: bool = True) -> Path:
    """Resolve the selected project, optionally requiring its config file."""
    root = state.project.resolve() if state.project is not None else nearest_project(Path.cwd())
    state_dir = project_state_dir(root)
    if not state_dir.is_dir():
        message = f"no {SHIKIRU_DIR_NAME}/ directory found from {root}"
        raise NotInitialisedError(message)
    config_path = project_config_path(root)
    if require_config and not config_path.is_file():
        message = (
            f"{config_path} is missing; initialize the repository before running project commands"
        )
        raise NotInitialisedError(message)
    return root


def nearest_project(start: Path) -> Path:
    """Find the nearest initialized project at or above `start`."""
    current = start.resolve()
    while True:
        if project_state_dir(current).is_dir():
            return current
        if current.parent == current:
            message = f"no {SHIKIRU_DIR_NAME}/ directory found from {start}"
            raise NotInitialisedError(message)
        current = current.parent
