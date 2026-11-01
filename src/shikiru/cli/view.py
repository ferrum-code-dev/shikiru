"""`shikiru view`: a role pane's read-only live tail of its `view.jsonl`."""

from __future__ import annotations

import asyncio
import json
import sys
from typing import TYPE_CHECKING, Annotated

import typer
from rich.console import Console

from shikiru.cli.context import project_root, state_from
from shikiru.clock import SystemClock, to_local
from shikiru.domain.tasks import Role
from shikiru.paths import run_dir
from shikiru.ui.quota_status import quota_status_text
from shikiru.ui.status_strip import StatusStrip
from shikiru.ui.stream import StreamLine
from shikiru.ui.tokens import AMBER
from shikiru.view_log import quota_status_path, read_quota_status, view_log_path

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.clock import Clock

_LINE_END = b"\n"
_VIEW_POLL_SECONDS = 0.5


class ViewTail:
    """Follows one `view.jsonl`, printing each complete row through a `StreamLine`.

    While `status_path` exists, its quota wait shows as a status strip above the output.
    """

    def __init__(
        self,
        path: Path,
        stream: StreamLine,
        console: Console,
        clock: Clock,
        *,
        status_path: Path,
        strip: StatusStrip,
    ) -> None:
        """Tail `path` from its start, rendering with `stream` onto `console`."""
        self._path = path
        self._stream = stream
        self._console = console
        self._clock = clock
        self._status_path = status_path
        self._strip = strip
        self._offset = 0

    async def follow(self) -> None:
        """Print rows as they are appended, polling through the clock until cancelled."""
        while True:
            self.drain()
            self.sync_status()
            await self._clock.sleep(_VIEW_POLL_SECONDS)

    def sync_status(self) -> None:
        """Show the published quota wait in the strip, or hide the strip when there is none."""
        status = read_quota_status(self._status_path)
        if status is None:
            self._strip.sync(None)
            return
        action, at = status
        now = to_local(self._clock.now())
        self._strip.sync(quota_status_text(action, to_local(at) if at is not None else None, now))

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
    """Render `role`'s live view of `task_id`."""
    root = project_root(state_from(context), require_config=False)
    console = Console()
    tail = ViewTail(
        view_log_path(run_dir(root, task_id), role.value),
        StreamLine(role),
        console,
        SystemClock(),
        status_path=quota_status_path(run_dir(root, task_id), role.value),
        strip=StatusStrip(console, sys.stdin, AMBER),
    )
    asyncio.run(tail.follow())
