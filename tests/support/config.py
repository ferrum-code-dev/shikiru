"""The minimal project config config-loader and config-settings tests both need."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

MINIMAL_PROJECT_CONFIG = """
[project]
name = "demo"

[roles.leader]
ladder = [ { agent = "claude", model = "opus", effort = "high" } ]

[roles.implementer]
ladder = [ { agent = "codex", model = "sol", effort = "high" } ]

[roles.helper]
ladder = [ { agent = "claude", model = "haiku", effort = "low" } ]
"""


def write_minimal_project(tmp_path: Path, extra: str = "") -> Path:
    """Write `MINIMAL_PROJECT_CONFIG` (plus `extra`) as `tmp_path`'s `.shikiru/config.toml`."""
    shikiru_dir = tmp_path / ".shikiru"
    shikiru_dir.mkdir()
    (shikiru_dir / "config.toml").write_text(MINIMAL_PROJECT_CONFIG + extra)
    return tmp_path
