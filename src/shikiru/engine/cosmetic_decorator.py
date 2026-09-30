"""Decorations that never fail the engine path that sets them (06 §5.3)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from shikiru.errors import CommandFailed
from shikiru.tmux.decorator import PaneDecorator

if TYPE_CHECKING:
    from collections.abc import Awaitable, Mapping

_LOGGER = logging.getLogger(__name__)


async def _cosmetic(operation: Awaitable[None]) -> None:
    try:
        await operation
    except CommandFailed:
        _LOGGER.exception("tmux decoration failed")


class CosmeticDecorator(PaneDecorator):
    """A `PaneDecorator` whose failing tmux calls are logged instead of raised.

    A lost pane or server is repaired by the layout guard and must not end a task's flow or a
    command.

    A title template that cannot be rendered still raises `ConfigError`.
    """

    async def set_pane_state(self, pane_target: str, *, color: str, glyph: str) -> None:
        """Set the pane state, logging a tmux failure."""
        await _cosmetic(super().set_pane_state(pane_target, color=color, glyph=glyph))

    async def set_window_state(self, window_target: str, *, color: str, glyph: str) -> None:
        """Set the window state, logging a tmux failure."""
        await _cosmetic(super().set_window_state(window_target, color=color, glyph=glyph))

    async def set_pane_title(
        self, pane_target: str, template: str, fields: Mapping[str, object]
    ) -> None:
        """Set the pane title, logging a tmux failure."""
        await _cosmetic(super().set_pane_title(pane_target, template, fields))

    async def set_window_title(
        self, window_target: str, template: str, fields: Mapping[str, object]
    ) -> None:
        """Set the window title, logging a tmux failure."""
        await _cosmetic(super().set_window_title(window_target, template, fields))
