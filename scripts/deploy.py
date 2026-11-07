"""Deploy the tagged Shikiru release to this host, or roll back to the release last deployed."""

import argparse
import json
import os
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

PACKAGE = "shikiru"
PYPROJECT_NAME = "pyproject.toml"
REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_HOME_ENVIRONMENT = "XDG_DATA_HOME"
DEFAULT_DATA_HOME = Path(".local", "share")
RELEASES_DIRECTORY = "releases"
CURRENT_NAME = "current"
TAG_PREFIX = "v"
RETAINED_WHEELS = 5
COMMAND_REJECTED_EXIT_CODE = 4
ENGINE_KEY = "engine"
ENGINE_UP = "up"
LS_ARGUMENT = "ls"
RESTART_ARGUMENT = "restart"
GATE_COMMAND = ("uv", "run", "--locked", "poe", "check")
GATE_ENVIRONMENT = {"PYTEST_ADDOPTS": "--dist=loadfile"}
SMOKE_COMMAND = ("bash", "scripts/release-readiness-smoke.sh")
GIT_STATUS_COMMAND = ("git", "status", "--porcelain")
GIT_TAGS_AT_HEAD_COMMAND = ("git", "tag", "--points-at", "HEAD")
EXIT_SUCCESS = 0
EXIT_FAILURE = 1


