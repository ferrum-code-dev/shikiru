"""Project resolution and global CLI state."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from shikiru.bootstrap import build_session_tools
from shikiru.errors import ConfigError, NotInitialisedError, UsageError
from shikiru.paths import SHIKIRU_DIR_NAME, project_config_path, project_state_dir

if TYPE_CHECKING:
    import typer

CURRENT_DIRECTORY = "."
PARENT_DIRECTORY = ".."
HOME_DIRECTORY = "~"


@dataclass(frozen=True)
class CliState:
    """Global options carried from Typer's callback to one command."""

    project: str | None
    json_output: bool
    overrides: tuple[str, ...]
    host: str | None = None


def explicit_project_path(value: str) -> Path | None:
    """Return `--project` value as a path when it cannot be a project name."""
    first_component = value.split(os.sep, 1)[0]
    if (
        not value
        or Path(value).is_absolute()
        or value.startswith(HOME_DIRECTORY)
        or first_component in {CURRENT_DIRECTORY, PARENT_DIRECTORY}
    ):
        return Path(value).resolve()
    return None


def select_project_dir(value: str, overrides: tuple[str, ...]) -> Path:
    """Resolve a `--project` value to a project directory without requiring it to be initialized.

    An explicit path is used as is. Any other value names a live project when one carries that
    exact configured name; otherwise it is a directory, initialized or not. A value that names a
    live project and also an initialized directory of a different project is rejected.
    """
    explicit = explicit_project_path(value)
    if explicit is not None:
        return explicit
    candidate = Path(value).resolve()
    lister, _attacher = build_session_tools(overrides)
    try:
        live = asyncio.run(lister.resolve_project(value))
    except ConfigError as error:
        message = f"{error}; select a directory with an explicit path such as ./{value}"
        raise ConfigError(message) from error
    initialized = project_state_dir(candidate).is_dir()
    if live is not None:
        if initialized and live.resolve() != candidate:
            message = (
                f"project {value!r} matches both the path {candidate} and the project at "
                f"{live}; give the absolute --project PATH of the intended project"
            )
            raise UsageError(message)
        return live
    if initialized or candidate.is_dir():
        return candidate
    message = f"no Shikiru project named {value!r} and no directory at {candidate}"
    raise UsageError(message)


def selected_project_dir(state: CliState) -> Path | None:
    """Return the directory `--project` selects, or `None` when the option was not given."""
    if state.project is None:
        return None
    return select_project_dir(state.project, state.overrides)


def state_from(context: typer.Context) -> CliState:
    """Return the callback state stored in `context`."""
    state = context.obj
    if not isinstance(state, CliState):
        message = "CLI context is not initialized"
        raise TypeError(message)
    return state


def project_root(state: CliState, *, require_config: bool = True) -> Path:
    """Resolve the selected project, optionally requiring its config file."""
    selected = selected_project_dir(state)
    root = selected if selected is not None else nearest_project(Path.cwd())
    state_dir = project_state_dir(root)
    if not state_dir.is_dir():
        message = f"no {SHIKIRU_DIR_NAME}/ directory found from {root}"
        raise NotInitialisedError(message, project_root=root, missing_path=state_dir)
    config_path = project_config_path(root)
    if require_config and not config_path.is_file():
        message = (
            f"{config_path} is missing; initialize the repository before running project commands"
        )
        raise NotInitialisedError(message, project_root=root, missing_path=config_path)
    return root


def nearest_project(start: Path) -> Path:
    """Find the nearest initialized project at or above `start`."""
    current = start.resolve()
    while True:
        if project_state_dir(current).is_dir():
            return current
        if current.parent == current:
            message = f"no {SHIKIRU_DIR_NAME}/ directory found from {start}"
            raise NotInitialisedError(
                message,
                project_root=start.resolve(),
                missing_path=project_state_dir(start),
            )
        current = current.parent
