"""Disk space and directory usage for the dashboard's Disk section (docs/design/05 §5)."""

from __future__ import annotations

import asyncio
import os
import shutil
from contextlib import suppress
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from shikiru.paths import runs_root

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

    from shikiru.clock import Clock


@dataclass(frozen=True, slots=True)
class DirectoryUsage:
    """The directories directly under a root and the bytes of every file beneath it."""

    directories: int
    size_bytes: int


def directory_usage(root: Path) -> DirectoryUsage:
    """Measure `root`, following no symlink; a missing `root` measures as empty.

    A file removed while the tree is walked (by retention) counts for nothing.
    """
    if not root.is_dir():
        return DirectoryUsage(0, 0)
    directories = sum(1 for entry in root.iterdir() if entry.is_dir())
    size = 0
    for parent, _, names in os.walk(root):
        for name in names:
            with suppress(FileNotFoundError):
                size += os.lstat(os.path.join(parent, name)).st_size  # noqa: PTH118
    return DirectoryUsage(directories, size)


@dataclass(frozen=True)
class DiskSnapshot:
    """The latest disk observation; sizes and `sampled_at` survive a failed later sample."""

    free_bytes: int | None = None
    total_bytes: int | None = None
    runs_bytes: int | None = None
    sampled_at: datetime | None = None
    error: str | None = None
    failed: bool = False


class DiskSampler:
    """Measure the project filesystem and its run directory off the event loop, once per cadence."""

    def __init__(self, clock: Clock, project_root: Path, *, refresh_seconds: float) -> None:
        """Measure `project_root` at most once per `refresh_seconds` of `clock` time."""
        self._clock = clock
        self._project_root = project_root
        self._runs_root = runs_root(project_root)
        self._refresh_seconds = refresh_seconds
        self._attempted_at: datetime | None = None
        self._last = DiskSnapshot()
        self._lock = asyncio.Lock()

    async def sample_if_due(self) -> DiskSnapshot:
        """Return the cached observation, measuring again once the refresh interval has passed."""
        async with self._lock:
            attempted_at = self._attempted_at
            if (
                attempted_at is None
                or (self._clock.now() - attempted_at).total_seconds() >= self._refresh_seconds
            ):
                await self._sample()
            return self._last

    async def _sample(self) -> None:
        now = self._clock.now()
        self._attempted_at = now
        try:
            free_bytes, total_bytes = await asyncio.to_thread(_filesystem, self._project_root)
        except OSError as error:
            self._last = replace(self._last, error=str(error), failed=False)
            return
        try:
            runs = await asyncio.to_thread(directory_usage, self._runs_root)
        except OSError as error:
            self._last = replace(self._last, error=str(error), failed=True)
            return
        self._last = DiskSnapshot(free_bytes, total_bytes, runs.size_bytes, now)


def _filesystem(root: Path) -> tuple[int, int]:
    usage = shutil.disk_usage(root)
    return usage.free, usage.total
