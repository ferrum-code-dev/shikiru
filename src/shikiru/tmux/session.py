"""The Shikiru tmux session: name, collision check, options, home and task windows."""

from __future__ import annotations

import shlex
from typing import TYPE_CHECKING

from shikiru.domain.versions import parse_version
from shikiru.errors import ConfigError
from shikiru.palette import (
    BACKGROUND_COLOR,
    NEUTRAL_BORDER_COLOR,
    PRIMARY_TEXT_COLOR,
    SECONDARY_TEXT_COLOR,
    SELECTED_SURFACE_COLOR,
    STEEL_BLUE,
)
from shikiru.tmux.client import (
    FORMAT_PANE_ID,
    FORMAT_PANE_START_COMMAND,
    FORMAT_WINDOW_ID,
    FORMAT_WINDOW_INDEX,
    FORMAT_WINDOW_NAME,
    HOME_PANE_OPTION,
    HOME_PANE_OPTION_VALUE,
    exact_target,
)
from shikiru.tmux.decorator import (
    HOME_WINDOW_STATUS_CURRENT_FORMAT,
    HOME_WINDOW_STATUS_FORMAT,
    PANE_BORDER_FORMAT,
    WINDOW_STATE_COLOR_OPTION,
    WINDOW_STATUS_CURRENT_FORMAT,
    WINDOW_STATUS_FORMAT,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from shikiru.tmux.client import Tmux

SESSION_PREFIX = "[shikiru] "
_SANITIZED_CHARS = (".", ":")
_SANITIZED_REPLACEMENT = "_"

PROJECT_DIR_ENV_VAR = "SHIKIRU_PROJECT_DIR"
HOME_WINDOW_NAME = "dashboard"
LEGACY_HOME_WINDOW_NAME = "shikiru"
DETACHED_SIZE = (240, 64)
_HOME_WINDOW_INDEX = "0"
_HOME_INDEX_TARGET = f":{_HOME_WINDOW_INDEX}"
_TMUX_SERVER_VERSION_FORMAT = "#{version}"
_ALLOW_SET_TITLE_OPTION = "allow-set-title"
ALLOW_SET_TITLE_MINIMUM_VERSION = (3, 5)

_PALETTE_KEY = "Space"
_DASHBOARD_PALETTE_EVENT = "F12"
_TMUX_COMMAND = "tmux"
_GLOB_SPECIAL_CHARS = "\\[]*?"


def _escape_glob(text: str) -> str:
    return "".join(f"\\{char}" if char in _GLOB_SPECIAL_CHARS else char for char in text)


SHIKIRU_SESSION_PATTERN = f"#{{m:{_escape_glob(SESSION_PREFIX)}*,#{{session_name}}}}"
_DEFAULT_PALETTE_KEY_COMMAND = "next-layout"
_HOME_COMMAND_MARKER = "dash --home"
_HOME_WINDOW_NAMES = frozenset((HOME_WINDOW_NAME, LEGACY_HOME_WINDOW_NAME))
_HOME_ROW_MAX_SPLITS = 5
_HOME_WINDOW_NAME_FIELD = 2
_HOME_WINDOW_ACTIVE_FIELD = 3
_ACTIVE_WINDOW_VALUE = "1"
_FIELD_SEPARATOR = "\t"
_STATUS_CLOCK_FORMAT = (
    f"#[fg={SECONDARY_TEXT_COLOR},bg={BACKGROUND_COLOR}]%H:%M:%S %Y-%m-%d"
    f"#[fg={PRIMARY_TEXT_COLOR},bg={BACKGROUND_COLOR}]"
)
_HOME_PANE_FORMAT = (
    f"{FORMAT_WINDOW_ID}{_FIELD_SEPARATOR}{FORMAT_PANE_ID}{_FIELD_SEPARATOR}"
    f"{FORMAT_WINDOW_NAME}{_FIELD_SEPARATOR}#{{window_active}}{_FIELD_SEPARATOR}"
    f"{FORMAT_PANE_START_COMMAND}{_FIELD_SEPARATOR}"
    f"#{{{HOME_PANE_OPTION}}}"
)

_SESSION_SCOPED_OPTIONS: dict[str, str] = {
    "base-index": _HOME_WINDOW_INDEX,
    "renumber-windows": "off",
    "mouse": "on",
    "set-titles": "on",
    "status-style": f"fg={PRIMARY_TEXT_COLOR},bg={BACKGROUND_COLOR}",
    "status-left-style": f"fg={SECONDARY_TEXT_COLOR},bg={BACKGROUND_COLOR}",
    "status-right-style": f"fg={SECONDARY_TEXT_COLOR},bg={BACKGROUND_COLOR}",
    "status-left": "",
    "status-right": _STATUS_CLOCK_FORMAT,
    "status-interval": "1",
}

_OWNED_WINDOW_BEHAVIOR_OPTIONS: dict[str, str] = {
    "remain-on-exit": "on",
    "allow-rename": "off",
    "automatic-rename": "off",
    _ALLOW_SET_TITLE_OPTION: "off",
}

_PANE_TITLE_OPTIONS: dict[str, str] = {
    "pane-border-status": "top",
    "pane-border-lines": "heavy",
    "pane-border-style": f"fg={NEUTRAL_BORDER_COLOR},bg={BACKGROUND_COLOR}",
    "pane-active-border-style": f"fg={STEEL_BLUE},bg={BACKGROUND_COLOR}",
    "pane-border-format": PANE_BORDER_FORMAT,
}

_WINDOW_STATUS_OPTIONS: dict[str, str] = {
    "window-status-style": f"fg={SECONDARY_TEXT_COLOR},bg={BACKGROUND_COLOR}",
    "window-status-current-style": f"fg={PRIMARY_TEXT_COLOR},bg={SELECTED_SURFACE_COLOR},bold",
    "window-status-format": WINDOW_STATUS_FORMAT,
    "window-status-current-format": WINDOW_STATUS_CURRENT_FORMAT,
}

_TASK_WINDOW_OPTIONS: dict[str, str] = {
    **_OWNED_WINDOW_BEHAVIOR_OPTIONS,
    **_PANE_TITLE_OPTIONS,
    **_WINDOW_STATUS_OPTIONS,
}

_TASK_WINDOW_RECOVERY_OPTIONS: dict[str, str] = {
    **_OWNED_WINDOW_BEHAVIOR_OPTIONS,
    **_PANE_TITLE_OPTIONS,
}

_HOME_WINDOW_OPTIONS: dict[str, str] = {
    **_TASK_WINDOW_OPTIONS,
    "pane-border-status": "off",
    "window-status-format": HOME_WINDOW_STATUS_FORMAT,
    "window-status-current-format": HOME_WINDOW_STATUS_CURRENT_FORMAT,
}

_HOME_WINDOW_RECOVERY_OPTIONS: dict[str, str] = {
    **_TASK_WINDOW_RECOVERY_OPTIONS,
    "pane-border-status": "off",
    "window-status-format": HOME_WINDOW_STATUS_FORMAT,
    "window-status-current-format": HOME_WINDOW_STATUS_CURRENT_FORMAT,
}


async def bind_palette_key(tmux: Tmux, palette_command: Sequence[str]) -> None:
    """Bind prefix+Space to the mounted Home dashboard or the contextual popup.

    The Home dashboard receives a dedicated F12 terminal event. Other Shikiru panes retain the
    standalone palette popup and originating pane context. Elsewhere the key keeps tmux's own
    default, `next-layout`: there is no global override, only this one conditional
    binding.
    """
    popup_command = f"{_TMUX_COMMAND} display-popup -E " + " ".join(
        shlex.quote(part) for part in palette_command
    )
    expanded_popup_command = f"run-shell {shlex.quote(popup_command)}"
    dashboard_pane = "#{&&:#{m:*dash --home*,#{pane_start_command}},#{!=:#{pane_dead},1}}"
    dashboard_command = f"send-keys {_DASHBOARD_PALETTE_EVENT}"
    contextual_command = (
        f"if-shell -F {shlex.quote(dashboard_pane)} "
        f"{shlex.quote(dashboard_command)} {shlex.quote(expanded_popup_command)}"
    )
    await tmux.bind_key(
        _PALETTE_KEY,
        "if-shell",
        "-F",
        SHIKIRU_SESSION_PATTERN,
        contextual_command,
        _DEFAULT_PALETTE_KEY_COMMAND,
    )


def session_name(project_name: str) -> str:
    """Build the session name for `project_name`: `.` and `:` become `_`."""
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
        created_home_pane: str | None = None
        if await self._tmux.target_exists(target):
            await self._check_no_collision(target, project_dir)
        else:
            created_home_pane = await self._tmux.new_session(
                name,
                cwd=project_dir,
                env={PROJECT_DIR_ENV_VAR: str(project_dir)},
                window_name=HOME_WINDOW_NAME,
                size=DETACHED_SIZE,
                command=home_command,
            )
        for option, value in _SESSION_SCOPED_OPTIONS.items():
            await self._tmux.set_session_option(target, option, value)
        await self._tmux.set_session_option(target, "set-titles-string", project_name)
        home_window = await self._ensure_home_window(
            target, project_dir, home_command, initial_pane=created_home_pane
        )
        await self._apply_session_window_options(target, home_window)
        return target

    async def create_task_window(self, session_target: str, *, window_name: str, cwd: Path) -> str:
        """Create a task window in `session_target`, returning the id of its base pane."""
        base_pane = await self._tmux.new_window(
            session_target, name=window_name, cwd=cwd, detached=True
        )
        await self._apply_options(await self._tmux.window_id(base_pane), _TASK_WINDOW_OPTIONS)
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
        self,
        target: str,
        project_dir: Path,
        home_command: Sequence[str],
        *,
        initial_pane: str | None,
    ) -> str:
        if initial_pane is not None:
            window_target = await self._tmux.window_id(initial_pane)
            await self._mark_home_pane(initial_pane)
        else:
            matches = await home_window_targets(self._tmux, target)
            if not matches:
                pane_id = await self._tmux.new_window(
                    target,
                    name=HOME_WINDOW_NAME,
                    cwd=project_dir,
                    command=home_command,
                    detached=True,
                )
                await self._mark_home_pane(pane_id)
                matches = await home_window_targets(self._tmux, target)
            window_target, pane_id, _, _ = _preferred_home(matches)
            await self._mark_home_pane(pane_id)
        if await self._tmux.display(window_target, FORMAT_WINDOW_NAME) != HOME_WINDOW_NAME:
            await self._tmux.rename_window(window_target, HOME_WINDOW_NAME)
        await self._ensure_home_index(target, window_target)
        await self._remove_duplicate_home_windows(target, window_target)
        return window_target

    async def _apply_session_window_options(self, target: str, home_window: str) -> None:
        for window_target in await self._tmux.list_session_windows(target):
            await self._apply_options(window_target, _WINDOW_STATUS_OPTIONS)
            if window_target == home_window:
                await self._apply_options(window_target, _HOME_WINDOW_RECOVERY_OPTIONS)
            elif await self._is_task_window(window_target):
                await self._apply_options(window_target, _TASK_WINDOW_RECOVERY_OPTIONS)

    async def _is_task_window(self, window_target: str) -> bool:
        state_color_format = f"#{{{WINDOW_STATE_COLOR_OPTION}}}"
        return bool(await self._tmux.display(window_target, state_color_format))

    async def _ensure_home_index(self, target: str, home_window: str) -> None:
        if await self._tmux.display(home_window, FORMAT_WINDOW_INDEX) == _HOME_WINDOW_INDEX:
            return
        home_target = f"{target}{_HOME_INDEX_TARGET}"
        if await self._tmux.target_exists(home_target):
            occupant_window = await self._tmux.window_id(home_target)
            homes = await home_window_targets(self._tmux, target)
            if any(match[0] == occupant_window for match in homes):
                await self._tmux.swap_windows(home_window, home_target)
                return
            occupant = await self._tmux.display(home_target, FORMAT_WINDOW_NAME)
            message = (
                f"cannot move Home window to index 0; {home_target} is occupied by {occupant!r}"
            )
            raise ConfigError(message)
        await self._tmux.move_window(home_window, home_target)

    async def _mark_home_pane(self, pane_id: str) -> None:
        await self._tmux.set_pane_option(pane_id, HOME_PANE_OPTION, HOME_PANE_OPTION_VALUE)

    async def _remove_duplicate_home_windows(self, target: str, home_window: str) -> None:
        for duplicate, _, _, _ in await home_window_targets(self._tmux, target):
            if duplicate != home_window:
                await self._tmux.kill_window(duplicate)

    async def _apply_options(self, window_target: str, options: dict[str, str]) -> None:
        for option, value in options.items():
            if option == _ALLOW_SET_TITLE_OPTION:
                server_version_text = await self._tmux.display(
                    window_target, _TMUX_SERVER_VERSION_FORMAT
                )
                server_version = parse_version(server_version_text)
                if server_version is None:
                    message = (
                        f"tmux server returned an unrecognized version: {server_version_text!r}"
                    )
                    raise ConfigError(message)
                if server_version < ALLOW_SET_TITLE_MINIMUM_VERSION:
                    continue
            await self._tmux.set_window_option(window_target, option, value)


