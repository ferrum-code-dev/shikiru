"""Copy-on-write cache cloning into a task worktree (03 §5)."""

from __future__ import annotations

import platform
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from collections.abc import Sequence

    from shikiru.process import SubprocessRunner

_COPY_TIMEOUT_SECONDS = 60.0
_PROBE_PREFIX = ".shikiru-cow-probe-"
_PROBE_DIRECTORY_PREFIX = ".shikiru-cow-check-"
_PROBE_CONTENT = b"shikiru cow probe"

CACHE_COW_UNSUPPORTED_WARNING = (
    "copy-on-write copy is not supported on this filesystem; caches were not cloned"
)

_MACOS_COW_ARGS = ("cp", "-c", "-R")
_LINUX_COW_ARGS = ("cp", "-a", "--reflink=always")
_COW_ARGS_BY_SYSTEM = {"Darwin": _MACOS_COW_ARGS, "Linux": _LINUX_COW_ARGS}


def default_cow_args() -> tuple[str, ...] | None:
    """Return this platform's copy-on-write `cp` invocation, or `None` if none is known."""
    return _COW_ARGS_BY_SYSTEM.get(platform.system())


@dataclass(frozen=True, slots=True)
class CacheCloneResult:
    """The outcome of one `CacheCloner.clone` call."""

    copied: tuple[Path, ...] = field(default_factory=tuple)
    warning: str | None = None


class CacheCloner:
    """Clone `repo.caches` globs that are git-ignored, copy-on-write, into a worktree."""

    def __init__(self, runner: SubprocessRunner, *, cow_args: Sequence[str] | None) -> None:
        """Copy with `cow_args` (the platform's copy-on-write `cp` invocation), via `runner`.

        `cow_args` is `None` when this platform has no known copy-on-write `cp` invocation
        (see `default_cow_args`); cloning then reports the filesystem as unsupported outright.
        """
        self._runner = runner
        self._cow_args = tuple(cow_args) if cow_args is not None else None

    async def clone(
        self,
        repo_dir: Path,
        worktree_dir: Path,
        cache_globs: Sequence[str],
        ignored: Sequence[Path],
    ) -> CacheCloneResult:
        """Copy every ignored path in `repo_dir` matching `cache_globs` into `worktree_dir`.

        Only paths that exist and are in `ignored` are copied. If this filesystem does not
        support copy-on-write, nothing is copied and exactly one warning is returned.
        """
        cow_args = self._cow_args
        if cow_args is None or not await self._cow_supported(cow_args, worktree_dir):
            return CacheCloneResult(warning=CACHE_COW_UNSUPPORTED_WARNING)
        ignored_set = set(ignored)
        copied: list[Path] = []
        for pattern in cache_globs:
            for match in sorted(repo_dir.glob(pattern)):
                if match not in ignored_set or not match.exists():
                    continue
                relative = match.relative_to(repo_dir)
                destination = worktree_dir / relative
                if destination.exists():
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                await self._copy(cow_args, match, destination)
                copied.append(relative)
        return CacheCloneResult(copied=tuple(copied))

    async def check_support(self, filesystem_root: Path) -> bool:
        """Check copy-on-write using disposable probe files on `filesystem_root`."""
        if self._cow_args is None:
            return False
        with tempfile.TemporaryDirectory(
            prefix=_PROBE_DIRECTORY_PREFIX, dir=filesystem_root
        ) as directory:
            return await self._cow_supported(self._cow_args, Path(directory))

    async def _cow_supported(self, cow_args: tuple[str, ...], worktree_dir: Path) -> bool:
        probe_source = worktree_dir / f"{_PROBE_PREFIX}{uuid4().hex}"
        probe_destination = worktree_dir / f"{_PROBE_PREFIX}{uuid4().hex}-copy"
        probe_source.write_bytes(_PROBE_CONTENT)
        try:
            result = await self._runner.run(
                [*cow_args, str(probe_source), str(probe_destination)],
                timeout=_COPY_TIMEOUT_SECONDS,
            )
        finally:
            probe_source.unlink(missing_ok=True)
            probe_destination.unlink(missing_ok=True)
        return result.returncode == 0

    async def _copy(self, cow_args: tuple[str, ...], source: Path, destination: Path) -> None:
        await self._runner.run(
            [*cow_args, str(source), str(destination)],
            timeout=_COPY_TIMEOUT_SECONDS,
            check=True,
        )
