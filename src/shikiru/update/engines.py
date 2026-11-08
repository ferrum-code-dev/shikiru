"""Restarting the engines after an install, one project at a time."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from shikiru.errors import EXIT_COMMAND_REJECTED
from shikiru.operator.lifecycle import PACKAGE_NAME
from shikiru.update.errors import ReleaseStepError

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from shikiru.process import ProcessResult
    from shikiru.update.commands import CommandRunner

JSON_FLAG = "--json"
PROJECT_FLAG = "--project"
LS_ARGUMENT = "ls"
RESTART_ARGUMENT = "restart"
PROJECT_DIR_KEY = "project_dir"
ENGINE_STATE_KEY = "engine_state"
ENGINE_KEY = "engine"
ENGINE_UP = "up"
LS_TIMEOUT_SECONDS = 60.0
RESTART_TIMEOUT_SECONDS = 300.0


@dataclass
class EngineRestarts:
    restarted: list[str] = field(default_factory=list)
    busy: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


async def engine_projects(commands: CommandRunner) -> list[str]:
    """Return the project directories whose engine is up, as `shikiru ls` reports them."""
    listing = await commands.run((PACKAGE_NAME, JSON_FLAG, LS_ARGUMENT), timeout=LS_TIMEOUT_SECONDS)
    if listing.returncode != 0:
        message = f"`{PACKAGE_NAME} ls` failed, so the engines cannot be listed"
        raise ReleaseStepError(message)
    try:
        rows = json.loads(listing.stdout)
    except json.JSONDecodeError as error:
        message = f"`{PACKAGE_NAME} ls` printed no JSON: {error}"
        raise ReleaseStepError(message) from error
    return [row[PROJECT_DIR_KEY] for row in rows if row[ENGINE_STATE_KEY] == ENGINE_UP]


def engine_came_up(result: ProcessResult) -> bool:
    if result.returncode != 0:
        return False
    try:
        return json.loads(result.stdout).get(ENGINE_KEY) == ENGINE_UP
    except json.JSONDecodeError:
        return False


async def restart_engines(
    commands: CommandRunner,
    projects: Sequence[str],
    report: Callable[[str], None],
) -> EngineRestarts:
    """Restart the engine of each project, reporting busy and failed ones."""
    outcome = EngineRestarts()
    for project in projects:
        result = await commands.run(
            (PACKAGE_NAME, PROJECT_FLAG, project, JSON_FLAG, RESTART_ARGUMENT),
            timeout=RESTART_TIMEOUT_SECONDS,
        )
        if result.returncode == EXIT_COMMAND_REJECTED:
            outcome.busy.append(project)
            report(f"busy, left on the previous code: {project}: {result.stderr.strip()}")
            continue
        outcome.restarted.append(project)
        if engine_came_up(result):
            report(f"restarted: {project}")
        else:
            outcome.failed.append(project)
            report(f"engine did not come up: {project}: {result.stderr.strip()}")
    return outcome