async def home_window_target(tmux: Tmux, session_target: str) -> tuple[str, str] | None:
    """Find Home by its owned pane marker or its shipped window name."""
    matches = await home_window_targets(tmux, session_target)
    if not matches:
        return None
    home = _preferred_home(matches)
    return home[0], home[1]


async def home_window_targets(tmux: Tmux, session_target: str) -> list[tuple[str, str, str, str]]:
    """Find owned Home windows, falling back to shipped names for legacy sessions."""
    panes = await tmux.list_session_panes(session_target, _HOME_PANE_FORMAT)
    identified: list[tuple[str, str, str, str]] = []
    named: list[tuple[str, str, str, str]] = []
    for row in panes:
        window_id, pane_id, window_name, active, start_command, marker = row.split(
            _FIELD_SEPARATOR, _HOME_ROW_MAX_SPLITS
        )
        if marker == HOME_PANE_OPTION_VALUE or _HOME_COMMAND_MARKER in start_command:
            identified.append((window_id, pane_id, window_name, active))
        elif window_name in _HOME_WINDOW_NAMES:
            named.append((window_id, pane_id, window_name, active))
    return identified or named


def _preferred_home(
    matches: list[tuple[str, str, str, str]],
) -> tuple[str, str, str, str]:
    return next(
        (match for match in matches if match[_HOME_WINDOW_ACTIVE_FIELD] == _ACTIVE_WINDOW_VALUE),
        next(
            (match for match in matches if match[_HOME_WINDOW_NAME_FIELD] == HOME_WINDOW_NAME),
            next(
                (
                    match
                    for match in matches
                    if match[_HOME_WINDOW_NAME_FIELD] == LEGACY_HOME_WINDOW_NAME
                ),
                matches[0],
            ),
        ),
    )
