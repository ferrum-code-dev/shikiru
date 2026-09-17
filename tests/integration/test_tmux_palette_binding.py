"""Integration tests for the conditional `prefix + Space` palette binding (06 §5.2)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.tmux.client import Tmux
from shikiru.tmux.session import SHIKIRU_SESSION_PATTERN, bind_palette_key

if TYPE_CHECKING:
    from pathlib import Path

    from tests.support.tmux import TmuxServer


async def test_binding_registers_without_error(tmux_server: TmuxServer) -> None:
    tmux = Tmux(tmux_server.runner, socket=tmux_server.socket_name)

    await bind_palette_key(tmux, ("shikiru", "palette"))


async def test_session_pattern_matches_only_shikiru_sessions(
    tmux_server: TmuxServer, tmp_path: Path
) -> None:
    """The `if-shell -F` pattern the binding dispatches on (06 §5.2): the crux of AC6."""
    tmux = Tmux(tmux_server.runner, socket=tmux_server.socket_name)
    await tmux.new_session(
        "[shikiru] demo", cwd=tmp_path, env={}, window_name="a", command=("sh", "-c", "sleep 300")
    )
    await tmux.new_session(
        "notshikiru", cwd=tmp_path, env={}, window_name="a", command=("sh", "-c", "sleep 300")
    )

    inside = await tmux.display("=[shikiru] demo:", SHIKIRU_SESSION_PATTERN)
    outside = await tmux.display("=test:", SHIKIRU_SESSION_PATTERN)
    not_a_prefix_match = await tmux.display("=notshikiru:", SHIKIRU_SESSION_PATTERN)

    assert inside == "1"
    assert outside == "0"
    assert not_a_prefix_match == "0"
