"""Registers shared fixtures from `tests/support`."""

from __future__ import annotations

import os

import pytest

pytest_plugins = ["tests.support.tmux", "tests.support.store", "tests.support.engine"]


@pytest.fixture(autouse=True)
def clear_fake_cli_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in tuple(os.environ):
        if name.startswith("SHIKIRU_FAKE_SCRIPT") or name == "SHIKIRU_FAKE_TICK_A1":
            monkeypatch.delenv(name)
