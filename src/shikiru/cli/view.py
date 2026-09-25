"""`shikiru view`: a role pane's read-only live tail of its `view.jsonl` (06 §5.1)."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Annotated

import typer
from rich.console import Console

from shikiru.agents.contracts import view_log_path
from shikiru.cli.context import project_root, state_from
from shikiru.clock import SystemClock
from shikiru.domain.tasks import Role
from shikiru.paths import run_dir
from shikiru.store.reader import DASHBOARD_CHANGE_POLL_SECONDS
from shikiru.ui.stream import StreamLine

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.clock import Clock

_LINE_END = b"\n"


class ViewTail:
    """Follows one `view.jsonl`, printing each complete row through a `StreamLine`."""

    def __init__(self, path: Path, stream: StreamLine, console: Console, clock: Clock) -> None:
        """Tail `path` from its start, rendering with `stream` onto `console`."""
        self._path = path
        self._stream = stream
        self._console = console
        self._clock = clock
        self._offset = 0

    async def follow(self) -> None:
        """Print rows as they are appended, polling through the clock until cancelled."""
        while True:
            self.drain()
            await self._clock.sleep(DASHBOARD_CHANGE_POLL_SECONDS)

    def drain(self) -> None:
        """Print every complete row appended since the last drain."""
        if not self._path.is_file():
            return
        with self._path.open("rb") as log:
            log.seek(self._offset)
            chunk = log.read()
        complete = chunk[: chunk.rfind(_LINE_END) + 1]
        self._offset += len(complete)
        for raw in complete.splitlines():
            for line in self._stream.render(json.loads(raw)):
                self._console.print(line)


def view_command(
    context: typer.Context,
    task_id: Annotated[str, typer.Argument()],
    role: Annotated[Role, typer.Argument()],
) -> None:
    """Render `role`'s live view of `task_id` (06 §5.1)."""
    root = project_root(state_from(context))
    path = view_log_path(run_dir(root, task_id), role.value)
    tail = ViewTail(path, StreamLine(role), Console(), SystemClock())
    asyncio.run(tail.follow())
