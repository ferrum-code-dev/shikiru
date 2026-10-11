"""Registers shared fixtures from `tests/support`."""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from shikiru.process import SubprocessRunner

if TYPE_CHECKING:
    from collections.abc import Iterator

_TMUX_DIR_PARENT = "/tmp"  # noqa: S108
_TMUX_DEFAULT_SOCKET = "default"

pytest_plugins = [
    "tests.support.tmux",
    "tests.support.store",
    "tests.support.engine",
    "tests.support.github",
    "tests.support.process_tree",
    "tests.support.project_dir",
]


@pytest.fixture(autouse=True)
def clear_fake_cli_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in tuple(os.environ):
        if name.startswith("SHIKIRU_FAKE_SCRIPT") or name == "SHIKIRU_FAKE_TICK_A1":
            monkeypatch.delenv(name)


@pytest.fixture
def colour_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep Textual from monochrome-filtering renders in tests that assert rendered colours."""
    monkeypatch.delenv("NO_COLOR", raising=False)


@pytest.fixture(autouse=True, scope="session")
def private_tmux_directory() -> Iterator[Path]:
    """Keep every tmux the tests reach off the user's default socket; fail if one starts there."""
    directory = Path(tempfile.mkdtemp(prefix="shk-tmux-", dir=_TMUX_DIR_PARENT))
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("TMUX_TMPDIR", str(directory))
        for name in ("TMUX", "TMUX_PANE"):
            patch.delenv(name, raising=False)
        yield directory
        leaked = list(directory.glob(f"tmux-*/{_TMUX_DEFAULT_SOCKET}"))
        if leaked:
            asyncio.run(SubprocessRunner().run(["tmux", "kill-server"], timeout=5, check=False))
        shutil.rmtree(directory, ignore_errors=True)
    if leaked:
        message = "a test started a tmux server on the default socket"
        raise RuntimeError(message)
