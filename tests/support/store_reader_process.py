"""Child process for store cross-process tests: watches for a commit from the parent."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from shikiru.clock import SystemClock
from shikiru.store.database import Database
from shikiru.store.reader import StateReader

_WATCH_TIMEOUT_SECONDS = 30.0


async def _wait_for_one_change(reader: StateReader, ready_path: Path) -> None:
    changed = asyncio.Event()

    async def on_change() -> None:
        changed.set()

    watcher = asyncio.ensure_future(reader.watch(on_change))
    asyncio.get_running_loop().call_soon(ready_path.write_text, "ready")
    try:
        await asyncio.wait_for(changed.wait(), timeout=_WATCH_TIMEOUT_SECONDS)
    finally:
        watcher.cancel()


async def _main(db_path: str, ready_path: str) -> None:
    database = Database(Path(db_path))
    reader = StateReader(database, SystemClock())
    try:
        await _wait_for_one_change(reader, Path(ready_path))
        print("changed")
    finally:
        reader.close()


if __name__ == "__main__":
    asyncio.run(_main(sys.argv[1], sys.argv[2]))
