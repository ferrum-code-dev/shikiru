"""The release store: retained release wheels and the `current` file that names the last one."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from shikiru.operator.lifecycle import PACKAGE_NAME
from shikiru.update.errors import ReleaseStepError

DATA_HOME_ENVIRONMENT = "XDG_DATA_HOME"
DEFAULT_DATA_HOME = Path(".local", "share")
RELEASES_DIRECTORY = "releases"
CURRENT_NAME = "current"
TEMPORARY_CURRENT_NAME = "current.tmp"
RETAINED_WHEELS = 5
WHEEL_GLOB = "*.whl"
WHEEL_SUFFIX = ".whl"
WHEEL_TAGS = "py3-none-any"
NAME_SEPARATOR = "-"
VERSION_FIELD = 1


def release_store() -> Path:
    """Return the directory that holds release wheels under `$XDG_DATA_HOME`."""
    configured = os.environ.get(DATA_HOME_ENVIRONMENT)
    data_home = Path(configured) if configured else Path.home() / DEFAULT_DATA_HOME
    return data_home / PACKAGE_NAME / RELEASES_DIRECTORY


def wheel_name(version: str) -> str:
    """Return the file name of the Shikiru wheel for `version`."""
    return f"{PACKAGE_NAME}{NAME_SEPARATOR}{version}{NAME_SEPARATOR}{WHEEL_TAGS}{WHEEL_SUFFIX}"


def wheel_version(wheel: Path) -> str:
    """Return the version a Shikiru wheel file name carries, refusing other files."""
    if not wheel.is_file():
        message = f"no wheel at {wheel}"
        raise ReleaseStepError(message)
    if not wheel.name.startswith(f"{PACKAGE_NAME}{NAME_SEPARATOR}") or wheel.suffix != WHEEL_SUFFIX:
        message = f"{wheel.name} is not a {PACKAGE_NAME} wheel"
        raise ReleaseStepError(message)
    return wheel.name.split(NAME_SEPARATOR)[VERSION_FIELD]


@dataclass(frozen=True)
class ReleaseStore:
    """The retained release wheels and the `current` file that names the last one installed."""

    root: Path

    @property
    def current_file(self) -> Path:
        return self.root / CURRENT_NAME

    def add(self, wheel: Path) -> Path:
        """Copy `wheel` into the store, unless it is already there, and return its stored path."""
        self.root.mkdir(parents=True, exist_ok=True)
        stored = self.root / wheel.name
        if wheel.resolve() != stored.resolve():
            shutil.copyfile(wheel, stored)
        return stored

    def read_current(self) -> Path | None:
        """Return the stored wheel `current` names, or None before the first install."""
        if not self.current_file.is_file():
            return None
        name = self.current_file.read_text(encoding="utf-8").strip()
        wheel = self.root / name
        if not wheel.is_file():
            message = f"{CURRENT_NAME} names {name}, which is not in {self.root}"
            raise ReleaseStepError(message)
        return wheel

    def write_current(self, wheel: Path) -> None:
        """Make `current` name `wheel`, replacing the file in one step."""
        temporary = self.root / TEMPORARY_CURRENT_NAME
        temporary.write_text(f"{wheel.name}\n", encoding="utf-8")
        temporary.replace(self.current_file)

    def prune(self, keep: Path) -> None:
        """Delete all but the newest wheels, always keeping `keep`."""
        wheels = sorted(self.root.glob(WHEEL_GLOB), key=lambda wheel: wheel.stat().st_mtime)
        for stale in wheels[:-RETAINED_WHEELS]:
            if stale != keep:
                stale.unlink()
