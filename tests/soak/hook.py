"""The notification hook the soak configures: records each event id, and can be made to hang."""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

LOG_NAME = "notified.log"
STUCK_NAME = "stuck"
FAILING_NAME = "failing"
FAILURE_EXIT = 3
POLL_SECONDS = 0.1


def main(directory: Path) -> None:
    event = json.load(sys.stdin)
    with (directory / LOG_NAME).open("a") as log:
        log.write(f"{event['id']} {event['kind']}\n")
    while (directory / STUCK_NAME).exists():
        threading.Event().wait(POLL_SECONDS)
    if (directory / FAILING_NAME).exists():
        sys.exit(FAILURE_EXIT)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
