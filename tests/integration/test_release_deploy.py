"""Deploying a release refuses unready trees, installs the wheel, and rolls back on failure."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from scripts import deploy as release

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

NEW_VERSION = "0.1.0"
OLD_VERSION = "0.0.9"
NEW_WHEEL = "shikiru-0.1.0-py3-none-any.whl"
OLD_WHEEL = "shikiru-0.0.9-py3-none-any.whl"
RELEASE_TAG = "v0.1.0"
REJECTED = 4
BROKEN_RESTART = 1
REPORTED_WRONG_VERSION = "9.9.9"


def wheel_name(version: str) -> str:
    return f"shikiru-{version}-py3-none-any.whl"


class FakeHost:
    def __init__(
        self,
        *,
        engines: Mapping[str, str],
        installed: str = OLD_VERSION,
        tags: Sequence[str] = (RELEASE_TAG,),
        dirty: bool = False,
        gate_status: int = 0,
        smoke_status: int = 0,
        busy: Sequence[str] = (),
        broken: Sequence[str] = (),
        misreporting: Sequence[str] = (),
    ) -> None:
        self.engines = dict(engines)
        self.installed = installed
        self.tags = tags
        self.dirty = dirty
        self.gate_status = gate_status
        self.smoke_status = smoke_status
        self.busy = busy
        self.broken = broken
        self.misreporting = misreporting
        self.installs: list[str] = []
        self.restarts: list[tuple[str, str]] = []

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str] | None = None,
        show_output: bool = False,
    ) -> release.CommandResult:
        del cwd, env, show_output
        if argv[0] == release.PACKAGE:
            return self._shikiru(tuple(argv))
        return self._toolchain(tuple(argv))

    def _toolchain(self, argv: tuple[str, ...]) -> release.CommandResult:
        match argv:
            case ("git", "status", "--porcelain"):
                return release.CommandResult(0, " M README.md\n" if self.dirty else "")
            case ("git", "tag", "--points-at", "HEAD"):
                return release.CommandResult(0, "".join(f"{tag}\n" for tag in self.tags))
            case ("uv", "run", "--locked", "poe", "check"):
                return release.CommandResult(self.gate_status)
            case ("bash", "scripts/release-readiness-smoke.sh"):
                return release.CommandResult(self.smoke_status)
            case ("uv", "build", "--wheel", "--out-dir", out_dir):
                (Path(out_dir) / wheel_name(NEW_VERSION)).write_bytes(b"wheel")
                return release.CommandResult(0)
            case ("uv", "tool", "install", "--reinstall", wheel):
                self.installs.append(Path(wheel).name)
                self.installed = release.wheel_version(Path(wheel))
                return release.CommandResult(0)
            case _:
                raise AssertionError(argv)

    def _shikiru(self, argv: tuple[str, ...]) -> release.CommandResult:
        match argv:
            case ("shikiru", "--version"):
                reported = (
                    REPORTED_WRONG_VERSION
                    if self.installed in self.misreporting
                    else self.installed
                )
                return release.CommandResult(0, f"{reported}\n")
            case ("shikiru", "--json", "ls"):
                rows = [{"project_dir": p, "engine_state": s} for p, s in self.engines.items()]
                return release.CommandResult(0, json.dumps(rows))
            case ("shikiru", "--project", project, "--json", "restart"):
                return self._restart(project)
            case _:
                raise AssertionError(argv)

    def _restart(self, project: str) -> release.CommandResult:
        if project in self.busy:
            return release.CommandResult(
                REJECTED, "", "restart refused while tasks are running: T-1"
            )
        self.restarts.append((project, self.installed))
        if project in self.broken and self.installed == NEW_VERSION:
            return release.CommandResult(BROKEN_RESTART, "", "engine did not start")
        self.engines[project] = "up"
        return release.CommandResult(0, json.dumps({"restarted": True, "engine": "up"}))


def publish(store: Path, name: str, *, age: float | None = None, current: bool = False) -> None:
    store.mkdir(parents=True, exist_ok=True)
    wheel = store / name
    wheel.write_bytes(b"wheel")
    if age is not None:
        os.utime(wheel, (age, age))
    if current:
        (store / "current").write_text(f"{name}\n", encoding="utf-8")


@pytest.fixture
def paths(tmp_path: Path) -> release.ReleasePaths:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        f'[project]\nname = "shikiru"\nversion = "{NEW_VERSION}"\n', encoding="utf-8"
    )
    return release.ReleasePaths(repo=repo, store=tmp_path / "store")


ENGINES = {"/p/a": "up"}


@pytest.mark.parametrize(
    ("host", "failure"),
    [
        pytest.param(FakeHost(engines=ENGINES, dirty=True), "not clean", id="dirty-work-tree"),
        pytest.param(FakeHost(engines=ENGINES, tags=()), "untagged", id="untagged-head"),
        pytest.param(
            FakeHost(engines=ENGINES, tags=("v0.2.0",)),
            f"not {RELEASE_TAG}",
            id="tag-differs-from-version",
        ),
        pytest.param(
            FakeHost(engines=ENGINES, gate_status=1), "the gate failed", id="failing-gate"
        ),
        pytest.param(
            FakeHost(engines=ENGINES, smoke_status=1),
            "the release smoke failed",
            id="failing-smoke",
        ),
    ],
)
def test_deploy_stops_before_installing_when_the_release_is_not_ready(
    paths: release.ReleasePaths, host: FakeHost, failure: str
) -> None:
    messages: list[str] = []

    code = release.deploy(host, paths, messages.append)

    assert code == release.EXIT_FAILURE
    assert failure in " ".join(messages)
    assert host.installs == []
    assert host.installed == OLD_VERSION
    assert not (paths.store / "current").exists()


def test_successful_deploy_installs_restarts_idle_engines_and_reports_busy_ones(
    paths: release.ReleasePaths,
) -> None:
    host = FakeHost(engines={"/p/a": "up", "/p/b": "down", "/p/c": "up"}, busy=("/p/c",))
    messages: list[str] = []

    code = release.deploy(host, paths, messages.append)

    assert code == release.EXIT_SUCCESS
    assert host.installs == [NEW_WHEEL]
    assert host.installed == NEW_VERSION
    assert host.restarts == [("/p/a", NEW_VERSION)]
    assert any("/p/c" in message and "busy" in message for message in messages)
    assert (paths.store / "current").read_text(encoding="utf-8") == f"{NEW_WHEEL}\n"


def test_failed_verification_reinstalls_the_current_release(paths: release.ReleasePaths) -> None:
    publish(paths.store, OLD_WHEEL, current=True)
    host = FakeHost(engines={"/p/a": "up"}, misreporting=(NEW_VERSION,))
    messages: list[str] = []

    code = release.deploy(host, paths, messages.append)

    assert code == release.EXIT_FAILURE
    assert host.installs == [NEW_WHEEL, OLD_WHEEL]
    assert host.installed == OLD_VERSION
    assert host.restarts == []
    assert (paths.store / "current").read_text(encoding="utf-8") == f"{OLD_WHEEL}\n"


def test_engine_that_does_not_come_up_rolls_back_every_engine_restarted(
    paths: release.ReleasePaths,
) -> None:
    publish(paths.store, OLD_WHEEL, current=True)
    host = FakeHost(engines={"/p/a": "up", "/p/b": "up"}, broken=("/p/b",))
    messages: list[str] = []

    code = release.deploy(host, paths, messages.append)

    assert code == release.EXIT_FAILURE
    assert host.installed == OLD_VERSION
    assert host.restarts == [
        ("/p/a", NEW_VERSION),
        ("/p/b", NEW_VERSION),
        ("/p/a", OLD_VERSION),
        ("/p/b", OLD_VERSION),
    ]
    assert (paths.store / "current").read_text(encoding="utf-8") == f"{OLD_WHEEL}\n"


def test_failed_deploy_without_a_current_release_says_so(paths: release.ReleasePaths) -> None:
    host = FakeHost(engines={}, misreporting=(NEW_VERSION,))
    messages: list[str] = []

    code = release.deploy(host, paths, messages.append)

    assert code == release.EXIT_FAILURE
    assert any("nothing to roll back to" in message for message in messages)


def test_successful_deploy_keeps_the_five_newest_wheels_and_the_current_one(
    paths: release.ReleasePaths,
) -> None:
    for index in range(1, 7):
        publish(paths.store, wheel_name(f"0.0.{index}"), age=1000 + index)
    (paths.store / "current").write_text(f"{wheel_name('0.0.1')}\n", encoding="utf-8")
    host = FakeHost(engines={})
    messages: list[str] = []

    code = release.deploy(host, paths, messages.append)

    assert code == release.EXIT_SUCCESS
    kept = {wheel.name for wheel in paths.store.glob("*.whl")}
    assert kept == {NEW_WHEEL, *(wheel_name(f"0.0.{index}") for index in range(3, 7))}
    assert (paths.store / "current").read_text(encoding="utf-8") == f"{NEW_WHEEL}\n"


def test_rollback_reinstalls_the_current_release_and_restarts_engines_that_are_up(
    paths: release.ReleasePaths,
) -> None:
    publish(paths.store, OLD_WHEEL, current=True)
    host = FakeHost(engines={"/p/a": "up", "/p/b": "down"}, installed=NEW_VERSION)
    messages: list[str] = []

    code = release.roll_back_on_demand(host, paths, messages.append)

    assert code == release.EXIT_SUCCESS
    assert host.installs == [OLD_WHEEL]
    assert host.installed == OLD_VERSION
    assert host.restarts == [("/p/a", OLD_VERSION)]


CHECKOUT_VENV = "/checkout/.venv"
CHECKOUT_VENV_BIN = f"{CHECKOUT_VENV}/bin"
SYSTEM_PATH = os.pathsep.join(["/usr/bin", "/bin"])
PRINT_PATH = "import os, sys; sys.stdout.write(os.environ['PATH'])"


@pytest.mark.parametrize(
    ("environ", "expected_path"),
    [
        pytest.param(
            {
                "VIRTUAL_ENV": CHECKOUT_VENV,
                "PATH": os.pathsep.join([CHECKOUT_VENV_BIN, SYSTEM_PATH]),
            },
            SYSTEM_PATH,
            id="checkout-venv-bin-removed",
        ),
        pytest.param(
            {"PATH": os.pathsep.join([CHECKOUT_VENV_BIN, SYSTEM_PATH])},
            os.pathsep.join([CHECKOUT_VENV_BIN, SYSTEM_PATH]),
            id="path-unchanged-without-virtual-env",
        ),
    ],
)
def test_commands_run_without_the_checkout_venv_bin_on_path(
    tmp_path: Path, environ: Mapping[str, str], expected_path: str
) -> None:
    runner = release.SubprocessRunner(environ)

    result = runner.run((sys.executable, "-c", PRINT_PATH), cwd=tmp_path)

    assert result.returncode == 0
    assert result.stdout == expected_path
