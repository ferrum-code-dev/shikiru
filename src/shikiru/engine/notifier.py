"""tmux and external command notifications (06 §5.7)."""

from __future__ import annotations

import json
import logging
import shlex
from typing import TYPE_CHECKING

from shikiru.domain.events import STOP_REASON_KEY, Event, EventKind

if TYPE_CHECKING:
    from shikiru.engine.fault_reporter import FaultReporter
    from shikiru.process import SubprocessRunner
    from shikiru.tmux.client import Tmux

_URGENT = frozenset({EventKind.ESCALATION, EventKind.ENGINE_FAULT})
_NOTIFIABLE = frozenset(
    {
        EventKind.ESCALATION,
        EventKind.TASK_DONE,
        EventKind.ENGINE_FAULT,
        EventKind.INTEGRATION_WAITING,
        EventKind.TAKEOVER_LOST,
    }
)
HOOK_FAULT_SCOPE = "notification hook"


class Notifier:
    """Displays escalation/completion events in tmux and invokes `notify.command`."""

    def __init__(
        self,
        tmux: Tmux,
        runner: SubprocessRunner,
        reporter: FaultReporter,
        *,
        session_target: str,
        command: str,
        timeout_seconds: float,
    ) -> None:
        """Use `tmux`, `runner`, the project session and an optional argv command.

        A failing hook is recorded through `reporter`, once per streak.
        """
        self._tmux = tmux
        self._runner = runner
        self._reporter = reporter
        self._session_target = session_target
        self._command = command
        self._timeout_seconds = timeout_seconds
        self._logger = logging.getLogger(__name__)

    @staticmethod
    def wants(event: Event) -> bool:
        """Return whether `event` is an escalation, completion, fault, merge wait or fault stop."""
        return event.kind in _NOTIFIABLE or (
            event.kind is EventKind.ENGINE_STOPPED and STOP_REASON_KEY in event.data
        )

    @staticmethod
    def is_urgent(event: Event) -> bool:
        """Return whether `event` is an escalation, an engine fault or a fault stop."""
        return event.kind in _URGENT or (
            event.kind is EventKind.ENGINE_STOPPED and STOP_REASON_KEY in event.data
        )

    async def notify(self, event: Event) -> None:
        """Deliver `event` when it is an escalation or task completion."""
        if not self.wants(event):
            return
        try:
            await self._tmux.display_message(self._session_target, event.message)
        except Exception:
            self._logger.exception("tmux notification failed for event %s", event.id)
        if self._command.strip():
            try:
                argv = tuple(shlex.split(self._command))
                payload = json.dumps(event.model_dump(mode="json"))
                await self._runner.run(
                    argv,
                    timeout=self._timeout_seconds,
                    input_text=payload,
                    check=True,
                    own_group=True,
                )
            except Exception as error:
                self._logger.exception("external notification failed for event %s", event.id)
                self._reporter.report(error, scope=HOOK_FAULT_SCOPE)
            else:
                self._reporter.resolve(HOOK_FAULT_SCOPE)
