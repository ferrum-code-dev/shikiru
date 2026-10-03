"""Atomic local file writing for operator evidence exports."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import threading

TEMP_FILE_PREFIX = ".shikiru-evidence-"
FILE_ENCODING = "utf-8"
FILE_MODE = 0o600
WRITE_CANCELLED_MESSAGE = "evidence export was cancelled before it was written"
CURRENT_DIRECTORY_NAME = "."
PARENT_DIRECTORY_NAME = ".."


class LocalEvidenceWriter:
    """Write one export under an explicit local destination with atomic publication."""

    def __init__(self, project_root: Path) -> None:
        """Resolve relative destinations from the initialized project root."""
        self._project_root = project_root

    def write(
        self,
        destination: str,
        content: str,
        *,
        overwrite: bool,
        cancelled: threading.Event,
    ) -> Path:
        """Publish the complete UTF-8 export and return its resolved destination."""
        path = self._destination(destination)
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=TEMP_FILE_PREFIX,
            dir=path.parent,
        )
        temporary_path = Path(temporary_name)
        cancelled_before_publish = False
        try:
            with os.fdopen(file_descriptor, "w", encoding=FILE_ENCODING) as output:
                os.fchmod(output.fileno(), FILE_MODE)
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            if cancelled.is_set():
                cancelled_before_publish = True
            elif overwrite:
                temporary_path.replace(path)
            else:
                path.hardlink_to(temporary_path)
                temporary_path.unlink()
        except Exception as error:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError as cleanup_error:
                raise cleanup_error from error
            raise
        if cancelled_before_publish:
            temporary_path.unlink(missing_ok=True)
            raise InterruptedError(WRITE_CANCELLED_MESSAGE)
        return path

    def _destination(self, destination: str) -> Path:
        selected = Path(destination)
        target = selected if selected.is_absolute() else self._project_root / selected
        parent = target.parent.resolve(strict=True)
        if not parent.is_dir():
            message = f"destination parent is not a directory: {parent}"
            raise NotADirectoryError(message)
        if not target.name or target.name in {CURRENT_DIRECTORY_NAME, PARENT_DIRECTORY_NAME}:
            message = f"destination must name a file: {target}"
            raise ValueError(message)
        return parent / target.name
