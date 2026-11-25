"""Handlers for engine fault diagnostics."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.domain.commands import DismissFault
from shikiru.engine.handlers.base import require_command_id

if TYPE_CHECKING:
    from shikiru.engine.fault_reporter import FaultReporter


class DismissFaultHandler:
    """Record a dashboard dismissal for the exact fault occurrence in its card."""

    command_type: type[DismissFault] = DismissFault

    def __init__(self, faults: FaultReporter) -> None:
        self._faults = faults

    async def handle(self, command: DismissFault) -> dict[str, object]:
        command_id = require_command_id(command)
        self._faults.dismiss(
            command.scope,
            task_id=command.task_id,
            fault_event_id=command.fault_event_id,
            command_id=command_id,
        )
        return {
            "scope": command.scope,
            "task_id": command.task_id,
            "fault_event_id": command.fault_event_id,
        }
