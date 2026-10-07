"""Integration tests for `SessionView` on the private `tmux_server` (06 §5.1)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from shikiru.errors import ConfigError
from shikiru.tmux.client import Tmux, exact_target, tmux_key_label
from shikiru.tmux.session import (
    DETACHED_SIZE,
    HOME_WINDOW_NAME,
    LEGACY_HOME_WINDOW_NAME,
    PROJECT_DIR_ENV_VAR,
    SessionView,
    session_name,
)
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from tests.support.tmux import TmuxServer

_HOME_COMMAND = ("sh", "-c", "sleep 300; shikiru dash --home")


def _tmux(server: TmuxServer) -> Tmux:
    return Tmux(server.runner, socket=server.socket_name)


async def test_current_prefix_uses_the_matching_session_override(
    tmux_server: TmuxServer,
) -> None:
    tmux = _tmux(tmux_server)
    target = exact_target(tmux_server.session)
    await tmux_server.run("set-option", "-g", "prefix", "C-b")
    await tmux.set_session_option(target, "prefix", "C-a")

    assert tmux_key_label(await tmux.current_prefix(target)) == "Ctrl+A"


@pytest.mark.parametrize("global_base_index", [0, 1])
async def test_ensure_creates_home_at_zero_with_session_scoped_base_index(
    tmux_server: TmuxServer, tmp_path: Path, global_base_index: int
) -> None:
    view = SessionView(_tmux(tmux_server))
    await tmux_server.run("set-option", "-g", "base-index", str(global_base_index))

    target = await view.ensure("demo", tmp_path, home_command=_HOME_COMMAND)
    session_base_index = await tmux_server.run(
        "show-options", "-Aqv", "-t", f"{target}:", "base-index"
    )
    actual_global_base_index = await tmux_server.run("show-options", "-gqv", "base-index")
    home_index = await _tmux(tmux_server).display(f"{target}:{HOME_WINDOW_NAME}", "#{window_index}")

    assert target == exact_target(session_name("demo"))
    assert await _tmux(tmux_server).show_session_environment(target, PROJECT_DIR_ENV_VAR) == str(
        tmp_path
    )
    assert await _tmux(tmux_server).target_exists(f"{target}:{HOME_WINDOW_NAME}")
    assert session_base_index.stdout.strip() == "0"
    assert actual_global_base_index.stdout.strip() == str(global_base_index)
    assert home_index == "0"
    renumber = await tmux_server.run("show-options", "-Aqv", "-t", f"{target}:", "renumber-windows")
    assert renumber.stdout.strip() == "off"


async def test_ensure_does_not_replace_a_window_occupying_home_index_zero(
    tmux_server: TmuxServer, tmp_path: Path
) -> None:
    tmux = _tmux(tmux_server)
    name = session_name("collision")
    target = exact_target(name)
    await tmux_server.run("set-option", "-g", "base-index", "1")
    await tmux.new_session(
        name,
        cwd=tmp_path,
        env={PROJECT_DIR_ENV_VAR: str(tmp_path)},
        window_name=LEGACY_HOME_WINDOW_NAME,
        size=DETACHED_SIZE,
        command=_HOME_COMMAND,
    )
    await tmux_server.run(
        "new-window",
        "-d",
        "-t",
        f"{target}:0",
        "-n",
        "operator window",
        "-c",
        str(tmp_path),
        "sleep",
        "300",
    )
    before = await tmux_server.run(
        "list-windows", "-t", target, "-F", "#{window_index}:#{window_name}\t#{window_id}"
    )
    before_windows = {
        window_id: index_and_name
        for line in before.stdout.splitlines()
        for index_and_name, window_id in (line.split("\t", 1),)
    }
    home_id = next(
        window_id for window_id, row in before_windows.items() if row.endswith(":shikiru")
    )
    operator_id = next(
        window_id for window_id, row in before_windows.items() if row.endswith(":operator window")
    )

    with pytest.raises(ConfigError, match="index 0"):
        await SessionView(tmux).ensure("collision", tmp_path, home_command=_HOME_COMMAND)

    after = await tmux_server.run(
        "list-windows", "-t", target, "-F", "#{window_index}:#{window_name}\t#{window_id}"
    )
    global_base_index = await tmux_server.run("show-options", "-gqv", "base-index")
    after_windows = {
        window_id: index_and_name
        for line in after.stdout.splitlines()
        for index_and_name, window_id in (line.split("\t", 1),)
    }
    assert after_windows[operator_id] == before_windows[operator_id]
    assert after_windows[home_id] == "1:dashboard"
    assert global_base_index.stdout.strip() == "1"


async def test_show_session_environment_reads_a_removed_variable_as_unset(
    tmux_server: TmuxServer, tmp_path: Path
) -> None:
    view = SessionView(_tmux(tmux_server))
    target = await view.ensure("demo", tmp_path, home_command=_HOME_COMMAND)

    await tmux_server.run("set-environment", "-t", target, "-r", PROJECT_DIR_ENV_VAR)

    assert await _tmux(tmux_server).show_session_environment(target, PROJECT_DIR_ENV_VAR) is None


async def test_detach_requires_an_attributable_attached_client(tmux_server: TmuxServer) -> None:
    tmux = _tmux(tmux_server)
    target = exact_target(tmux_server.session)
    pane = (await tmux.list_panes(target, "#{pane_id}"))[0]

    assert await tmux.client_for_pane(target, pane) is None
    assert not await tmux.detach_client_for_pane(target, pane)
    assert await tmux.target_exists(target)


async def test_ensure_recreates_the_home_window_once_it_has_been_killed(
    tmux_server: TmuxServer, tmp_path: Path
) -> None:
    tmux = _tmux(tmux_server)
    view = SessionView(tmux)
    target = await view.ensure("demo", tmp_path, home_command=_HOME_COMMAND)
    task_pane = await view.create_task_window(target, window_name="T-1", cwd=tmp_path)
    task_window = await tmux.window_id(task_pane)
    window_target = f"{target}:{HOME_WINDOW_NAME}"
    await tmux.kill_window(window_target)
    assert not await tmux.target_exists(window_target)

    await view.ensure("demo", tmp_path, home_command=_HOME_COMMAND)

    assert await tmux.target_exists(window_target)
    assert await tmux.display(window_target, "#{window_index}") == "0"
    assert await tmux.display(task_window, "#{window_index}") == "1"


async def test_ensure_reuses_a_renamed_legacy_home_without_touching_other_windows(
    tmux_server: TmuxServer, tmp_path: Path
) -> None:
    tmux = _tmux(tmux_server)
    await tmux_server.run("set-option", "-g", "base-index", "1")
    name = session_name("upgrade")
    target = exact_target(name)
    await tmux.new_session(
        name,
        cwd=tmp_path,
        env={PROJECT_DIR_ENV_VAR: str(tmp_path)},
        window_name=LEGACY_HOME_WINDOW_NAME,
        size=DETACHED_SIZE,
        command=_HOME_COMMAND,
    )
    other_pane = await tmux.new_window(
        target, name="operator window", cwd=tmp_path, command=("sh", "-c", "sleep 300")
    )
    other_window = await tmux.window_id(other_pane)
    other_index = await tmux.display(other_window, "#{window_index}")
    legacy_pane = await tmux.display(f"{target}:{LEGACY_HOME_WINDOW_NAME}", "#{pane_id}")
    await tmux.rename_window(f"{target}:{LEGACY_HOME_WINDOW_NAME}", "operator renamed Home")
    view = SessionView(tmux)

    await view.ensure("upgrade", tmp_path, home_command=_HOME_COMMAND)
    await view.ensure("upgrade", tmp_path, home_command=_HOME_COMMAND)

    windows = await tmux_server.run(
        "list-windows", "-t", target, "-F", "#{window_id}\t#{window_name}"
    )
    home_pane = await tmux.display(f"{target}:{HOME_WINDOW_NAME}", "#{pane_id}")
    home_window = await tmux.window_id(home_pane)
    assert dict(line.split("\t", 1) for line in windows.stdout.splitlines()) == {
        home_window: HOME_WINDOW_NAME,
        other_window: "operator window",
    }
    assert home_pane == legacy_pane
    assert await tmux.display(home_window, "#{window_index}") == "0"
    assert await tmux.display(other_window, "#{window_index}") == other_index
    title = await tmux_server.run("show-options", "-Aqv", "-t", f"{target}:", "set-titles-string")
    assert title.stdout.strip() == "upgrade"


async def test_ensure_converges_duplicate_home_windows_without_moving_focus(
    tmux_server: TmuxServer, tmp_path: Path
) -> None:
    tmux = _tmux(tmux_server)
    await tmux_server.run("set-option", "-g", "base-index", "1")
    view = SessionView(tmux)
    target = await view.ensure("demo", tmp_path, home_command=_HOME_COMMAND)
    first_home = await tmux.window_id(
        await tmux.display(f"{target}:{HOME_WINDOW_NAME}", "#{pane_id}")
    )
    operator_pane = await tmux.new_window(
        target, name="operator window", cwd=tmp_path, command=("sh", "-c", "sleep 300")
    )
    operator_window = await tmux.window_id(operator_pane)
    second_home = await tmux.new_window(
        target,
        name="renamed dashboard",
        cwd=tmp_path,
        command=_HOME_COMMAND,
        detached=True,
    )
    second_home_window = await tmux.window_id(second_home)
    operator_index = await tmux.display(operator_window, "#{window_index}")
    await tmux.select_window(second_home_window)

    await view.ensure("demo", tmp_path, home_command=_HOME_COMMAND)

    windows = await tmux_server.run(
        "list-windows", "-t", target, "-F", "#{window_id}\t#{window_name}"
    )
    assert dict(line.split("\t", 1) for line in windows.stdout.splitlines()) == {
        second_home_window: HOME_WINDOW_NAME,
        operator_window: "operator window",
    }
    assert first_home != second_home_window
    assert await tmux.display(second_home_window, "#{window_index}") == "0"
    assert await tmux.display(operator_window, "#{window_index}") == operator_index


async def test_home_owns_no_pane_border_row_while_task_roles_keep_their_labels(
    tmux_server: TmuxServer, tmp_path: Path
) -> None:
    tmux = _tmux(tmux_server)
    view = SessionView(tmux)
    target = await view.ensure("demo", tmp_path, home_command=_HOME_COMMAND)
    task_pane = await view.create_task_window(target, window_name="T-1 Add retries", cwd=tmp_path)
    task_window = await tmux.window_id(task_pane)

    home_chrome = await tmux_server.run(
        "show-window-options", "-v", "-t", f"{target}:{HOME_WINDOW_NAME}", "pane-border-status"
    )
    task_chrome = await tmux_server.run(
        "show-window-options", "-v", "-t", task_window, "pane-border-status"
    )

    assert home_chrome.stdout.strip() == "off"
    assert task_chrome.stdout.strip() == "top"


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
