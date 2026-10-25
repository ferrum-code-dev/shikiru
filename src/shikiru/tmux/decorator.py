"""Pane and window decorations and titles."""

from __future__ import annotations

from string import Formatter
from typing import TYPE_CHECKING

from shikiru.errors import ConfigError
from shikiru.palette import (
    BACKGROUND_COLOR,
    PRIMARY_TEXT_COLOR,
    SECONDARY_TEXT_COLOR,
    SELECTED_SURFACE_COLOR,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from shikiru.tmux.client import Tmux

PANE_COLOR_OPTION = "@shk_color"
PANE_GLYPH_OPTION = "@shk_glyph"
WINDOW_STATE_COLOR_OPTION = "@shk_state_color"
WINDOW_STATE_GLYPH_OPTION = "@shk_state_glyph"
PANE_TITLE_FORMAT_OPTION = "@shk_title_format"

PANE_BORDER_FORMAT = (
    f"#[fg={PRIMARY_TEXT_COLOR},bg={BACKGROUND_COLOR}] "
    f"#{{?{PANE_TITLE_FORMAT_OPTION},#{{{PANE_TITLE_FORMAT_OPTION}}},#{{pane_title}}}}"
    f" #[fg={PRIMARY_TEXT_COLOR},bg={BACKGROUND_COLOR}]"
)
WINDOW_STATUS_FORMAT = (
    f"#[default]#[fg=#{{{WINDOW_STATE_COLOR_OPTION}}},bg={BACKGROUND_COLOR}] "
    f"#{{{WINDOW_STATE_GLYPH_OPTION}}} "
    f"#[fg={SECONDARY_TEXT_COLOR},bg={BACKGROUND_COLOR}]#{{window_index}}:"
    f"#[fg={PRIMARY_TEXT_COLOR},bg={BACKGROUND_COLOR}]#{{window_name}}"
    f"#[default]#[fg={PRIMARY_TEXT_COLOR},bg={BACKGROUND_COLOR}]"
)
WINDOW_STATUS_CURRENT_FORMAT = (
    f"#[fg={PRIMARY_TEXT_COLOR},bg={SELECTED_SURFACE_COLOR},bold]["
    f"#[fg=#{{{WINDOW_STATE_COLOR_OPTION}}},bg={SELECTED_SURFACE_COLOR}]"
    f"#{{{WINDOW_STATE_GLYPH_OPTION}}} "
    f"#[fg={SECONDARY_TEXT_COLOR},bg={SELECTED_SURFACE_COLOR}]#{{window_index}}:"
    f"#[fg={PRIMARY_TEXT_COLOR},bg={SELECTED_SURFACE_COLOR}]#{{window_name}}"
    f" #[fg={PRIMARY_TEXT_COLOR},bg={SELECTED_SURFACE_COLOR},bold]]"
    f"#[default]#[fg={PRIMARY_TEXT_COLOR},bg={BACKGROUND_COLOR}]"
)
HOME_WINDOW_STATUS_FORMAT = (
    f"#[default]#[fg={PRIMARY_TEXT_COLOR},bg={BACKGROUND_COLOR}]#{{window_index}}:"
    f"#{{window_name}}#[default]#[fg={PRIMARY_TEXT_COLOR},bg={BACKGROUND_COLOR}]"
)
HOME_WINDOW_STATUS_CURRENT_FORMAT = (
    f"#[fg={PRIMARY_TEXT_COLOR},bg={SELECTED_SURFACE_COLOR},bold]["
    f"#{{window_index}}:#{{window_name}}]"
    f"#[default]#[fg={PRIMARY_TEXT_COLOR},bg={BACKGROUND_COLOR}]"
)

_PANE_BORDER_FORMAT_OPTION = "pane-border-format"
_WINDOW_STATUS_FORMAT_OPTION = "window-status-format"
_WINDOW_STATUS_CURRENT_FORMAT_OPTION = "window-status-current-format"
_TMUX_FORMAT_HASH = "#"
_TMUX_FORMAT_ESCAPED_HASH = "##"


def render_title(template: str, fields: Mapping[str, object]) -> str:
    """Render a `dashboard.titles` template, failing loudly on any field it can't fill."""
    try:
        return template.format(**fields)
    except KeyError as error:
        message = f"title template {template!r} references unknown field {error.args[0]!r}"
        raise ConfigError(message) from error


def _render_styled_title(
    template: str,
    fields: Mapping[str, object],
    field_colors: Mapping[str, str],
    field_segments: Mapping[str, tuple[tuple[str, str], ...]],
) -> str:
    render_title(template, fields)
    formatter = Formatter()
    output: list[str] = [f"#[fg={PRIMARY_TEXT_COLOR},bg={BACKGROUND_COLOR}]"]
    literal_parts: list[str] = []
    for literal, field_name, format_spec, conversion in formatter.parse(template):
        literal_parts.append(literal)
        if field_name is None:
            continue
        if literal_parts:
            output.extend(
                (
                    f"#[fg={PRIMARY_TEXT_COLOR},bg={BACKGROUND_COLOR}]",
                    _escape_tmux_format("".join(literal_parts)),
                )
            )
            literal_parts.clear()
        value, _ = formatter.get_field(field_name, (), fields)
        if conversion is not None:
            value = formatter.convert_field(value, conversion)
        resolved_spec = ""
        if format_spec is not None:
            resolved_spec = formatter.vformat(format_spec, (), fields)
        rendered = formatter.format_field(value, resolved_spec)
        segments = field_segments.get(field_name)
        if segments is not None and "".join(text for text, _ in segments) == rendered:
            for segment_text, color in segments:
                output.extend(
                    (
                        f"#[fg={color},bg={BACKGROUND_COLOR}]",
                        _escape_tmux_format(segment_text),
                    )
                )
        else:
            color = field_colors.get(field_name, PRIMARY_TEXT_COLOR)
            output.extend((f"#[fg={color},bg={BACKGROUND_COLOR}]", _escape_tmux_format(rendered)))
    if literal_parts:
        output.extend(
            (
                f"#[fg={PRIMARY_TEXT_COLOR},bg={BACKGROUND_COLOR}]",
                _escape_tmux_format("".join(literal_parts)),
            )
        )
    output.append(f"#[fg={PRIMARY_TEXT_COLOR},bg={BACKGROUND_COLOR}]")
    return "".join(output)


def _escape_tmux_format(text: str) -> str:
    return text.replace(_TMUX_FORMAT_HASH, _TMUX_FORMAT_ESCAPED_HASH)


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

    async def set_pane_title_styling(
        self,
        pane_target: str,
        template: str,
        fields: Mapping[str, object],
        *,
        field_colors: Mapping[str, str],
        field_segments: Mapping[str, tuple[tuple[str, str], ...]],
    ) -> None:
        """Set the separately rendered title styles for one pane's border row."""
        title_format = _render_styled_title(template, fields, field_colors, field_segments)
        await self._tmux.set_pane_option(pane_target, PANE_TITLE_FORMAT_OPTION, title_format)

    async def set_pane_state(self, pane_target: str, *, color: str, glyph: str) -> None:
        """Set pane `pane_target`'s `@shk_color` and `@shk_glyph`."""
        await self._tmux.set_pane_option(pane_target, PANE_COLOR_OPTION, color)
        await self._tmux.set_pane_option(pane_target, PANE_GLYPH_OPTION, glyph)

    async def set_window_state(self, window_target: str, *, color: str, glyph: str) -> None:
        """Set window `window_target`'s `@shk_state_color` and `@shk_state_glyph`."""
        await self._tmux.set_window_option(window_target, WINDOW_STATE_COLOR_OPTION, color)
        await self._tmux.set_window_option(window_target, WINDOW_STATE_GLYPH_OPTION, glyph)

    async def set_pane_title(
        self, pane_target: str, template: str, fields: Mapping[str, object]
    ) -> None:
        """Render `template` with `fields` and set it as pane `pane_target`'s title."""
        title = render_title(template, fields)
        await self._tmux.set_pane_title(pane_target, title)
        title_format = _render_styled_title(template, fields, {}, {})
        await self._tmux.set_pane_option(pane_target, PANE_TITLE_FORMAT_OPTION, title_format)

    async def set_window_title(
        self, window_target: str, template: str, fields: Mapping[str, object]
    ) -> None:
        """Render `template` with `fields` and rename window `window_target` to it."""
        await self._tmux.rename_window(window_target, render_title(template, fields))
