"""Registers shared fixtures from `tests/support`."""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from shikiru.process import SubprocessRunner
from tests.support.spawn_hook import spawn_hook
from tests.support.xdist_workers import CPUS_PER_WORKER_ENV, worker_count

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

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


def pytest_xdist_auto_num_workers(config: pytest.Config) -> int:
    """Resolve `-n auto` to half the CPUs, or to the divisor in `SHIKIRU_TEST_CPUS_PER_WORKER`."""
    del config
    cpus = os.process_cpu_count()
    if cpus is None:
        message = "os.process_cpu_count() could not determine the CPU count for -n auto"
        raise RuntimeError(message)
    return worker_count(cpus, os.environ.get(CPUS_PER_WORKER_ENV))


@pytest.fixture(autouse=True)
def clear_fake_cli_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    def isolated(name: str) -> bool:
        return name.startswith("SHIKIRU_FAKE_") or name in {
            "SHIKIRU_TASK",
            "SHIKIRU_PROJECT",
            "SHIKIRU_SLOT",
        }

    inherited = {name: value for name, value in os.environ.items() if isolated(name)}
    for name in inherited:
        del os.environ[name]
    try:
        yield
    finally:
        monkeypatch.undo()
        for name in tuple(os.environ):
            if isolated(name):
                del os.environ[name]
        os.environ.update(inherited)


_DEFAULT_LOCAL_ZONE = "UTC"


@pytest.fixture(autouse=True)
def local_timezone(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[str], None]]:
    """Pin the process's local zone to UTC; the yielded callable switches it for one test."""

    def use(zone: str) -> None:
        monkeypatch.setenv("TZ", zone)
        time.tzset()

    use(_DEFAULT_LOCAL_ZONE)
    yield use
    monkeypatch.undo()
    time.tzset()


@pytest.fixture
def colour_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep Textual from monochrome-filtering renders in tests that assert rendered colours."""
    monkeypatch.delenv("NO_COLOR", raising=False)


@pytest.fixture(autouse=True, scope="session")
def spawn_instrumentation() -> Iterator[None]:
    """Delay and count the test process's spawns when the `SHIKIRU_TEST_SPAWN_*` variables ask."""
    with spawn_hook(os.environ):
        yield


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
