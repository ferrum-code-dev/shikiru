"""Engine-level command handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from shikiru.domain.commands import Stop
from shikiru.engine.handlers.base import reject_wrong_command

if TYPE_CHECKING:
    from shikiru.domain.commands import Command
    from shikiru.engine.handlers.base import HandlerContext


class StopHandler:
    """Request an orderly engine shutdown while leaving agent panes running."""

    command_type: ClassVar[object] = Stop

    def __init__(self, context: HandlerContext) -> None:
        self._context = context

    async def handle(self, command: Command) -> dict[str, object]:
        if not isinstance(command, Stop):
            reject_wrong_command("StopHandler", command)
        self._context.stop_engine()
        return {"stopping": True}
