"""Integration tests for `TmuxPane` on the private `tmux_server` (04 §1; 03 §4.3)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.clock import SystemClock
from shikiru.tmux.client import Tmux
from shikiru.tmux.pane import TmuxPane
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from tests.support.tmux import TmuxServer

_WAIT_TIMEOUT = 5.0


async def test_paste_line_delivers_the_line_to_the_pane(
    tmux_server: TmuxServer, tmp_path: Path
) -> None:
    tmux = Tmux(tmux_server.runner, socket=tmux_server.socket_name)
    marker_path = tmp_path / "received"
    pane_id = await tmux.new_window(
        "=test", name="reader", command=("sh", "-c", f"cat > {marker_path}")
    )
    pane = TmuxPane(tmux, pane_id, SystemClock())

    await pane.paste_line("hello shikiru")

    async def _line_written() -> bool:
        return marker_path.exists() and marker_path.read_text().strip() == "hello shikiru"

    await wait_until(_line_written, _WAIT_TIMEOUT)


async def test_is_alive_is_false_for_a_dead_pane_kept_by_remain_on_exit(
    tmux_server: TmuxServer,
) -> None:
    tmux = Tmux(tmux_server.runner, socket=tmux_server.socket_name)
    pane_id = await tmux.new_window(
        "=test", name="dying", command=("sh", "-c", "sleep 0.2; exit 0")
    )
    await tmux.set_window_option("=test:dying", "remain-on-exit", "on")
    pane = TmuxPane(tmux, pane_id, SystemClock())

    await wait_until(lambda: tmux.is_pane_dead(pane_id), _WAIT_TIMEOUT)

    assert await pane.is_alive() is False


async def test_is_alive_is_true_for_a_live_pane(tmux_server: TmuxServer) -> None:
    tmux = Tmux(tmux_server.runner, socket=tmux_server.socket_name)
    pane_id = await tmux.new_window("=test", name="alive", command=("sh", "-c", "sleep 300"))
    pane = TmuxPane(tmux, pane_id, SystemClock())

    assert await pane.is_alive() is True


async def test_missing_pane_is_dead(tmux_server: TmuxServer) -> None:
    tmux = Tmux(tmux_server.runner, socket=tmux_server.socket_name)
    pane_id = await tmux.new_window("=test", name="removed")
    await tmux.kill_pane(pane_id)

    assert await tmux.is_pane_dead(pane_id) is True


async def test_respawn_replaces_the_process_with_new_argv_cwd_and_env(
    tmux_server: TmuxServer, tmp_path: Path
) -> None:
    tmux = Tmux(tmux_server.runner, socket=tmux_server.socket_name)
    pane_id = await tmux.new_window("=test", name="respawn", command=("sh", "-c", "sleep 300"))
    pane = TmuxPane(tmux, pane_id, SystemClock())
    first_dir = tmp_path / "first"
    first_dir.mkdir()

    await pane.respawn(
        ("sh", "-c", 'echo "FOO=[$FOO] PWD=$(pwd)"; sleep 300'),
        env={"FOO": "baz"},
        cwd=first_dir,
    )

    async def _shows_first() -> bool:
        text = await pane.capture_visible()
        return f"FOO=[baz] PWD={first_dir}" in text

    await wait_until(_shows_first, _WAIT_TIMEOUT)

    second_dir = tmp_path / "second"
    second_dir.mkdir()
    await pane.respawn(
        ("sh", "-c", 'echo "FOO=[$FOO] PWD=$(pwd)"; sleep 300'),
        env={"FOO": None},
        cwd=second_dir,
    )

    async def _shows_second() -> bool:
        text = await pane.capture_visible()
        return f"FOO=[] PWD={second_dir}" in text

    await wait_until(_shows_second, _WAIT_TIMEOUT)


async def test_send_keys_types_into_the_pane_a_running_reader_receives(
    tmux_server: TmuxServer, tmp_path: Path
) -> None:
    tmux = Tmux(tmux_server.runner, socket=tmux_server.socket_name)
    marker_path = tmp_path / "received"
    pane_id = await tmux.new_window(
        "=test", name="keys", command=("sh", "-c", f'read line; echo "GOT:$line" > {marker_path}')
    )
    pane = TmuxPane(tmux, pane_id, SystemClock())

    await pane.send_keys("typed by send_keys", "Enter")

    async def _line_received() -> bool:
        return marker_path.exists() and marker_path.read_text().strip() == "GOT:typed by send_keys"

    await wait_until(_line_received, _WAIT_TIMEOUT)
