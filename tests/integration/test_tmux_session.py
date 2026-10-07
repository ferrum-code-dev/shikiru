"""Integration tests for `SessionView` on the private `tmux_server` (06 §5.1)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from shikiru.errors import ConfigError
from shikiru.tmux.client import Tmux, exact_target, tmux_key_label
from shikiru.tmux.session import (
    DETACHED_SIZE,
    HOME_WINDOW_NAME,
    PROJECT_DIR_ENV_VAR,
    SessionView,
    session_name,
)
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from tests.support.tmux import TmuxServer

_HOME_COMMAND = ("sh", "-c", "sleep 300")


def _tmux(server: TmuxServer) -> Tmux:
    return Tmux(server.runner, socket=server.socket_name)


async def test_current_prefix_reads_and_formats_the_private_server_configuration(
    tmux_server: TmuxServer,
) -> None:
    tmux = _tmux(tmux_server)
    await tmux_server.run("set-option", "-g", "prefix", "C-a")

    assert tmux_key_label(await tmux.current_prefix()) == "Ctrl+A"


async def test_ensure_creates_the_session_with_the_project_env_var(
    tmux_server: TmuxServer, tmp_path: Path
) -> None:
    view = SessionView(_tmux(tmux_server))

    target = await view.ensure("demo", tmp_path, home_command=_HOME_COMMAND)

    assert target == exact_target(session_name("demo"))
    assert await _tmux(tmux_server).show_session_environment(target, PROJECT_DIR_ENV_VAR) == str(
        tmp_path
    )
    assert await _tmux(tmux_server).target_exists(f"{target}:{HOME_WINDOW_NAME}")


async def test_show_session_environment_reads_a_removed_variable_as_unset(
    tmux_server: TmuxServer, tmp_path: Path
) -> None:
    view = SessionView(_tmux(tmux_server))
    target = await view.ensure("demo", tmp_path, home_command=_HOME_COMMAND)

    await tmux_server.run("set-environment", "-t", target, "-r", PROJECT_DIR_ENV_VAR)

    assert await _tmux(tmux_server).show_session_environment(target, PROJECT_DIR_ENV_VAR) is None


async def test_ensure_recreates_the_home_window_once_it_has_been_killed(
    tmux_server: TmuxServer, tmp_path: Path
) -> None:
    tmux = _tmux(tmux_server)
    view = SessionView(tmux)
    target = await view.ensure("demo", tmp_path, home_command=_HOME_COMMAND)
    await view.create_task_window(target, window_name="T-1", cwd=tmp_path)
    window_target = f"{target}:{HOME_WINDOW_NAME}"
    await tmux.kill_window(window_target)
    assert not await tmux.target_exists(window_target)

    await view.ensure("demo", tmp_path, home_command=_HOME_COMMAND)

    assert await tmux.target_exists(window_target)


async def test_missing_pane_is_dead(tmux_server: TmuxServer, tmp_path: Path) -> None:
    tmux = _tmux(tmux_server)
    view = SessionView(tmux)
    target = await view.ensure("demo", tmp_path, home_command=_HOME_COMMAND)
    pane_id = await view.create_task_window(target, window_name="T-1", cwd=tmp_path)

    await tmux_server.run("kill-pane", "-t", pane_id)

    assert not await tmux.pane_exists(pane_id)
    assert await tmux.is_pane_dead(pane_id)


async def test_ensure_applies_session_options_to_a_pre_existing_session(
    tmux_server: TmuxServer, tmp_path: Path
) -> None:
    tmux = _tmux(tmux_server)
    name = session_name("demo")
    target = exact_target(name)
    await tmux.new_session(
        name,
        cwd=tmp_path,
        env={PROJECT_DIR_ENV_VAR: str(tmp_path)},
        window_name=HOME_WINDOW_NAME,
        size=DETACHED_SIZE,
        command=_HOME_COMMAND,
    )
    view = SessionView(tmux)

    await view.ensure("demo", tmp_path, home_command=_HOME_COMMAND)

    assert await tmux.display(f"{target}:{HOME_WINDOW_NAME}", "#{mouse}") == "1"


async def test_ensure_rejects_a_session_name_collision_with_another_project(
    tmux_server: TmuxServer, tmp_path: Path
) -> None:
    view = SessionView(_tmux(tmux_server))
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    await view.ensure("demo", other_dir, home_command=_HOME_COMMAND)

    with pytest.raises(ConfigError):
        await view.ensure("demo", tmp_path / "mine", home_command=_HOME_COMMAND)


async def test_task_window_is_created_and_closed(tmux_server: TmuxServer, tmp_path: Path) -> None:
    tmux = _tmux(tmux_server)
    view = SessionView(tmux)
    target = await view.ensure("demo", tmp_path, home_command=_HOME_COMMAND)

    base_pane = await view.create_task_window(target, window_name="T-1", cwd=tmp_path)
    window_target = f"{target}:T-1"
    assert await tmux.target_exists(window_target)
    assert base_pane

    await view.close_task_window(window_target)
    assert not await tmux.target_exists(window_target)


async def test_task_windows_get_the_detached_size_before_any_client_attaches(
    tmux_server: TmuxServer, tmp_path: Path
) -> None:
    tmux = _tmux(tmux_server)
    view = SessionView(tmux)
    target = await view.ensure("demo", tmp_path, home_command=_HOME_COMMAND)

    await view.create_task_window(target, window_name="T-1", cwd=tmp_path)

    size = await tmux.display(f"{target}:T-1", "#{window_width}x#{window_height}")
    assert size == "{}x{}".format(*DETACHED_SIZE)


async def test_task_window_survives_its_process_exiting(
    tmux_server: TmuxServer, tmp_path: Path
) -> None:
    tmux = _tmux(tmux_server)
    view = SessionView(tmux)
    target = await view.ensure("demo", tmp_path, home_command=_HOME_COMMAND)
    window_target = f"{target}:T-1"
    pane_id = await view.create_task_window(target, window_name="T-1", cwd=tmp_path)

    await tmux.respawn_pane(pane_id, ("/bin/sh", "-c", "exit"), env={}, cwd=tmp_path)

    await wait_until(lambda: tmux.is_pane_dead(pane_id), 5.0)
    assert await tmux.target_exists(window_target)
