"""Child process for gate tests: spawns a marker-writing process, then dies by SIGKILL."""

from __future__ import annotations

import asyncio
import os
import signal
import sys

from shikiru.process import SubprocessRunner

_COMMAND_FLAG = "-c"
_MARKER_PROGRAM = "import pathlib, sys, time; pathlib.Path(sys.argv[1]).touch(); time.sleep(300)"


def _die_after_reporting(pid: int) -> None:
    sys.stdout.write(f"{pid}\n")
    sys.stdout.flush()
    os.kill(os.getpid(), signal.SIGKILL)


async def main(marker: str) -> None:
    """Spawn the process, whose `on_started` reports its id and kills this process."""
    await SubprocessRunner().spawn(
        [sys.executable, _COMMAND_FLAG, _MARKER_PROGRAM, marker], on_started=_die_after_reporting
    )


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
