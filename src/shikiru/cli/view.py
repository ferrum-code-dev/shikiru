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
_VIEW_READ_BYTES = 65_536


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
        self._pending_bytes = b""
        self._size = (console.size.width, console.size.height)

    async def follow(self) -> None:
        """Print rows as they are appended, polling through the clock until cancelled."""
        while True:
            size = self._console.size
            resized = (size.width, size.height) != self._size and self._strip.active
            if resized:
                self._reset_for_replay()
                self.sync_status()
            while self.drain():
                await self._clock.sleep(0)
            if not resized and self.sync_status():
                self._reset_for_replay()
                self.sync_status()
                while self.drain():
                    await self._clock.sleep(0)
            size = self._console.size
            self._size = (size.width, size.height)
            await self._clock.sleep(_VIEW_POLL_SECONDS)

    def sync_status(self) -> bool:
        """Show the published quota wait and report whether output needs replaying."""
        status = read_quota_status(self._status_path)
        if status is None:
            return self._strip.sync(None)
        action, at = status
        now = to_local(self._clock.now())
        return self._strip.sync(
            quota_status_text(action, to_local(at) if at is not None else None, now)
        )

    def _reset_for_replay(self) -> None:
        self._strip.reset_for_replay()
        self._stream.reset()
        self._offset = 0
        self._pending_bytes = b""

    def drain(self) -> bool:
        """Print complete rows from one bounded chunk; return whether more may remain."""
        if not self._path.is_file():
            return False
        with self._path.open("rb") as log:
            log.seek(self._offset)
            chunk = log.read(_VIEW_READ_BYTES)
        if not chunk:
            return False
        self._offset += len(chunk)
        combined = self._pending_bytes + chunk
        complete = combined[: combined.rfind(_LINE_END) + 1]
        self._pending_bytes = combined[len(complete) :]
        for raw in complete.splitlines():
            for line in self._stream.render(json.loads(raw)):
                self._console.print(line)
        return len(chunk) == _VIEW_READ_BYTES


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
