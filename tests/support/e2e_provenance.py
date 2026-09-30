"""The source state and tool versions a real-agent E2E result was produced with."""

from __future__ import annotations

import platform
from typing import TYPE_CHECKING

from shikiru.process import SubprocessRunner

if TYPE_CHECKING:
    from pathlib import Path

PROBE_TIMEOUT_SECONDS = 30.0
TOOL_VERSION_ARGV = {
    "claude": ("claude", "--version"),
    "codex": ("codex", "--version"),
    "tmux": ("tmux", "-V"),
    "git": ("git", "--version"),
}
PYTHON_TOOL = "python"


async def source_state(root: Path) -> dict[str, object]:
    """Return the checked-out commit of `root` and whether its tree has uncommitted changes."""
    runner = SubprocessRunner()
    commit = await runner.run(
        ["git", "rev-parse", "HEAD"], timeout=PROBE_TIMEOUT_SECONDS, cwd=root, check=True
    )
    status = await runner.run(
        ["git", "status", "--porcelain"], timeout=PROBE_TIMEOUT_SECONDS, cwd=root, check=True
    )
    return {"source_commit": commit.stdout.strip(), "source_dirty": bool(status.stdout.strip())}


async def tool_versions() -> dict[str, str | None]:
    """Return each tool's reported version; a tool that is not installed is recorded as null."""
    runner = SubprocessRunner()
    versions: dict[str, str | None] = {PYTHON_TOOL: platform.python_version()}
    for tool, argv in TOOL_VERSION_ARGV.items():
        try:
            result = await runner.run(argv, timeout=PROBE_TIMEOUT_SECONDS, check=True)
        except FileNotFoundError:
            versions[tool] = None
        else:
            versions[tool] = result.stdout.strip()
    return versions
