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
from shikiru.ui.tokens import (
    BACKGROUND_COLOR,
    PRIMARY_TEXT_COLOR,
    SELECTED_SURFACE_COLOR,
)
from tests.support.tmux import attach_tmux_client

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from pathlib import Path

    from tests.support.tmux import TmuxServer

_INHERITED_STYLE = "fg=cyan,bg=green"
_CLOCK_FORMAT = "%H:%M %d-%b-%y"


async def _window(tmux: Tmux) -> str:
    return await tmux.new_window("=test", name="deco", command=("sh", "-c", "sleep 300"))


async def _set_window_options(tmux: Tmux, target: str, options: Mapping[str, str]) -> None:
    for option, value in options.items():
        await tmux.set_window_option(target, option, value)


async def _set_global_options(
    tmux_server: TmuxServer, command: tuple[str, ...], options: Mapping[str, str]
) -> None:
    for option, value in options.items():
        await tmux_server.run(*command, option, value)


async def _show_option_values(
    tmux_server: TmuxServer, command: tuple[str, ...], options: Iterable[str]
) -> dict[str, str]:
    return {option: (await tmux_server.run(*command, option)).stdout.strip() for option in options}


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
    for option, value in inherited_window_options.items():
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
    custom_fields = {"agent": "codex #{window_index} #[bg=green]"}
    title_cases = (
        (
            "Leader #{{session_name}} #[bg=green] · {agent}",
            "Leader #{session_name} #[bg=green] · codex #{window_index} #[bg=green]",
        ),
        (
            "Leader #{{session_name}} #[bg=green] · {agent} ##{{session_name}}",
            (
                "Leader #{session_name} #[bg=green] · codex #{window_index} #[bg=green] "
                "##{session_name}"
            ),
        ),
    )
    custom_template = title_cases[-1][0]
    for title_template, expected_title in title_cases:
        await decorator.set_pane_title(first_pane, title_template, custom_fields)
        assert await tmux.display(first_pane, "#{pane_title}") == expected_title
    await decorator.set_pane_title_styling(
        first_pane,
        custom_template,
        custom_fields,
        field_colors={"agent": PRIMARY_TEXT_COLOR},
        field_segments={},
    )
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
        expanded_title_format = await tmux.display(first_pane, "#{E:@shk_title_format}")
        assert "#{session_name}" in expanded_title_format
        assert "##[bg=green]" in expanded_title_format
        assert "#{window_index}" in expanded_title_format

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
        assert f"bg={BACKGROUND_COLOR}" in pane_border.stdout
        assert "@shk_glyph" not in pane_border.stdout
        assert f"bg={BACKGROUND_COLOR}" in inactive_title.stdout
        assert f"bg={SELECTED_SURFACE_COLOR}" in current_title.stdout
        assert f"bg={SELECTED_SURFACE_COLOR}" in active_window_title.stdout
        assert "#{window_index}" in inactive_title.stdout
        assert "#{window_index}" in current_title.stdout
        assert "#{window_index}" in active_window_title.stdout
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


