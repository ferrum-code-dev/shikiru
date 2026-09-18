"""The monorepo path convention shared by task roots and git facts (03 §5)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

MONOREPO_PATH = "."


def is_monorepo_path(repo_path: str) -> bool:
    """Return whether `repo_path` denotes the project root itself, not a subdirectory repo."""
    return repo_path == MONOREPO_PATH


def repo_dir(root: Path, repo_path: str) -> Path:
    """Return `repo_path`'s directory under `root`: `root` itself for the monorepo path."""
    return root if is_monorepo_path(repo_path) else root / repo_path
