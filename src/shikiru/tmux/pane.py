"""One tmux pane as a `PaneHandle` (04 §1), plus pointer injection (03 §4.3)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from shikiru.clock import Clock
    from shikiru.tmux.client import Tmux

INJECT_DELAY_SECONDS = 0.15
ENTER_KEY = "Enter"
_INJECT_BUFFER_PREFIX = "shikiru-inject-"


class TmuxPane:
    """The `agents.contracts.PaneHandle` for one tmux pane, plus pointer injection."""

    def __init__(self, tmux: Tmux, pane_id: str, clock: Clock) -> None:
        """Address pane `pane_id` through `tmux`, timing injection with `clock`."""
        self._tmux = tmux
        self._pane_id = pane_id
        self._clock = clock

    async def respawn(self, argv: Sequence[str], env: Mapping[str, str | None], cwd: Path) -> None:
        """Restart this pane's process with `argv`, `env` and `cwd`."""
        await self._tmux.respawn_pane(self._pane_id, argv, env=env, cwd=cwd)

    async def capture_visible(self) -> str:
        """Return this pane's currently visible text."""
        return await self._tmux.capture_pane(self._pane_id)

    async def capture_tail(self, lines: int) -> str:
        """Return this pane's last `lines` lines, including scrollback (03 §8's evidence)."""
        return await self._tmux.capture_pane(self._pane_id, tail_lines=lines)

    async def is_alive(self) -> bool:
        """Return whether this pane's process is still running.

        A pane kept open by `remain-on-exit` after its process died is not alive.
        """
        return not await self._tmux.is_pane_dead(self._pane_id)

    async def paste_line(self, text: str) -> None:
        """Inject `text` as one line: `load-buffer` → `paste-buffer -p` → delay → `Enter`."""
        buffer_name = f"{_INJECT_BUFFER_PREFIX}{self._pane_id.lstrip('%')}"
        await self._tmux.load_buffer(buffer_name, text)
        await self._tmux.paste_buffer(self._pane_id, buffer_name)
        await self._clock.sleep(INJECT_DELAY_SECONDS)
        await self._tmux.send_keys(self._pane_id, ENTER_KEY)

    async def send_keys(self, *keys: str) -> None:
        """Send `keys` to this pane, e.g. an interrupt or exit sequence."""
        await self._tmux.send_keys(self._pane_id, *keys)

    async def kill(self) -> None:
        """Kill this pane outright (03 §4.1 task end, after the exit grace period)."""
        await self._tmux.kill_pane(self._pane_id)
