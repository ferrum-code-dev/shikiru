"""Integration tests for `PaneDecorator` on the private `tmux_server` (06 §5.3, §6.1)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from shikiru.errors import ConfigError
from shikiru.tmux.client import Tmux
from shikiru.tmux.decorator import PANE_COLOR_OPTION, PANE_GLYPH_OPTION, PaneDecorator

if TYPE_CHECKING:
    from tests.support.tmux import TmuxServer


async def _window(tmux: Tmux) -> str:
    return await tmux.new_window("=test", name="deco", command=("sh", "-c", "sleep 300"))


async def test_pane_state_and_title_render_and_are_readable_back(
    tmux_server: TmuxServer,
) -> None:
    tmux = Tmux(tmux_server.runner, socket=tmux_server.socket_name)
    pane_id = await _window(tmux)
    decorator = PaneDecorator(tmux)

    await decorator.set_pane_state(pane_id, color="#22d3ee", glyph="▲")
    await decorator.set_pane_title(pane_id, "{glyph} {agent}", {"glyph": "▲", "agent": "codex"})

    assert await tmux.display(pane_id, f"#{{{PANE_COLOR_OPTION}}}") == "#22d3ee"
    assert await tmux.display(pane_id, f"#{{{PANE_GLYPH_OPTION}}}") == "▲"
    assert await tmux.display(pane_id, "#{pane_title}") == "▲ codex"


async def test_window_state_and_title_render_and_are_readable_back(
    tmux_server: TmuxServer,
) -> None:
    tmux = Tmux(tmux_server.runner, socket=tmux_server.socket_name)
    pane_id = await _window(tmux)
    window_target = await tmux.display(pane_id, "#{window_id}")
    decorator = PaneDecorator(tmux)

    await decorator.set_window_state(window_target, color="#facc15", glyph="⏧")
    await decorator.set_window_title(
        window_target,
        "{task_id} {title:.4}",
        {
            "task_id": "T-1",
            "title": "Add retry logic",
        },
    )

    assert await tmux.display(window_target, "#{window_name}") == "T-1 Add"


async def test_render_title_fails_loudly_on_an_unfilled_field(tmux_server: TmuxServer) -> None:
    tmux = Tmux(tmux_server.runner, socket=tmux_server.socket_name)
    pane_id = await _window(tmux)
    decorator = PaneDecorator(tmux)

    with pytest.raises(ConfigError, match="cost"):
        await decorator.set_pane_title(pane_id, "${cost:.2f}", {})
