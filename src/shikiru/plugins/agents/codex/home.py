"""Codex's local session data directory."""

from __future__ import annotations

import os
from pathlib import Path

from shikiru.plugins.agents.codex.options import CODEX_HOME_ENV_VAR


def codex_home() -> Path:
    """Return the Codex home used for rollout discovery."""
    configured = os.environ.get(CODEX_HOME_ENV_VAR)
    return Path(configured) if configured else Path.home() / ".codex"
