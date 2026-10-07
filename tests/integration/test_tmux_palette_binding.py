"""Integration tests for the conditional `prefix + Space` dashboard and palette routes."""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import stat
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from shikiru.clock import SystemClock
from shikiru.operator.lifecycle import home_command
from shikiru.process import identify_pid
from shikiru.store.commands import CommandQueue
from shikiru.store.database import Database
from shikiru.tmux.client import FORMAT_PANE_ID, Tmux, exact_target
from shikiru.tmux.session import (
    PROJECT_DIR_ENV_VAR,
    SHIKIRU_SESSION_PATTERN,
    bind_palette_key,
    session_name,
)
from tests.support.config import write_minimal_project
from tests.support.store import seed_task
from tests.support.tmux import attach_tmux_client
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from shikiru.domain.tasks import Task
    from tests.support.tmux import TmuxServer

_PALETTE_COMMAND = (
    sys.executable,
    "-m",
    "shikiru",
    "palette",
    "--origin-pane",
    FORMAT_PANE_ID,
)
_NATIVE_VISUAL_REVIEW_DIRECTORY = "SHIKIRU_NATIVE_VISUAL_REVIEW_DIR"


async def _bound_space_tokens(tmux_server: TmuxServer) -> list[str]:
    result = await tmux_server.run("list-keys", "-T", "prefix")
    lines = [line for line in result.stdout.splitlines() if " Space " in line]
    assert len(lines) == 1
    return shlex.split(lines[0])


async def _client_key_table(tmux_server: TmuxServer, target: str) -> str:
    result = await tmux_server.run("display-message", "-p", "-t", target, "#{client_key_table}")
    return result.stdout.strip()


async def test_bind_palette_key_dispatches_on_the_shikiru_session_pattern_else_next_layout(
    tmux_server: TmuxServer,
) -> None:
    """Home receives F12, other project panes retain the popup, and unrelated sessions keep
    tmux's `next-layout` default.
    """
    tmux = Tmux(
        tmux_server.runner,
        socket=tmux_server.socket_name,
        env=tmux_server.env,
    )

    await bind_palette_key(tmux, _PALETTE_COMMAND)

    tokens = await _bound_space_tokens(tmux_server)
    if_shell_index = tokens.index("if-shell")
    assert tokens[if_shell_index + 1] == "-F"
    assert tokens[if_shell_index + 2] == SHIKIRU_SESSION_PATTERN
    assert tokens[-1] == "next-layout"
    route_tokens = shlex.split(tokens[if_shell_index + 3])
    assert route_tokens[:2] == ["if-shell", "-F"]
    assert "dash --home" in route_tokens[2]
    assert "pane_dead" in route_tokens[2]
    dashboard_tokens = shlex.split(route_tokens[3])
    assert dashboard_tokens == ["send-keys", "F12"]
    popup_tokens = shlex.split(route_tokens[4])
    assert popup_tokens[0] == "run-shell"
    shell_tokens = shlex.split(popup_tokens[1])
    assert shell_tokens[:3] == ["tmux", "display-popup", "-E"]
    assert shell_tokens[-2:] == ["--origin-pane", FORMAT_PANE_ID]