class ReleaseStepError(Exception):
    """A deploy step failed; the message names the step and the reason."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class CommandRunner(Protocol):
    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str] | None = None,
        show_output: bool = False,
    ) -> CommandResult: ...


class SubprocessRunner:
    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str] | None = None,
        show_output: bool = False,
    ) -> CommandResult:
        environment = None if env is None else {**os.environ, **env}
        if show_output:
            completed = subprocess.run(argv, cwd=cwd, env=environment, check=False)
            return CommandResult(completed.returncode)
        completed = subprocess.run(
            argv, cwd=cwd, env=environment, capture_output=True, text=True, check=False
        )
        return CommandResult(completed.returncode, completed.stdout, completed.stderr)


@dataclass(frozen=True)
class ReleasePaths:
    repo: Path
    store: Path

    @property
    def pyproject(self) -> Path:
        return self.repo / PYPROJECT_NAME

    @property
    def current(self) -> Path:
        return self.store / CURRENT_NAME


@dataclass
class EngineRestarts:
    restarted: list[str] = field(default_factory=list)
    busy: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


def release_store() -> Path:
    configured = os.environ.get(DATA_HOME_ENVIRONMENT)
    data_home = Path(configured) if configured else Path.home() / DEFAULT_DATA_HOME
    return data_home / PACKAGE / RELEASES_DIRECTORY


def project_version(paths: ReleasePaths) -> str:
    project = tomllib.loads(paths.pyproject.read_text(encoding="utf-8"))
    return str(project["project"]["version"])


def wheel_version(wheel: Path) -> str:
    return wheel.name.split("-")[1]


def check_source(runner: CommandRunner, paths: ReleasePaths, version: str) -> None:
    status = runner.run(GIT_STATUS_COMMAND, cwd=paths.repo)
    if status.returncode != 0 or status.stdout.strip():
        message = "the work tree is not clean, or git could not report it"
        raise ReleaseStepError(message)
    tags = runner.run(GIT_TAGS_AT_HEAD_COMMAND, cwd=paths.repo)
    release_tags = [tag for tag in tags.stdout.split() if tag.startswith(TAG_PREFIX)]
    expected = f"{TAG_PREFIX}{version}"
    if not release_tags:
        message = f"HEAD is untagged; tag the release commit {expected}"
        raise ReleaseStepError(message)
    if expected not in release_tags:
        message = f"HEAD is tagged {', '.join(release_tags)}, not {expected}"
        raise ReleaseStepError(message)


def run_gate(runner: CommandRunner, paths: ReleasePaths) -> None:
    gate = runner.run(GATE_COMMAND, cwd=paths.repo, env=GATE_ENVIRONMENT, show_output=True)
    if gate.returncode != 0:
        message = "the gate failed"
        raise ReleaseStepError(message)
    smoke = runner.run(SMOKE_COMMAND, cwd=paths.repo, show_output=True)
    if smoke.returncode != 0:
        message = "the release smoke failed"
        raise ReleaseStepError(message)


def build_wheel(runner: CommandRunner, paths: ReleasePaths, version: str) -> Path:
    paths.store.mkdir(parents=True, exist_ok=True)
    built = runner.run(
        ("uv", "build", "--wheel", "--out-dir", str(paths.store)),
        cwd=paths.repo,
        show_output=True,
    )
    if built.returncode != 0:
        message = "building the wheel failed"
        raise ReleaseStepError(message)
    wheels = sorted(paths.store.glob(f"{PACKAGE}-{version}-*.whl"))
    if len(wheels) != 1:
        message = f"expected one wheel for {version} in {paths.store}, found {len(wheels)}"
        raise ReleaseStepError(message)
    return wheels[0]


def install(runner: CommandRunner, paths: ReleasePaths, wheel: Path) -> None:
    installed = runner.run(
        ("uv", "tool", "install", "--reinstall", str(wheel)),
        cwd=paths.repo,
        show_output=True,
    )
    if installed.returncode != 0:
        message = f"installing {wheel.name} failed"
        raise ReleaseStepError(message)
    expected = wheel_version(wheel)
    reported = runner.run((PACKAGE, "--version"), cwd=paths.repo)
    if reported.returncode != 0 or reported.stdout.strip() != expected:
        message = f"`{PACKAGE} --version` does not report {expected}"
        raise ReleaseStepError(message)


def engine_projects(runner: CommandRunner, paths: ReleasePaths) -> list[str]:
    listing = runner.run((PACKAGE, "--json", LS_ARGUMENT), cwd=paths.repo)
    if listing.returncode != 0:
        message = f"`{PACKAGE} ls` failed, so the engines cannot be listed"
        raise ReleaseStepError(message)
    try:
        rows = json.loads(listing.stdout)
    except json.JSONDecodeError as error:
        message = f"`{PACKAGE} ls` printed no JSON: {error}"
        raise ReleaseStepError(message) from error
    return [row["project_dir"] for row in rows if row["engine_state"] == ENGINE_UP]


def engine_came_up(result: CommandResult) -> bool:
    if result.returncode != 0:
        return False
    try:
        return json.loads(result.stdout).get(ENGINE_KEY) == ENGINE_UP
    except json.JSONDecodeError:
        return False


def restart_engines(
    runner: CommandRunner,
    paths: ReleasePaths,
    projects: Sequence[str],
    report: Callable[[str], None],
) -> EngineRestarts:
    outcome = EngineRestarts()
    for project in projects:
        result = runner.run(
            (PACKAGE, "--project", project, "--json", RESTART_ARGUMENT), cwd=paths.repo
        )
        if result.returncode == COMMAND_REJECTED_EXIT_CODE:
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


def read_current(paths: ReleasePaths) -> Path | None:
    if not paths.current.is_file():
        return None
    name = paths.current.read_text(encoding="utf-8").strip()
    wheel = paths.store / name
    if not wheel.is_file():
        message = f"{CURRENT_NAME} names {name}, which is not in {paths.store}"
        raise ReleaseStepError(message)
    return wheel


def write_current(paths: ReleasePaths, wheel: Path) -> None:
    temporary = paths.store / f"{CURRENT_NAME}.tmp"
    temporary.write_text(f"{wheel.name}\n", encoding="utf-8")
    temporary.replace(paths.current)


def prune_wheels(paths: ReleasePaths, keep: Path) -> None:
    wheels = sorted(paths.store.glob("*.whl"), key=lambda wheel: wheel.stat().st_mtime)
    for stale in wheels[:-RETAINED_WHEELS]:
        if stale != keep:
            stale.unlink()


def roll_back(
    runner: CommandRunner,
    paths: ReleasePaths,
    report: Callable[[str], None],
    projects: Sequence[str],
) -> bool:
    try:
        previous = read_current(paths)
        if previous is None:
            report("no current release is recorded, so there is nothing to roll back to")
            return False
        install(runner, paths, previous)
    except ReleaseStepError as error:
        report(f"rollback failed: {error}")
        return False
    outcome = restart_engines(runner, paths, projects, report)
    report(f"rolled back to {previous.name}")
    return not outcome.failed


def deploy(runner: CommandRunner, paths: ReleasePaths, report: Callable[[str], None]) -> int:
    version = project_version(paths)
    try:
        check_source(runner, paths, version)
        run_gate(runner, paths)
        wheel = build_wheel(runner, paths, version)
    except ReleaseStepError as error:
        report(f"deploy stopped before anything was installed: {error}")
        return EXIT_FAILURE
    try:
        install(runner, paths, wheel)
        projects = engine_projects(runner, paths)
    except ReleaseStepError as error:
        report(f"deploy failed: {error}")
        roll_back(runner, paths, report, ())
        return EXIT_FAILURE
    outcome = restart_engines(runner, paths, projects, report)
    if outcome.failed:
        report("deploy failed: an engine did not come up on the new release")
        roll_back(runner, paths, report, outcome.restarted)
        return EXIT_FAILURE
    write_current(paths, wheel)
    prune_wheels(paths, wheel)
    report(f"deployed {version} from {wheel.name}")
    if outcome.busy:
        report("busy projects still run the previous code; `shikiru restart` there finishes it")
    return EXIT_SUCCESS


def roll_back_on_demand(
    runner: CommandRunner, paths: ReleasePaths, report: Callable[[str], None]
) -> int:
    try:
        projects = engine_projects(runner, paths)
    except ReleaseStepError as error:
        report(f"rollback failed: {error}")
        return EXIT_FAILURE
    return EXIT_SUCCESS if roll_back(runner, paths, report, projects) else EXIT_FAILURE


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Deploy the tagged Shikiru release, or roll back to the release last deployed."
    )
    parser.add_argument(
        "--rollback",
        action="store_true",
        help="reinstall the release named by the release store's current file",
    )
    arguments = parser.parse_args(argv)
    paths = ReleasePaths(repo=REPO_ROOT, store=release_store())
    runner = SubprocessRunner()
    if arguments.rollback:
        return roll_back_on_demand(runner, paths, print)
    return deploy(runner, paths, print)


if __name__ == "__main__":
    raise SystemExit(main())
