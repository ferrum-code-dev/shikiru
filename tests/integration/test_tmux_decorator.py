"""Integration tests for `PaneDecorator` on the private `tmux_server` (06 §5.3, §6.1)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from shikiru.errors import ConfigError
from shikiru.tmux.client import Tmux
from shikiru.tmux.decorator import (
    PANE_COLOR_OPTION,
    PANE_GLYPH_OPTION,
    PaneDecorator,
)
from shikiru.tmux.session import HOME_WINDOW_NAME, SessionView
from shikiru.ui.tokens import PRIMARY_TEXT_COLOR, SELECTED_SURFACE_COLOR, SURFACE_COLOR
from tests.support.tmux import attach_tmux_client

if TYPE_CHECKING:
    from pathlib import Path

    from tests.support.tmux import TmuxServer

_INHERITED_STYLE = "fg=cyan,bg=green"
_CLOCK_FORMAT = "%H:%M %d-%b-%y"


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

    with pytest.raises(ConfigError):
        await decorator.set_pane_title(pane_id, "${cost:.2f}", {})


async def test_task_titles_use_scoped_readable_styles_and_keep_the_attached_session_untouched(
    tmux_server: TmuxServer, tmp_path: Path
) -> None:
    tmux = Tmux(tmux_server.runner, socket=tmux_server.socket_name, env=tmux_server.env)
    inherited_window_options = {
        "pane-border-style": _INHERITED_STYLE,
        "pane-active-border-style": _INHERITED_STYLE,
        "pane-border-format": f"#[{_INHERITED_STYLE}]#{{pane_title}}",
        "window-status-style": _INHERITED_STYLE,
        "window-status-current-style": _INHERITED_STYLE,
        "window-status-format": f"#[{_INHERITED_STYLE}]#{{window_name}}",
        "window-status-current-format": f"#[{_INHERITED_STYLE}]#{{window_name}}",
    }
    inherited_session_options = {"status-right": _CLOCK_FORMAT, "prefix": "C-b"}
    for option, value in inherited_window_options.items():
        await tmux_server.run("set-option", "-g", option, value)
    for option, value in inherited_session_options.items():
        await tmux_server.run("set-option", "-g", option, value)

    session_target = await SessionView(tmux).ensure(
        "styled", tmp_path, home_command=("sh", "-c", "exec sleep 300")
    )
    home_pane = await tmux.display(f"{session_target}:{HOME_WINDOW_NAME}", "#{pane_id}")
    project = SessionView(tmux)
    first_pane = await project.create_task_window(
        session_target, window_name="T-1 First", cwd=tmp_path
    )
    first_window = await tmux.window_id(first_pane)
    decorator = PaneDecorator(tmux)
    await decorator.configure_formats(first_window)
    await decorator.set_pane_state(first_pane, color="#83bddd", glyph="◆")
    await decorator.set_pane_title(first_pane, "Leader · codex:model/high · iter 1 · ctx 42%", {})
    assert await tmux.display(home_pane, "#{window_active}") == "1"

    second_pane = await project.create_task_window(
        session_target, window_name="T-2 Second", cwd=tmp_path
    )
    second_window = await tmux.window_id(second_pane)
    await decorator.configure_formats(second_window)
    assert await tmux.display(home_pane, "#{window_active}") == "1"
    await tmux.select_window(second_window)

    async with attach_tmux_client(tmux_server, session_target):
        assert await tmux.display(first_pane, "#{window_active}") == "0"
        assert await tmux.display(second_pane, "#{window_active}") == "1"

        pane_border = await tmux_server.run(
            "show-window-options", "-v", "-t", first_window, "pane-border-format"
        )
        inactive_title = await tmux_server.run(
            "show-window-options", "-v", "-t", first_window, "window-status-format"
        )
        current_title = await tmux_server.run(
            "show-window-options", "-v", "-t", first_window, "window-status-current-format"
        )
        active_window_title = await tmux_server.run(
            "show-window-options", "-v", "-t", second_window, "window-status-current-format"
        )
        assert f"fg={PRIMARY_TEXT_COLOR}" in pane_border.stdout
        assert f"bg={SURFACE_COLOR}" in pane_border.stdout
        assert "@shk_glyph" not in pane_border.stdout
        assert f"bg={SURFACE_COLOR}" in inactive_title.stdout
        assert f"bg={SELECTED_SURFACE_COLOR}" in current_title.stdout
        assert f"bg={SELECTED_SURFACE_COLOR}" in active_window_title.stdout
        assert await tmux.display(first_pane, "#{pane_title}") == (
            "Leader · codex:model/high · iter 1 · ctx 42%"
        )

        for option, value in inherited_session_options.items():
            effective = await tmux_server.run(
                "show-options", "-Aqv", "-t", f"{session_target}:", option
            )
            assert effective.stdout.strip() == value
        assert await tmux.display(f"{session_target}:{HOME_WINDOW_NAME}", "#{window_index}") == "0"

    for option, value in inherited_window_options.items():
        global_value = await tmux_server.run("show-window-options", "-g", "-v", option)
        assert global_value.stdout.strip() == value

    unrelated_style = await tmux_server.run(
        "show-options",
        "-A",
        "-w",
        "-qv",
        "-t",
        f"={tmux_server.session}:0",
        "window-status-format",
    )
    assert _INHERITED_STYLE in unrelated_style.stdout
