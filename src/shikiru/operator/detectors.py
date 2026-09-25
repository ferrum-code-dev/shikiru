"""Project detection shared by non-interactive init and the future wizard (06 §3)."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from shikiru.errors import ConfigError
from shikiru.paths import EXCLUDE_LINE, SHIKIRU_DIR_NAME, project_config_path

if TYPE_CHECKING:
    from shikiru.operator.plugin_checks import PluginChecks
    from shikiru.workspace.git import Git

MAX_SCAN_DEPTH = 4
TRACKER_DIRECTORY = "tasks"
TRACKER_NAMES = ("plan", "todo")
CACHE_NAMES = (
    ".venv",
    "node_modules",
    "target",
    ".gradle",
    ".pytest_cache",
)
DEFAULT_MODEL = "default"
EXCLUDED_DIRECTORIES = frozenset({SHIKIRU_DIR_NAME, ".git"})


def not_repository_root(root: Path) -> str:
    """Explain why `root` cannot be a Shikiru project: it is not a git repository root (D20)."""
    return (
        f"{root} is not a git repository root; Shikiru supports one repository per project, "
        "so run it from the repository's top-level directory"
    )


@dataclass(frozen=True, slots=True)
class DetectedProject:
    """All filesystem and plugin facts used to write a default project config."""

    name: str
    base: str
    caches: tuple[str, ...]
    trackers: tuple[str, ...]
    agents: tuple[str, ...]


class ProjectDetectors:
    """Detect the repository base, trackers, installed agents and caches for `init`."""

    def __init__(self, root: Path, *, git: Git, plugin_checks: PluginChecks) -> None:
        """Inspect `root` through injected git and agent-plugin check boundaries."""
        self._root = root.resolve()
        self._git = git
        self._plugin_checks = plugin_checks

    async def detect(self) -> DetectedProject:
        """Return deterministic project facts, or an actionable config error."""
        if not await self._git.is_repository_root(self._root):
            raise ConfigError(not_repository_root(self._root))
        agents = await self._agents()
        if not agents:
            message = "no shikiru.agents plugin is installed"
            raise ConfigError(message)
        return DetectedProject(
            name=self._root.name,
            base=await self._git.current_branch(self._root),
            caches=tuple(await self._caches()),
            trackers=tuple(self._trackers()),
            agents=agents,
        )

    async def initialize(self) -> Path:
        """Detect facts, write config and exclude `.shikiru/` in the repository."""
        config_path = project_config_path(self._root)
        if await asyncio.to_thread(config_path.is_file):
            return config_path
        project = await self.detect()
        await asyncio.to_thread(_write_config, config_path, render_config(project))
        await self._git.append_to_exclude(self._root, EXCLUDE_LINE)
        return config_path

    def _trackers(self) -> list[str]:
        candidates: set[str] = set()
        for path in _files_at_depth(self._root, MAX_SCAN_DEPTH):
            relative = path.relative_to(self._root).as_posix()
            name = path.stem.lower()
            if (
                path.parent.name == TRACKER_DIRECTORY
                or name.startswith(TRACKER_NAMES)
                or name.endswith(TRACKER_NAMES)
            ) and path.suffix.lower() == ".md":
                candidates.add(relative)
        return sorted(candidates)

    async def _caches(self) -> list[str]:
        ignored = await self._git.ignored_paths(self._root)
        return sorted(path.name for path in ignored if path.name in CACHE_NAMES)

    async def _agents(self) -> tuple[str, ...]:
        results = [
            await self._plugin_checks.check(name, {}) for name in self._plugin_checks.names()
        ]
        names = tuple(result.name for result in results if result.ok)
        if names:
            return names
        if not results:
            message = "no shikiru.agents plugin is installed"
        else:
            details = "; ".join(f"{result.name}: {result.detail}" for result in results)
            message = f"no installed agent plugin passed doctor(): {details}"
        raise ConfigError(message)


def render_config(project: DetectedProject) -> str:
    """Render a complete default config from detected project facts."""
    agent = project.agents[0]
    lines = [
        "[project]",
        f"name = {json.dumps(project.name)}",
        f"trackers = {json.dumps(list(project.trackers))}",
        "",
    ]
    lines.extend(
        (
            "[repo]",
            f"base = {json.dumps(project.base)}",
            f"caches = {json.dumps(list(project.caches))}",
            'remote = "origin"',
            "",
        )
    )
    for name in project.agents:
        lines.extend(
            (
                f"[agents.{name}]",
                "",
            )
        )
    rung = f"{{ agent = {json.dumps(agent)}, model = {json.dumps(DEFAULT_MODEL)} }}"
    lines.extend(
        (
            "[roles.leader]",
            f"ladder = [{rung}]",
            "",
            "[roles.implementer]",
            f"ladder = [{rung}]",
            "",
            "[roles.helper]",
            f"ladder = [{rung}]",
            "",
        )
    )
    return "\n".join(lines)


def _write_config(config_path: Path, contents: str) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(contents)


def _files_at_depth(root: Path, max_depth: int) -> list[Path]:
    files: list[Path] = []
    pending: list[tuple[Path, int]] = [(root, 0)]
    while pending:
        directory, depth = pending.pop()
        with os.scandir(directory) as entries:
            for entry in sorted(entries, key=lambda item: item.name):
                if entry.name in EXCLUDED_DIRECTORIES:
                    continue
                entry_depth = depth + 1
                if entry_depth > max_depth:
                    continue
                path = Path(entry.path)
                if entry.is_dir(follow_symlinks=False):
                    pending.append((path, entry_depth))
                elif entry.is_file(follow_symlinks=False):
                    files.append(path)
    return files
