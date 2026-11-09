"""Installing a release wheel on this host, restarting its engines, and rolling back on failure."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

from shikiru.domain.releases import (
    TAG_PREFIX,
    ReleaseRuleError,
    checksum_for,
    digest_matches,
    newest_release,
    parse_installed_number,
)
from shikiru.errors import EXIT_GENERIC_ERROR
from shikiru.operator.lifecycle import PACKAGE_NAME
from shikiru.update.engines import engine_projects, restart_engines
from shikiru.update.errors import ReleaseStepError
from shikiru.update.store import wheel_name, wheel_version

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from shikiru.domain.releases import Release, ReleaseNumber
    from shikiru.update.commands import CommandRunner
    from shikiru.update.sources import ReleaseSource
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
CHECKSUMS_FILE_NAME = "SHA256SUMS"
NO_USABLE_RELEASE = (
    "the repository has no usable release: "
    "none is a v<major>.<minor>.<patch> tag that is not a draft"
)
RELEASE_FAILURES = (ReleaseStepError, ReleaseRuleError)


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


def installed_number(installed: str) -> ReleaseNumber:
    """Return the release numbers of the installed version, such as 0.1.4 for 0.1.4.dev3+gabc."""
    number = parse_installed_number(installed)
    if number is None:
        message = f"the installed version {installed} has no major.minor.patch release"
        raise ReleaseStepError(message)
    return number


async def latest_release(source: ReleaseSource) -> tuple[ReleaseNumber, Release]:
    """Return the newest usable release of the repository with its number."""
    found = newest_release(await source.releases())
    if found is None:
        raise ReleaseStepError(NO_USABLE_RELEASE)
    return found


async def verified_wheel(source: ReleaseSource, release: Release) -> tuple[str, bytes]:
    """Return the file name and content of the release's wheel, once its SHA-256 is confirmed."""
    name = wheel_name(release.tag.removeprefix(TAG_PREFIX))
    wheel = release.asset(name)
    checksums = release.asset(CHECKSUMS_FILE_NAME)
    if wheel is None:
        message = f"{release.tag} has no {name} file"
        raise ReleaseStepError(message)
    if checksums is None:
        message = f"{release.tag} has no {CHECKSUMS_FILE_NAME} file"
        raise ReleaseStepError(message)
    expected = checksum_for(await source.download(checksums.url), name)
    content = await source.download(wheel.url)
    if not digest_matches(content, expected):
        message = f"the SHA-256 of {name} is not the one {CHECKSUMS_FILE_NAME} lists"
        raise ReleaseStepError(message)
    return name, content


async def check_for_update(
    source: ReleaseSource,
    installed: str,
    report: Callable[[str], None],
) -> int:
    """Report the installed and the newest release, and whether an update exists; change nothing."""
    try:
        current = installed_number(installed)
        number, newest = await latest_release(source)
    except RELEASE_FAILURES as error:
        report(f"update check failed: {error}")
        return EXIT_FAILURE
    report(f"installed: {installed}")
    report(f"newest: {newest.tag.removeprefix(TAG_PREFIX)}")
    report("an update is available" if number > current else "up to date")
    return EXIT_SUCCESS


async def update_to_newest(
    source: ReleaseSource,
    commands: CommandRunner,
    store: ReleaseStore,
    installed: str,
    report: Callable[[str], None],
) -> int:
    """Download and verify the newest release, then install it as `update_from_wheel` does."""
    try:
        current = installed_number(installed)
        number, newest = await latest_release(source)
        if number <= current:
            report(f"already the newest release: {installed}")
            return EXIT_SUCCESS
        report(f"updating {installed} to {newest.tag.removeprefix(TAG_PREFIX)}")
        name, content = await verified_wheel(source, newest)
    except RELEASE_FAILURES as error:
        report(f"update stopped before anything was installed: {error}")
        return EXIT_FAILURE
    with TemporaryDirectory() as directory:
        wheel = stage_wheel(Path(directory), name, content)
        return await update_from_wheel(commands, store, wheel, report)


def stage_wheel(directory: Path, name: str, content: bytes) -> Path:
    """Write a downloaded wheel into `directory` under its release file name."""
    wheel = directory / name
    wheel.write_bytes(content)
    return wheel
