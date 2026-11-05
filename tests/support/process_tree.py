"""A parent process whose descendant shares its stdio and ignores SIGTERM."""

from __future__ import annotations

import os
import signal
import sys
from contextlib import suppress
from typing import TYPE_CHECKING

import pytest

from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

PID_WAIT_SECONDS = 10.0

_DESCENDANT = (
    "import pathlib, signal, sys, threading; "
    "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
    "pathlib.Path(sys.argv[1]).write_text('ready'); "
    "threading.Event().wait()"
)
_PARENT = (
    "import pathlib, subprocess, sys, threading; "
    f"child = subprocess.Popen([sys.executable, '-c', {_DESCENDANT!r}, sys.argv[2]]); "
    "pathlib.Path(sys.argv[1]).write_text(str(child.pid)); "
    "threading.Event().wait()"
)
_PARENT_IGNORING_TERM = (
    "import pathlib, signal, subprocess, sys, threading; "
    "signal.signal(signal.SIGTERM, lambda *_: pathlib.Path(sys.argv[3]).write_text('received')); "
    f"child = subprocess.Popen([sys.executable, '-c', {_DESCENDANT!r}, sys.argv[2]]); "
    "pathlib.Path(sys.argv[1]).write_text(str(child.pid)); "
    "threading.Event().wait()"
)


class ProcessTree:
    """A parent that exits on SIGTERM and a descendant that ignores it, both holding stdio."""

    def __init__(self, pid_file: Path) -> None:
        self._pid_file = pid_file
        self._ready_file = pid_file.with_suffix(".ready")
        self._signal_file = pid_file.with_suffix(".signal")
        self.parent_pid: int | None = None

    @property
    def argv(self) -> list[str]:
        return [sys.executable, "-c", _PARENT, str(self._pid_file), str(self._ready_file)]

    @property
    def ignoring_term_argv(self) -> list[str]:
        return [
            sys.executable,
            "-c",
            _PARENT_IGNORING_TERM,
            str(self._pid_file),
            str(self._ready_file),
            str(self._signal_file),
        ]

    def received_term(self) -> bool:
        return self._signal_file.exists()

    @property
    def descendant_pid(self) -> int:
        return int(self._pid_file.read_text())

    async def descendant_started(self) -> int:
        await wait_until(
            lambda: (
                self._pid_file.exists()
                and bool(self._pid_file.read_text())
                and self._ready_file.exists()
            ),
            PID_WAIT_SECONDS,
        )
        return self.descendant_pid

    def kill_all(self) -> None:
        if self._pid_file.exists() and (text := self._pid_file.read_text()):
            with suppress(ProcessLookupError):
                os.kill(int(text), signal.SIGKILL)
        if self.parent_pid is not None:
            with suppress(ProcessLookupError):
                os.kill(self.parent_pid, signal.SIGKILL)


@pytest.fixture
def process_tree(tmp_path: Path) -> Iterator[ProcessTree]:
    tree = ProcessTree(tmp_path / "descendant.pid")
    yield tree
    tree.kill_all()
