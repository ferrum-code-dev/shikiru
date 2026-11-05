"""Wait for a subprocess to block on a FIFO before releasing it."""

from __future__ import annotations

import errno
import os
from contextlib import suppress
from typing import TYPE_CHECKING

from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from pathlib import Path


async def fifo_writer(path: Path, timeout: float) -> int:
    """Open a nonblocking writer only after the subprocess is waiting to read."""
    descriptor: int | None = None

    def ready() -> bool:
        nonlocal descriptor
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
        except OSError as error:
            if error.errno != errno.ENXIO:
                raise
            return False
        return True

    await wait_until(ready, timeout)
    assert descriptor is not None
    return descriptor


def release_fifo(descriptor: int, path: Path) -> None:
    """Release the waiting reader and leave later readers an immediately readable file."""
    with suppress(BrokenPipeError):
        os.write(descriptor, b"go\n")
    os.close(descriptor)
    path.unlink()
    path.write_text("go\n")