async def test_project_chrome_is_local_and_recovered_without_changing_focus(
    tmux_server: TmuxServer, tmp_path: Path
) -> None:
    tmux = Tmux(tmux_server.runner, socket=tmux_server.socket_name, env=tmux_server.env)
    inherited_session_options = {
        "status-style": _INHERITED_STYLE,
        "status-left": "server left",
        "status-left-style": _INHERITED_STYLE,
        "status-right": _CLOCK_FORMAT,
        "status-right-style": _INHERITED_STYLE,
        "status-interval": "15",
    }
    inherited_window_options = {
        "pane-border-style": _INHERITED_STYLE,
        "pane-active-border-style": _INHERITED_STYLE,
        "pane-border-format": f"#[{_INHERITED_STYLE}]#{{pane_title}}",
        "window-status-style": _INHERITED_STYLE,
        "window-status-current-style": _INHERITED_STYLE,
        "window-status-format": f"#[{_INHERITED_STYLE}]#{{window_name}}",
        "window-status-current-format": f"#[{_INHERITED_STYLE}]#{{window_name}}",
    }
    await _set_global_options(tmux_server, ("set-option", "-g"), inherited_session_options)
    await _set_global_options(tmux_server, ("set-window-option", "-g"), inherited_window_options)

    project = SessionView(tmux)
    session_target = await project.ensure(
        "chrome-recovery", tmp_path, home_command=("sh", "-c", "exec sleep 300")
    )
    home_pane = await tmux.display(f"{session_target}:{HOME_WINDOW_NAME}", "#{pane_id}")
    home_window = await tmux.window_id(home_pane)
    task_pane = await project.create_task_window(
        session_target, window_name="T-1 Recovery", cwd=tmp_path
    )
    task_window = await tmux.window_id(task_pane)
    decorator = PaneDecorator(tmux)
    await decorator.configure_formats(task_window)
    await decorator.set_window_state(task_window, color="#83bddd", glyph="◆")
    await decorator.set_pane_title(task_pane, "Task title #{{session_name}} #[bg=green]", {})
    task_title = await tmux.display(task_pane, "#{pane_title}")

    operator_pane = await tmux.new_window(
        session_target, name="operator window", cwd=tmp_path, detached=True
    )
    operator_window = await tmux.window_id(operator_pane)
    operator_title = "operator literal #{session_name} #[bg=green]"
    await tmux.set_pane_title(operator_pane, operator_title)
    operator_options = {
        "remain-on-exit": "off",
        "allow-rename": "on",
        "automatic-rename": "on",
        "allow-set-title": "on",
        "pane-border-status": "bottom",
        "pane-border-lines": "single",
        "pane-border-style": _INHERITED_STYLE,
        "pane-active-border-style": _INHERITED_STYLE,
        "pane-border-format": "operator #[fg=red]#{pane_title}",
    }
    await _set_window_options(tmux, operator_window, operator_options)

    await tmux.set_session_option(session_target, "status-style", _INHERITED_STYLE)
    owned_window_options = {
        **inherited_window_options,
        "remain-on-exit": "off",
        "allow-rename": "on",
        "automatic-rename": "on",
        "allow-set-title": "on",
        "pane-border-status": "bottom",
        "pane-border-lines": "single",
    }
    await _set_window_options(tmux, home_window, owned_window_options)
    await _set_window_options(tmux, task_window, owned_window_options)
    await tmux.select_window(operator_window)
    await project.ensure("chrome-recovery", tmp_path, home_command=("sh", "-c", "exec sleep 300"))

    assert await tmux.display(operator_window, "#{window_active}") == "1"
    assert await tmux.display(home_pane, "#{window_active}") == "0"
    assert await tmux.display(task_pane, "#{window_active}") == "0"
    assert await tmux.display(task_pane, "#{pane_title}") == task_title
    assert await tmux.display(operator_pane, "#{pane_title}") == operator_title
    operator_actual = await _show_option_values(
        tmux_server, ("show-window-options", "-v", "-t", operator_window), operator_options
    )
    assert operator_actual == operator_options

    for window_target, border_status in ((home_window, "off"), (task_window, "top")):
        expected_options = {
            "remain-on-exit": "on",
            "allow-rename": "off",
            "automatic-rename": "off",
            "allow-set-title": "off",
            "pane-border-status": border_status,
            "pane-border-lines": "heavy",
        }
        actual_options = await _show_option_values(
            tmux_server,
            ("show-window-options", "-v", "-t", window_target),
            expected_options,
        )
        assert actual_options == expected_options

    status_style = await tmux_server.run(
        "show-options", "-Aqv", "-t", f"{session_target}:", "status-style"
    )
    assert f"bg={BACKGROUND_COLOR}" in status_style.stdout
    for window_target in (home_window, task_window, operator_window):
        status_styles = await _show_option_values(
            tmux_server,
            ("show-window-options", "-v", "-t", window_target),
            ("window-status-style", "window-status-current-style"),
        )
        assert _INHERITED_STYLE not in status_styles.values()
    for window_target in (home_window, task_window):
        pane_style = await tmux_server.run(
            "show-window-options", "-v", "-t", window_target, "pane-border-style"
        )
        assert f"bg={BACKGROUND_COLOR}" in pane_style.stdout

    assert (
        await _show_option_values(tmux_server, ("show-options", "-gqv"), inherited_session_options)
        == inherited_session_options
    )
    assert (
        await _show_option_values(
            tmux_server, ("show-window-options", "-g", "-v"), inherited_window_options
        )
        == inherited_window_options
    )
    unrelated_status = await tmux_server.run(
        "show-options", "-Aqv", "-t", f"={tmux_server.session}:", "status-style"
    )
    assert unrelated_status.stdout.strip() == _INHERITED_STYLE
