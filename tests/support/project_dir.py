"""A project directory outside `$TMPDIR`, which Codex treats as writable."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

E2E_PROJECTS_PARENT = Path.home() / ".cache" / "shikiru-e2e"
E2E_PROJECTS_PREFIX = "p"


@pytest.fixture
def e2e_project_dir() -> Iterator[Path]:
    """Yield a private directory under the user cache and remove it afterwards."""
    E2E_PROJECTS_PARENT.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix=E2E_PROJECTS_PREFIX, dir=E2E_PROJECTS_PARENT))
    try:
        yield directory
    finally:
        shutil.rmtree(directory, ignore_errors=True)
