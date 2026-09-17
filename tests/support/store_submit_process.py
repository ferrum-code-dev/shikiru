"""Child process for store concurrency tests: appends `count` `Submit` commands."""

from __future__ import annotations

import sys
from pathlib import Path

from shikiru.clock import SystemClock
from shikiru.domain.commands import Submit
from shikiru.store.commands import CommandQueue
from shikiru.store.database import Database


def main(db_path: str, count: str) -> None:
    """Append `count` `Submit` commands to `db_path`, printing each allocated task id."""
    connection = Database(Path(db_path)).open_writer()
    queue = CommandQueue(connection, SystemClock())
    for _ in range(int(count)):
        queued = queue.append(Submit(task_id="", title="t", request="r"))
        assert isinstance(queued.command, Submit)
        print(queued.command.task_id)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
