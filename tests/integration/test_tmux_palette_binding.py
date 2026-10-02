"""Integration tests for the conditional `prefix + Space` palette binding (06 §5.2)."""

from __future__ import annotations

import json
import shlex
import stat
import sys
from typing import TYPE_CHECKING

from shikiru.tmux.client import FORMAT_PANE_ID, Tmux, exact_target
from shikiru.tmux.session import SHIKIRU_SESSION_PATTERN, bind_palette_key

if TYPE_CHECKING:
    from pathlib import Path

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
    popup_tokens = shlex.split(tokens[if_shell_index + 3])
    assert popup_tokens[0] == "run-shell"
    shell_tokens = shlex.split(popup_tokens[1])
    assert shell_tokens[:3] == ["tmux", "display-popup", "-E"]
    assert shell_tokens[-2:] == ["--origin-pane", FORMAT_PANE_ID]


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
    run_shell = shlex.split(tokens[if_shell_index + 3])
    await tmux_server.run("run-shell", "-t", pane_id, run_shell[1])

    dispatched = json.loads(capture_path.read_text())
    assert dispatched == ["display-popup", "-E", *command[:-1], pane_id]
