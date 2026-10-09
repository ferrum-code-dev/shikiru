"""Decorations that never fail the engine path that sets them (06 §5.3)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from shikiru.errors import PROCESS_FAILURES
from shikiru.tmux.decorator import PaneDecorator

if TYPE_CHECKING:
    from collections.abc import Awaitable, Mapping

    from shikiru.tmux.client import Tmux

COSMETIC_LOGGER_NAME = __name__
_LOGGER = logging.getLogger(COSMETIC_LOGGER_NAME)


class CosmeticDecorator(PaneDecorator):
    """A `PaneDecorator` whose failing tmux calls, timeouts included, are logged instead of raised.

    A streak of failures of one operation is logged once, at its first failure, and ends with
    that operation's next success.

    A lost pane or server is repaired by the layout guard and must not end a task's flow or a
    command.

    A title template that cannot be rendered still raises `ConfigError`.
    """

    def __init__(self, tmux: Tmux) -> None:
        """Decorate through `tmux`."""
        super().__init__(tmux)
        self._failing: set[str] = set()

    async def _cosmetic(self, name: str, operation: Awaitable[None]) -> None:
        try:
            await operation
        except PROCESS_FAILURES:
            if name not in self._failing:
                _LOGGER.exception("tmux %s failed", name)
            self._failing.add(name)
        else:
            self._failing.discard(name)

    async def configure_formats(self, window_target: str) -> None:
        """Set the window formats, logging a tmux failure."""
        await self._cosmetic("configure_formats", super().configure_formats(window_target))

    async def set_pane_state(self, pane_target: str, *, color: str, glyph: str) -> None:
        """Set the pane state, logging a tmux failure."""
        await self._cosmetic(
            "set_pane_state", super().set_pane_state(pane_target, color=color, glyph=glyph)
        )

    async def set_window_state(self, window_target: str, *, color: str, glyph: str) -> None:
        """Set the window state, logging a tmux failure."""
        await self._cosmetic(
            "set_window_state", super().set_window_state(window_target, color=color, glyph=glyph)
        )

    async def set_pane_title(
        self, pane_target: str, template: str, fields: Mapping[str, object]
    ) -> None:
        """Set the pane title, logging a tmux failure."""
        await self._cosmetic(
            "set_pane_title", super().set_pane_title(pane_target, template, fields)
        )

    async def set_pane_title_styling(
        self,
        pane_target: str,
        template: str,
        fields: Mapping[str, object],
        *,
        field_colors: Mapping[str, str],
        field_segments: Mapping[str, tuple[tuple[str, str], ...]],
    ) -> None:
        """Set pane-title component colors, logging a tmux failure."""
        await self._cosmetic(
            "set_pane_title_styling",
            super().set_pane_title_styling(
                pane_target,
                template,
                fields,
                field_colors=field_colors,
                field_segments=field_segments,
            ),
        )

    async def set_window_title(
        self, window_target: str, template: str, fields: Mapping[str, object]
    ) -> None:
        """Set the window title, logging a tmux failure."""
        await self._cosmetic(
            "set_window_title", super().set_window_title(window_target, template, fields)
        )
