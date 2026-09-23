"""The Shikiru tmux session: name, collision check, options, home and task windows (06 §5.1)."""

from __future__ import annotations

import shlex
from typing import TYPE_CHECKING

from shikiru.errors import ConfigError
from shikiru.tmux.client import exact_target

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from shikiru.tmux.client import Tmux

SESSION_PREFIX = "[shikiru] "
_SANITIZED_CHARS = (".", ":")
_SANITIZED_REPLACEMENT = "_"

PROJECT_DIR_ENV_VAR = "SHIKIRU_PROJECT_DIR"
HOME_WINDOW_NAME = "shikiru"
DETACHED_SIZE = (240, 64)

_PALETTE_KEY = "Space"
_GLOB_SPECIAL_CHARS = "\\[]*?"


def _escape_glob(text: str) -> str:
    return "".join(f"\\{char}" if char in _GLOB_SPECIAL_CHARS else char for char in text)


SHIKIRU_SESSION_PATTERN = f"#{{m:{_escape_glob(SESSION_PREFIX)}*,#{{session_name}}}}"
_DEFAULT_PALETTE_KEY_COMMAND = "next-layout"

_SESSION_SCOPED_OPTIONS: dict[str, str] = {
    "renumber-windows": "on",
    "mouse": "on",
}

_WINDOW_SCOPED_OPTIONS: dict[str, str] = {
    "remain-on-exit": "on",
    "allow-rename": "off",
    "automatic-rename": "off",
    "allow-set-title": "off",
    "pane-border-status": "top",
    "pane-border-lines": "heavy",
}


async def bind_palette_key(tmux: Tmux, palette_command: Sequence[str]) -> None:
    """Bind prefix+Space to open `palette_command` in a popup, only in `[shikiru]` sessions.

    Elsewhere the key keeps tmux's own default, `next-layout` (06 §5.2): there is no global
    override, only this one conditional binding.
    """
    popup_command = "display-popup -E " + " ".join(shlex.quote(part) for part in palette_command)
    await tmux.bind_key(
        _PALETTE_KEY,
        "if-shell",
        "-F",
        SHIKIRU_SESSION_PATTERN,
        popup_command,
        _DEFAULT_PALETTE_KEY_COMMAND,
    )


def session_name(project_name: str) -> str:
    """Build the session name for `project_name` (06 §5.1): `.` and `:` become `_`."""
    sanitized = project_name
    for char in _SANITIZED_CHARS:
        sanitized = sanitized.replace(char, _SANITIZED_REPLACEMENT)
    return f"{SESSION_PREFIX}{sanitized}"


class SessionView:
    """Ensures the project's tmux session, its options, and its home and task windows."""

    def __init__(self, tmux: Tmux) -> None:
        """Drive every session operation through `tmux`."""
        self._tmux = tmux

    async def ensure(
        self, project_name: str, project_dir: Path, *, home_command: Sequence[str]
    ) -> str:
        """Ensure the session for `project_name` exists with its options and home window.

        Returns the session's exact target. Idempotent: calling this twice creates nothing
        twice. Raises `ConfigError` if a session of that name already belongs to another
        project (a different `SHIKIRU_PROJECT_DIR`).
        """
        name = session_name(project_name)
        target = exact_target(name)
        if await self._tmux.target_exists(target):
            await self._check_no_collision(target, project_dir)
        else:
            await self._tmux.new_session(
                name,
                cwd=project_dir,
                env={PROJECT_DIR_ENV_VAR: str(project_dir)},
                window_name=HOME_WINDOW_NAME,
                size=DETACHED_SIZE,
                command=home_command,
            )
            await self._apply_window_options(f"{target}:{HOME_WINDOW_NAME}")
        for option, value in _SESSION_SCOPED_OPTIONS.items():
            await self._tmux.set_session_option(target, option, value)
        await self._ensure_home_window(target, project_dir, home_command)
        return target

    async def create_task_window(self, session_target: str, *, window_name: str, cwd: Path) -> str:
        """Create a task window in `session_target`, returning the id of its base pane."""
        window_target = f"{session_target}:{window_name}"
        base_pane = await self._tmux.new_window(session_target, name=window_name, cwd=cwd)
        await self._apply_window_options(window_target)
        return base_pane

    async def close_task_window(self, window_target: str) -> None:
        """Close a task window."""
        await self._tmux.kill_window(window_target)

    async def _check_no_collision(self, target: str, project_dir: Path) -> None:
        other = await self._tmux.show_session_environment(target, PROJECT_DIR_ENV_VAR)
        if other != str(project_dir):
            other_label = other if other is not None else "an unknown project"
            message = f"tmux session {target} already belongs to {other_label}, not {project_dir}"
            raise ConfigError(message)

    async def _ensure_home_window(
        self, target: str, project_dir: Path, home_command: Sequence[str]
    ) -> None:
        window_target = f"{target}:{HOME_WINDOW_NAME}"
        if await self._tmux.target_exists(window_target):
            return
        await self._tmux.new_window(
            target, name=HOME_WINDOW_NAME, cwd=project_dir, command=home_command
        )
        await self._apply_window_options(window_target)

    async def _apply_window_options(self, window_target: str) -> None:
        for option, value in _WINDOW_SCOPED_OPTIONS.items():
            await self._tmux.set_window_option(window_target, option, value)
