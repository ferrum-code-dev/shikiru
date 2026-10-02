"""Integration tests for the conditional `prefix + Space` palette binding (06 §5.2)."""

from __future__ import annotations

import shlex
import sys
from typing import TYPE_CHECKING

from shikiru.tmux.client import FORMAT_PANE_ID, Tmux
from shikiru.tmux.session import SHIKIRU_SESSION_PATTERN, bind_palette_key

if TYPE_CHECKING:
    from tests.support.tmux import TmuxServer

_PALETTE_COMMAND = (
    sys.executable,
    "-m",
    "shikiru",
    "palette",
    "--origin-pane",
    FORMAT_PANE_ID,
)


async def _bound_space_tokens(tmux_server: TmuxServer) -> list[str]:
    result = await tmux_server.run("list-keys", "-T", "prefix")
    lines = [line for line in result.stdout.splitlines() if " Space " in line]
    assert len(lines) == 1
    return shlex.split(lines[0])


async def test_bind_palette_key_dispatches_on_the_shikiru_session_pattern_else_next_layout(
    tmux_server: TmuxServer,
) -> None:
    """Binding prefix+Space runs `if-shell -F` against the `[shikiru]` session match, falling
    back to tmux's own `next-layout` (06 §5.2) rather than overriding the key everywhere.
    """
    tmux = Tmux(tmux_server.runner, socket=tmux_server.socket_name)

    await bind_palette_key(tmux, _PALETTE_COMMAND)

    tokens = await _bound_space_tokens(tmux_server)
    if_shell_index = tokens.index("if-shell")
    assert tokens[if_shell_index + 1] == "-F"
    assert tokens[if_shell_index + 2] == SHIKIRU_SESSION_PATTERN
    assert tokens[-1] == "next-layout"
    popup_tokens = shlex.split(tokens[if_shell_index + 3])
    assert popup_tokens[:2] == ["display-popup", "-E"]
    assert popup_tokens[-2:] == ["--origin-pane", FORMAT_PANE_ID]