async def _exercise_live_home_palette_routes(
    tmux_server: TmuxServer,
    session: str,
    pane_id: str,
    pane_pid: int,
    task: Task,
) -> None:
    async def pane_text() -> str:
        return (await tmux_server.run("capture-pane", "-p", "-t", pane_id, check=False)).stdout

    async def dashboard_ready() -> bool:
        text = await pane_text()
        return "SHIKIRU" in text and task.title in text

    async def actions_open() -> bool:
        return "Shikiru actions" in await pane_text()

    async def actions_closed() -> bool:
        text = await pane_text()
        return task.title in text and "Shikiru actions" not in text

    await wait_until(dashboard_ready, timeout=15)
    await tmux_server.run("select-window", "-t", pane_id)
    async with attach_tmux_client(tmux_server, session):
        client_tty = await tmux_server.run("list-clients", "-t", session, "-F", "#{client_tty}")
        client_target = client_tty.stdout.strip()
        client_pane = await tmux_server.run(
            "display-message", "-p", "-t", client_target, "#{pane_id}"
        )
        assert client_pane.stdout.strip() == pane_id
        await tmux_server.run("send-keys", "-K", "-c", client_target, "C-b")

        async def prefix_active() -> bool:
            return await _client_key_table(tmux_server, client_target) == "prefix"

        await wait_until(prefix_active, timeout=5)
        await tmux_server.run("send-keys", "-K", "-c", client_target, "Space")
        await wait_until(actions_open, timeout=5)
        actions_text = await pane_text()
        assert "Shikiru actions" in actions_text
        assert "TASKS" in actions_text
        native_directory = os.environ.get(_NATIVE_VISUAL_REVIEW_DIRECTORY)
        if native_directory is not None:
            capture_directory = Path(native_directory)
            await asyncio.to_thread(capture_directory.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(
                (capture_directory / "task108-prefix-space-actions-pane.txt").write_text,
                actions_text,
                encoding="utf-8",
            )
        assert (
            await tmux_server.run("display-message", "-p", "-t", pane_id, "#{pane_pid}")
        ).stdout.strip() == str(pane_pid)
        await tmux_server.run("send-keys", "-t", pane_id, "Escape")
        await wait_until(actions_closed, timeout=5)

        await tmux_server.run("send-keys", "-t", pane_id, "C-p")
        await wait_until(actions_open, timeout=5)
        actions_text = await pane_text()
        assert "Shikiru actions" in actions_text
        assert "TASKS" in actions_text
        await tmux_server.run("send-keys", "-t", pane_id, "Escape")
        await wait_until(actions_closed, timeout=5)


async def test_home_prefix_space_and_ctrl_p_open_actions_in_the_existing_dashboard(
    tmp_path: Path,
    tmux_server: TmuxServer,
) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    write_minimal_project(project_dir)
    expected_session = session_name("demo")
    await tmux_server.run("rename-session", "-t", tmux_server.session, expected_session)
    await tmux_server.run(
        "set-environment", "-t", expected_session, PROJECT_DIR_ENV_VAR, str(project_dir)
    )
    database = Database(project_dir / ".shikiru" / "state.db")
    connection = database.connect()
    task = seed_task(connection, SystemClock(), title="tmux overlay route proof")
    queued_before = CommandQueue(connection, SystemClock()).pending()
    connection.close()
    tmux = Tmux(
        tmux_server.runner,
        socket=tmux_server.socket_name,
        env=tmux_server.env,
    )
    pane_id = await tmux.new_window(
        exact_target(expected_session),
        name="dashboard",
        cwd=project_dir,
        command=home_command(project_dir),
    )
    await bind_palette_key(tmux, _PALETTE_COMMAND)
    pane_pid = int(
        (await tmux_server.run("display-message", "-p", "-t", pane_id, "#{pane_pid}")).stdout
    )
    pane_start = await tmux_server.run(
        "display-message", "-p", "-t", pane_id, "#{pane_start_command}"
    )
    assert "dash --home" in pane_start.stdout
    session_match = await tmux_server.run(
        "display-message", "-p", "-t", pane_id, SHIKIRU_SESSION_PATTERN
    )
    assert session_match.stdout.strip() == "1"
    try:
        await _exercise_live_home_palette_routes(
            tmux_server, expected_session, pane_id, pane_pid, task
        )
        connection = database.connect()
        try:
            assert CommandQueue(connection, SystemClock()).pending() == queued_before
        finally:
            connection.close()
    finally:
        await tmux_server.run("kill-window", "-t", pane_id, check=False)
        await wait_until(lambda: identify_pid(pane_pid) is None, timeout=5)


async def test_popup_command_dispatch_expands_origin_and_preserves_argv(
    tmp_path: Path,
    tmux_server: TmuxServer,
) -> None:
    tmux = Tmux(
        tmux_server.runner,
        socket=tmux_server.socket_name,
        env=tmux_server.env,
    )
    pane_id = await tmux.new_window(exact_target(tmux_server.session), name="origin")
    capture_path = tmp_path / "captured argv.json"
    fake_bin = tmp_path / "tmux command"
    fake_bin.mkdir()
    fake_tmux = fake_bin / "tmux"
    fake_tmux.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "from pathlib import Path\n"
        f"Path({str(capture_path)!r}).write_text(json.dumps(sys.argv[1:]))\n"
    )
    fake_tmux.chmod(fake_tmux.stat().st_mode | stat.S_IXUSR)
    await tmux_server.run(
        "set-environment",
        "-g",
        "PATH",
        f"{fake_bin}:{tmux_server.env['PATH']}",
    )
    command = (
        sys.executable,
        "-m",
        "shikiru",
        "palette",
        "--project-root",
        str(tmp_path / "project with spaces"),
        "--origin-pane",
        FORMAT_PANE_ID,
    )
    await bind_palette_key(tmux, command)

    tokens = await _bound_space_tokens(tmux_server)
    if_shell_index = tokens.index("if-shell")
    route_tokens = shlex.split(tokens[if_shell_index + 3])
    run_shell = shlex.split(route_tokens[4])
    await tmux_server.run("run-shell", "-t", pane_id, run_shell[1])

    dispatched = json.loads(capture_path.read_text())
    assert dispatched == ["display-popup", "-E", *command[:-1], pane_id]
