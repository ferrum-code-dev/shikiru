"""Disk usage of a directory tree (05 §5)."""

from __future__ import annotations

import os
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


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
