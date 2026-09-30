"""Owner-only creation and tightening of everything Shikiru keeps under `.shikiru/` (01 §6)."""

from __future__ import annotations

import os
import stat
from typing import IO, TYPE_CHECKING, Any

from shikiru.paths import WORKTREES_DIR_NAME

if TYPE_CHECKING:
    from pathlib import Path

PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
_OTHERS_MASK = 0o077
_ENCODING = "utf-8"


def make_private_dir(path: Path) -> None:
    """Create `path` and its missing parents as owner-only directories."""
    for directory in reversed([path, *path.parents]):
        if not directory.exists():
            directory.mkdir(mode=PRIVATE_DIR_MODE)


def create_private_file(path: Path) -> None:
    """Create the empty file `path` as owner-only unless it exists; its directory must exist."""
    os.close(os.open(path, os.O_WRONLY | os.O_CREAT, PRIVATE_FILE_MODE))


def write_private_text(path: Path, text: str) -> None:
    """Write `text` to `path`, creating it owner-only."""
    with _open(path, os.O_TRUNC, "w") as handle:
        handle.write(text)


def append_private_text(path: Path, text: str) -> None:
    """Append `text` to `path`, creating it owner-only."""
    with _open(path, os.O_APPEND, "a") as handle:
        handle.write(text)


def append_private_bytes(path: Path, content: bytes) -> None:
    """Append `content` to `path`, creating it owner-only."""
    with _open(path, os.O_APPEND, "ab") as handle:
        handle.write(content)


def tighten_state_dir(state_dir: Path) -> list[Path]:
    """Remove group and other access below `state_dir` and return the paths it changed.

    Task worktrees hold the project's own files, so only the `worktrees/` directory itself is
    tightened. Symlinks are never followed or changed.
    """
    changed: list[Path] = []
    for directory, subdirectories, names in state_dir.walk():
        entries = [directory]
        if directory.name == WORKTREES_DIR_NAME and directory.parent == state_dir:
            subdirectories.clear()
        else:
            entries.extend(directory / name for name in names)
        changed.extend(entry for entry in entries if not entry.is_symlink() and _restrict(entry))
    return changed


def _open(path: Path, flag: int, mode: str) -> IO[Any]:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | flag, PRIVATE_FILE_MODE)
    return os.fdopen(descriptor, mode, encoding=None if "b" in mode else _ENCODING)


def _restrict(path: Path) -> bool:
    mode = stat.S_IMODE(path.stat().st_mode)
    restricted = PRIVATE_DIR_MODE if path.is_dir() else mode & ~_OTHERS_MASK
    if restricted == mode:
        return False
    path.chmod(restricted)
    return True
