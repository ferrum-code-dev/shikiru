"""Recreates the project's tmux session after the tmux server or session is lost."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence
    from logging import Logger
    from pathlib import Path

    from shikiru.tmux.client import Tmux
    from shikiru.tmux.session import SessionView


class SessionGuard:
    """Brings back the session and its home window when they have vanished."""

    def __init__(
        self,
        *,
        tmux: Tmux,
        view: SessionView,
        session_target: str,
        project_name: str,
        project_root: Path,
        home_command: Sequence[str],
        logger: Logger,
    ) -> None:
        """Probe `session_target` through `tmux` and recreate it through `view`."""
        self._tmux = tmux
        self._view = view
        self._session_target = session_target
        self._project_name = project_name
        self._project_root = project_root
        self._home_command = home_command
        self._logger = logger

    async def ensure(self) -> None:
        """Recreate the session with its home window when it does not exist."""
        if await self._tmux.target_exists(self._session_target):
            return
        self._logger.warning("tmux session %s is gone; recreating it", self._session_target)
        await self._view.ensure(
            self._project_name, self._project_root, home_command=self._home_command
        )
