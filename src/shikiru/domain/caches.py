"""Cache path rules shared by `init` detection and Doctor suggestions."""

from __future__ import annotations

from enum import StrEnum
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Sequence

CACHE_NAMES = (
    ".venv",
    "node_modules",
    "target",
    ".gradle",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".next",
    ".nuxt",
    ".dart_tool",
    "vendor",
)


class CacheEntryEffect(StrEnum):
    """Why a configured cache entry copies nothing into task worktrees."""

    MATCHES_NOTHING = "matches_nothing"
    NOT_GIT_IGNORED = "not_git_ignored"


def detected_caches(ignored_paths: Iterable[str]) -> tuple[str, ...]:
    """Return the Git-ignored project-relative paths whose name is a known cache, sorted."""
    return tuple(sorted(path for path in ignored_paths if PurePosixPath(path).name in CACHE_NAMES))


def uncached_detections(detected: Sequence[str], copied: Collection[str]) -> tuple[str, ...]:
    """Return the detected cache paths that no configured cache pattern matches.

    `copied` holds every project-relative path that a configured pattern matches, so a
    trailing slash or a glob that already covers a detected cache is not reported as missing.
    """
    return tuple(path for path in detected if path not in copied)


def ineffective_cache_entry(
    matches: Sequence[str], ignored_paths: Collection[str]
) -> CacheEntryEffect | None:
    """Return why a cache entry has no effect, or `None` when one of its matches is copied.

    `matches` are the project-relative paths the entry's glob matches; a match copies only when
    Git ignores it.
    """
    if not matches:
        return CacheEntryEffect.MATCHES_NOTHING
    if any(match in ignored_paths for match in matches):
        return None
    return CacheEntryEffect.NOT_GIT_IGNORED
