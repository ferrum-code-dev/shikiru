"""Installing a release wheel on this host, restarting its engines, and rolling back on failure."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.errors import EXIT_GENERIC_ERROR
from shikiru.operator.lifecycle import PACKAGE_NAME
from shikiru.update.engines import engine_projects, restart_engines
from shikiru.update.errors import ReleaseStepError
from shikiru.update.store import wheel_version

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from shikiru.update.commands import CommandRunner
    from shikiru.update.store import ReleaseStore

EXIT_SUCCESS = 0
EXIT_FAILURE = EXIT_GENERIC_ERROR
UV_COMMAND = "uv"
TOOL_ARGUMENT = "tool"
INSTALL_ARGUMENT = "install"
REINSTALL_FLAG = "--reinstall"
VERSION_FLAG = "--version"
INSTALL_TIMEOUT_SECONDS = 600.0
VERSION_TIMEOUT_SECONDS = 60.0


async def install(commands: CommandRunner, wheel: Path) -> None:
    """Install `wheel` as the tool and check that `shikiru --version` reports its version."""
    installed = await commands.run(
        (UV_COMMAND, TOOL_ARGUMENT, INSTALL_ARGUMENT, REINSTALL_FLAG, str(wheel)),
        timeout=INSTALL_TIMEOUT_SECONDS,
    )
    if installed.returncode != 0:
        message = f"installing {wheel.name} failed"
        raise ReleaseStepError(message)
    expected = wheel_version(wheel)
    reported = await commands.run((PACKAGE_NAME, VERSION_FLAG), timeout=VERSION_TIMEOUT_SECONDS)
    if reported.returncode != 0 or reported.stdout.strip() != expected:
        message = f"`{PACKAGE_NAME} --version` does not report {expected}"
        raise ReleaseStepError(message)


async def roll_back(
    commands: CommandRunner,
    store: ReleaseStore,
    report: Callable[[str], None],
    projects: Sequence[str],
) -> bool:
    """Reinstall the release `current` names and restart `projects`; return whether all came up."""
    try:
        previous = store.read_current()
        if previous is None:
            report("no current release is recorded, so there is nothing to roll back to")
            return False
        await install(commands, previous)
    except ReleaseStepError as error:
        report(f"rollback failed: {error}")
        return False
    outcome = await restart_engines(commands, projects, report)
    report(f"rolled back to {previous.name}")
    return not outcome.failed


async def update_from_wheel(
    commands: CommandRunner,
    store: ReleaseStore,
    wheel: Path,
    report: Callable[[str], None],
) -> int:
    """Store and install `wheel`, restart the engines that are up, and roll back on any failure."""
    try:
        version = wheel_version(wheel)
        staged = store.add(wheel)
    except ReleaseStepError as error:
        report(f"deploy stopped before anything was installed: {error}")
        return EXIT_FAILURE
    try:
        await install(commands, staged)
        projects = await engine_projects(commands)
    except ReleaseStepError as error:
        report(f"deploy failed: {error}")
        await roll_back(commands, store, report, ())
        return EXIT_FAILURE
    outcome = await restart_engines(commands, projects, report)
    if outcome.failed:
        report("deploy failed: an engine did not come up on the new release")
        await roll_back(commands, store, report, outcome.restarted)
        return EXIT_FAILURE
    store.write_current(staged)
    store.prune(staged)
    report(f"deployed {version} from {staged.name}")
    if outcome.busy:
        report("busy projects still run the previous code; `shikiru restart` there finishes it")
    return EXIT_SUCCESS


async def roll_back_on_demand(
    commands: CommandRunner,
    store: ReleaseStore,
    report: Callable[[str], None],
) -> int:
    """Roll back to the release `current` names, restarting the engines that are up."""
    try:
        projects = await engine_projects(commands)
    except ReleaseStepError as error:
        report(f"rollback failed: {error}")
        return EXIT_FAILURE
    return EXIT_SUCCESS if await roll_back(commands, store, report, projects) else EXIT_FAILURE
