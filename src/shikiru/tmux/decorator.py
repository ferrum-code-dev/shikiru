"""Pane and window decorations and titles (06 §5.3, §6.1)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.errors import ConfigError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from shikiru.tmux.client import Tmux

PANE_COLOR_OPTION = "@shk_color"
PANE_GLYPH_OPTION = "@shk_glyph"
WINDOW_STATE_COLOR_OPTION = "@shk_state_color"
WINDOW_STATE_GLYPH_OPTION = "@shk_state_glyph"

PANE_BORDER_FORMAT = f" #[fg=#{{{PANE_COLOR_OPTION}}}]#{{{PANE_GLYPH_OPTION}}} #{{pane_title}} "
WINDOW_STATUS_FORMAT = (
    f" #[fg=#{{{WINDOW_STATE_COLOR_OPTION}}}]#{{{WINDOW_STATE_GLYPH_OPTION}}} #{{window_name}} "
)
WINDOW_STATUS_CURRENT_FORMAT = (
    f"[#[fg=#{{{WINDOW_STATE_COLOR_OPTION}}}]#{{{WINDOW_STATE_GLYPH_OPTION}}} #{{window_name}}]"
)

_PANE_BORDER_FORMAT_OPTION = "pane-border-format"
_WINDOW_STATUS_FORMAT_OPTION = "window-status-format"
_WINDOW_STATUS_CURRENT_FORMAT_OPTION = "window-status-current-format"


def render_title(template: str, fields: Mapping[str, object]) -> str:
    """Render a `dashboard.titles` template, failing loudly on any field it can't fill."""
    try:
        return template.format(**fields)
    except KeyError as error:
        message = f"title template {template!r} references unknown field {error.args[0]!r}"
        raise ConfigError(message) from error


class PaneDecorator:
    """Sets the `@shk_*` decorations, their formats, and renders pane and window titles."""

    def __init__(self, tmux: Tmux) -> None:
        """Decorate through `tmux`."""
        self._tmux = tmux

    async def configure_formats(self, window_target: str) -> None:
        """Set the border and window-status formats that read the `@shk_*` options."""
        await self._tmux.set_window_option(
            window_target, _PANE_BORDER_FORMAT_OPTION, PANE_BORDER_FORMAT
        )
        await self._tmux.set_window_option(
            window_target, _WINDOW_STATUS_FORMAT_OPTION, WINDOW_STATUS_FORMAT
        )
        await self._tmux.set_window_option(
            window_target, _WINDOW_STATUS_CURRENT_FORMAT_OPTION, WINDOW_STATUS_CURRENT_FORMAT
        )

    async def set_pane_state(self, pane_target: str, *, color: str, glyph: str) -> None:
        """Set pane `pane_target`'s `@shk_color` and `@shk_glyph`."""
        await self._tmux.set_pane_option(pane_target, PANE_COLOR_OPTION, color)
        await self._tmux.set_pane_option(pane_target, PANE_GLYPH_OPTION, glyph)

    async def set_pane_option(self, pane_target: str, name: str, value: str) -> None:
        """Set pane option `name` on `pane_target`."""
        await self._tmux.set_pane_option(pane_target, name, value)

    async def set_window_state(self, window_target: str, *, color: str, glyph: str) -> None:
        """Set window `window_target`'s `@shk_state_color` and `@shk_state_glyph`."""
        await self._tmux.set_window_option(window_target, WINDOW_STATE_COLOR_OPTION, color)
        await self._tmux.set_window_option(window_target, WINDOW_STATE_GLYPH_OPTION, glyph)

    async def set_pane_title(
        self, pane_target: str, template: str, fields: Mapping[str, object]
    ) -> None:
        """Render `template` with `fields` and set it as pane `pane_target`'s title."""
        await self._tmux.set_pane_title(pane_target, render_title(template, fields))

    async def set_window_title(
        self, window_target: str, template: str, fields: Mapping[str, object]
    ) -> None:
        """Render `template` with `fields` and rename window `window_target` to it."""
        await self._tmux.rename_window(window_target, render_title(template, fields))
