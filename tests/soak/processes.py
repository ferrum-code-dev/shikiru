"""Finding and ending the processes an arena owns, through psutil."""

from __future__ import annotations

import os
import signal
from contextlib import suppress
from typing import TYPE_CHECKING

import psutil

if TYPE_CHECKING:
    from pathlib import Path

AGENT_MODULE = "fake_agent.turn_cli"
INSPECTION_ERRORS = (psutil.Error, OSError, SystemError)


def _under(path: Path, cwd: str) -> bool:
    return cwd == str(path) or cwd.startswith(f"{path}{os.sep}")


def owned_by(directory: Path) -> list[psutil.Process]:
    """Every live process of this user that runs in, or names, `directory`."""
    found: list[psutil.Process] = []
    for process in psutil.process_iter():
        if process.pid == os.getpid():
            continue
        with suppress(*INSPECTION_ERRORS):
            if str(directory) in " ".join(process.cmdline()) or _under(directory, process.cwd()):
                found.append(process)
    return found


def agents_in(worktree: Path) -> list[psutil.Process]:
    """The fake agent processes whose working directory is inside `worktree`."""
    found: list[psutil.Process] = []
    for process in psutil.process_iter():
        with suppress(*INSPECTION_ERRORS):
            if AGENT_MODULE in " ".join(process.cmdline()) and _under(worktree, process.cwd()):
                found.append(process)
    return found


def group_of(process: psutil.Process) -> int | None:
    try:
        return os.getpgid(process.pid)
    except ProcessLookupError:
        return None


def kill_all(processes: list[psutil.Process]) -> None:
    for process in processes:
        with suppress(psutil.NoSuchProcess, ProcessLookupError):
            group = group_of(process)
            if group is not None and group != os.getpgrp():
                os.killpg(group, signal.SIGKILL)
            process.kill()
