"""Disk space and directory usage for the dashboard's Disk section."""

from __future__ import annotations

import asyncio
import os
import shutil
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING

from shikiru.paths import project_state_dir

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
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
    return DirectoryUsage(directories, sum(stat.st_size for stat in _file_stats(root, None)))


def tree_size(root: Path) -> int:
    """Return the apparent bytes of every file beneath `root`; a missing `root` is zero.

    Symlinks are not followed, a hard-linked file counts once, and an unreadable subtree raises
    `OSError` instead of being skipped.
    """
    if not root.is_dir():
        return 0
    linked: set[tuple[int, int]] = set()
    size = 0
    for stat in _file_stats(root, _raise):
        if stat.st_nlink > 1:
            identity = (stat.st_dev, stat.st_ino)
            if identity in linked:
                continue
            linked.add(identity)
        size += stat.st_size
    return size


def _raise(error: OSError) -> None:
    raise error


def _file_stats(root: Path, onerror: Callable[[OSError], None] | None) -> Iterator[os.stat_result]:
    for parent, _, names in os.walk(root, onerror=onerror):
        for name in names:
            with suppress(FileNotFoundError):
                yield os.lstat(os.path.join(parent, name))  # noqa: PTH118


@dataclass(frozen=True)
class DiskSnapshot:
    """The latest disk observation; sizes and `sampled_at` survive a failed later sample."""

    free_bytes: int | None = None
    total_bytes: int | None = None
    shikiru_bytes: int | None = None
    sampled_at: datetime | None = None
    error: str | None = None
    failed: bool = False


class DiskSampler:
    """Measure the project filesystem and its `.shikiru` folder off the event loop.

    Free space is measured every `refresh_seconds`; the folder size is walked on its own, slower
    cadence in a single background flight so a long walk never delays the free-space reading.
    """

    def __init__(
        self,
        clock: Clock,
        project_root: Path,
        *,
        refresh_seconds: Callable[[], float],
        size_refresh_seconds: Callable[[], float],
    ) -> None:
        """Measure `project_root` at most once per cadence of `clock` time."""
        self._clock = clock
        self._project_root = project_root
        self._shikiru_root = project_state_dir(project_root)
        self._refresh_seconds = refresh_seconds
        self._size_refresh_seconds = size_refresh_seconds
        self._attempted_at: datetime | None = None
        self._size_finished_at: datetime | None = None
        self._size_task: asyncio.Task[None] | None = None
        self._free_bytes: int | None = None
        self._total_bytes: int | None = None
        self._sampled_at: datetime | None = None
        self._shikiru_bytes: int | None = None
        self._free_error: str | None = None
        self._size_error: str | None = None
        self._lock = asyncio.Lock()

    async def sample_if_due(self) -> DiskSnapshot:
        """Return the cached observation, measuring again once a cadence has passed."""
        self._start_size_walk_if_due()
        async with self._lock:
            if self._is_due(self._attempted_at, self._refresh_seconds):
                await self._sample_free_space()
            return self._snapshot()

    def _is_due(self, since: datetime | None, interval: Callable[[], float]) -> bool:
        return since is None or (self._clock.now() - since).total_seconds() >= interval()

    def _start_size_walk_if_due(self) -> None:
        if self._size_task is not None and not self._size_task.done():
            return
        if self._is_due(self._size_finished_at, self._size_refresh_seconds):
            self._size_task = asyncio.create_task(self._walk_size())

    async def _walk_size(self) -> None:
        try:
            self._shikiru_bytes = await asyncio.to_thread(tree_size, self._shikiru_root)
            self._size_error = None
        except OSError as error:
            self._size_error = str(error)
        self._size_finished_at = self._clock.now()

    async def _sample_free_space(self) -> None:
        now = self._clock.now()
        self._attempted_at = now
        try:
            self._free_bytes, self._total_bytes = await asyncio.to_thread(
                _filesystem, self._project_root
            )
        except OSError as error:
            self._free_error = str(error)
            return
        self._free_error = None
        self._sampled_at = now

    def _snapshot(self) -> DiskSnapshot:
        return DiskSnapshot(
            self._free_bytes,
            self._total_bytes,
            self._shikiru_bytes,
            self._sampled_at,
            self._free_error or self._size_error,
            failed=self._free_error is None and self._size_error is not None,
        )


def _filesystem(root: Path) -> tuple[int, int]:
    usage = shutil.disk_usage(root)
    return usage.free, usage.total
