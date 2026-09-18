"""Registers shared fixtures from `tests/support`."""

from __future__ import annotations

pytest_plugins = ["tests.support.tmux", "tests.support.store", "tests.support.engine"]
