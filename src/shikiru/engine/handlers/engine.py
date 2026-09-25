"""Engine-level command handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.domain.commands import Stop

if TYPE_CHECKING:
    from shikiru.engine.handlers.base import HandlerContext


class StopHandler:
    """Request an orderly engine shutdown while leaving agent panes running."""

    command_type: type[Stop] = Stop

    def __init__(self, context: HandlerContext) -> None:
        self._context = context

    async def handle(self, command: Stop) -> dict[str, object]:
        del command
        self._context.stop_engine()
        return {"stopping": True}
