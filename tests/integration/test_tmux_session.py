"""Integration tests for `SessionView` on the private `tmux_server` (06 §5.1)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from shikiru.errors import ConfigError
from shikiru.tmux.client import Tmux, exact_target
from shikiru.tmux.session import HOME_WINDOW_NAME, PROJECT_DIR_ENV_VAR, SessionView, session_name
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from tests.support.tmux import TmuxServer

_HOME_COMMAND = ("sh", "-c", "sleep 300")


def _tmux(server: TmuxServer) -> Tmux:
    return Tmux(server.runner, socket=server.socket_name)


async def test_ensure_creates_the_session_with_the_project_env_var(
    tmux_server: TmuxServer, tmp_path: Path
) -> None:
    view = SessionView(_tmux(tmux_server))

    target = await view.ensure("demo", tmp_path, home_command=_HOME_COMMAND)

    assert target == exact_target(session_name("demo"))
    assert await _tmux(tmux_server).show_session_environment(target, PROJECT_DIR_ENV_VAR) == str(
        tmp_path
    )
    assert await _tmux(tmux_server).has_session(f"{target}:{HOME_WINDOW_NAME}")


async def test_ensure_is_idempotent(tmux_server: TmuxServer, tmp_path: Path) -> None:
    view = SessionView(_tmux(tmux_server))

    await view.ensure("demo", tmp_path, home_command=_HOME_COMMAND)
    await view.ensure("demo", tmp_path, home_command=_HOME_COMMAND)

    target = exact_target(session_name("demo"))
    windows = await _tmux(tmux_server).list_windows(target, "#{window_name}")
    assert windows.count(HOME_WINDOW_NAME) == 1


async def test_ensure_rejects_a_session_name_collision_with_another_project(
    tmux_server: TmuxServer, tmp_path: Path
) -> None:
    view = SessionView(_tmux(tmux_server))
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    await view.ensure("demo", other_dir, home_command=_HOME_COMMAND)

    with pytest.raises(ConfigError, match=str(other_dir)):
        await view.ensure("demo", tmp_path / "mine", home_command=_HOME_COMMAND)


async def test_task_window_is_created_and_closed(tmux_server: TmuxServer, tmp_path: Path) -> None:
    tmux = _tmux(tmux_server)
    view = SessionView(tmux)
    target = await view.ensure("demo", tmp_path, home_command=_HOME_COMMAND)

    base_pane = await view.create_task_window(target, window_name="T-1", cwd=tmp_path)
    window_target = f"{target}:T-1"
    assert await tmux.has_session(window_target)
    assert base_pane

    await view.close_task_window(window_target)
    assert not await tmux.has_session(window_target)


async def test_task_window_survives_its_process_exiting(
    tmux_server: TmuxServer, tmp_path: Path
) -> None:
    tmux = _tmux(tmux_server)
    view = SessionView(tmux)
    target = await view.ensure("demo", tmp_path, home_command=_HOME_COMMAND)
    window_target = f"{target}:T-1"
    pane_id = await view.create_task_window(target, window_name="T-1", cwd=tmp_path)

    await tmux.send_keys(pane_id, "exit", "Enter")

    await wait_until(lambda: tmux.is_pane_dead(pane_id), 5.0)
    assert await tmux.has_session(window_target)
