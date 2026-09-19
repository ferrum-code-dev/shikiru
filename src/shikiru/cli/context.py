"""Project resolution and global CLI state."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from shikiru.config.loader import SHIKIRU_DIR_NAME
from shikiru.errors import NotInitialisedError

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


def project_root(state: CliState) -> Path:
    """Resolve and validate the initialized project selected by `state`."""
    root = state.project.resolve() if state.project is not None else nearest_project(Path.cwd())
    if not (root / SHIKIRU_DIR_NAME).is_dir():
        message = f"no {SHIKIRU_DIR_NAME}/ directory found from {root}"
        raise NotInitialisedError(message)
    return root


def nearest_project(start: Path) -> Path:
    """Find the nearest initialized project at or above `start`."""
    current = start.resolve()
    while True:
        if (current / SHIKIRU_DIR_NAME).is_dir():
            return current
        if current.parent == current:
            message = f"no {SHIKIRU_DIR_NAME}/ directory found from {start}"
            raise NotInitialisedError(message)
        current = current.parent
