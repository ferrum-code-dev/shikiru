"""tmux and external command notifications (06 §5.7)."""

from __future__ import annotations

import json
import shlex
from typing import TYPE_CHECKING

from shikiru.domain.events import Event, EventKind

if TYPE_CHECKING:
    from shikiru.process import SubprocessRunner
    from shikiru.tmux.client import Tmux

_NOTIFIABLE = frozenset({EventKind.ESCALATION, EventKind.TASK_DONE})


class Notifier:
    """Displays escalation/completion events in tmux and invokes `notify.command`."""

    def __init__(
        self,
        tmux: Tmux,
        runner: SubprocessRunner,
        *,
        session_target: str,
        command: str,
        timeout_seconds: float,
    ) -> None:
        """Use `tmux`, `runner`, the project session and an optional argv command."""
        self._tmux = tmux
        self._runner = runner
        self._session_target = session_target
        self._command = command
        self._timeout_seconds = timeout_seconds

    async def notify(self, event: Event) -> None:
        """Deliver `event` when it is an escalation or task completion."""
        if event.kind not in _NOTIFIABLE:
            return
        await self._tmux.display_message(self._session_target, event.message)
        if not self._command.strip():
            return
        argv = tuple(shlex.split(self._command))
        payload = json.dumps(event.model_dump(mode="json"))
        await self._runner.run(argv, timeout=self._timeout_seconds, input_text=payload)

    async def handle(self, event: Event) -> None:
        """Alias for `notify`, suitable as an event subscriber callback."""
        await self.notify(event)
