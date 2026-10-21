"""Shared admission policy for Shikiru-managed tmux client transitions."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.errors import UsageError

if TYPE_CHECKING:
    from shikiru.tmux.client import Tmux

_BUSY_SESSION_MESSAGE = (
    "a terminal is already attached to Shikiru session {session!r}; detach it first, then retry"
)


async def admit_attachment(tmux: Tmux, session: str, current_pane: str | None) -> bool:
    """Return true for a same-client no-op or refuse when another client owns the session."""
    clients = await tmux.session_client_panes(session)
    if not clients:
        return False
    if len(clients) == 1 and current_pane is not None and clients[0] == current_pane:
        return True
    raise UsageError(_BUSY_SESSION_MESSAGE.format(session=session))
