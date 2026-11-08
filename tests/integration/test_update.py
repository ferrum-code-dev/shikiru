"""`shikiru update` installs a release wheel, restarts the engines, and rolls back on failure."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from shikiru.cli.app import app
from shikiru.errors import EXIT_COMMAND_REJECTED, EXIT_GENERIC_ERROR
from shikiru.operator.lifecycle import PACKAGE_NAME
from shikiru.process import ProcessResult, SubprocessRunner
from shikiru.update.commands import HostCommands, command_environment
from shikiru.update.flow import EXIT_FAILURE, EXIT_SUCCESS, roll_back_on_demand, update_from_wheel
from shikiru.update.store import ReleaseStore, wheel_version

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

NEW_VERSION = "0.1.0"
OLD_VERSION = "0.0.9"
NEW_WHEEL = "shikiru-0.1.0-py3-none-any.whl"
OLD_WHEEL = "shikiru-0.0.9-py3-none-any.whl"
REPORTED_WRONG_VERSION = "9.9.9"
BROKEN_RESTART = 1
UNSTARTED_ENGINE_MESSAGE = "engine did not start"
ENGINE_UP = "up"
ENGINE_DOWN = "down"
PROJECT_A = "/p/a"
PROJECT_B = "/p/b"
PROJECT_C = "/p/c"
RETAINED_WHEEL_COUNT = 5
TIMEOUT_EXIT_CODE = 124
COMMAND_TIMEOUT_SECONDS = 30.0
SHORT_TIMEOUT_SECONDS = 0.5
LONG_SLEEP_SECONDS = 60
CHECKOUT_VENV = "/checkout/.venv"
CHECKOUT_VENV_BIN = f"{CHECKOUT_VENV}/bin"
SYSTEM_PATH = os.pathsep.join(["/usr/bin", "/bin"])
PRINT_PATH = "import os, sys; sys.stdout.write(os.environ['PATH'])"
SLEEP_PROGRAM = f"import time; time.sleep({LONG_SLEEP_SECONDS})"
NO_OPTIONS_EXIT = EXIT_GENERIC_ERROR


def wheel_name(version: str) -> str:
    return f"shikiru-{version}-py3-none-any.whl"


def loaded_shikiru_modules() -> frozenset[str]:
    return frozenset(
        name for name in sys.modules if name == PACKAGE_NAME or name.startswith(f"{PACKAGE_NAME}.")
    )


@dataclass
class FakeHost:
    engines: dict[str, str]
    installed: str = OLD_VERSION
    busy: Sequence[str] = ()
    broken: Sequence[str] = ()
    misreporting: Sequence[str] = ()
    installs: list[str] = field(default_factory=list)
    restarts: list[tuple[str, str]] = field(default_factory=list)
    modules_at_install: frozenset[str] | None = None

    async def run(self, argv: Sequence[str], *, timeout: float) -> ProcessResult:
        del timeout
        match tuple(argv):
            case ("uv", "tool", "install", "--reinstall", wheel):
                self.modules_at_install = loaded_shikiru_modules()
                self.installs.append(Path(wheel).name)
                self.installed = wheel_version(Path(wheel))
                return _result(argv, 0)
            case (name, "--version") if name == PACKAGE_NAME:
                reported = (
                    REPORTED_WRONG_VERSION
                    if self.installed in self.misreporting
                    else self.installed
                )
                return _result(argv, 0, f"{reported}\n")
            case (name, "--json", "ls") if name == PACKAGE_NAME:
                rows = [{"project_dir": p, "engine_state": s} for p, s in self.engines.items()]
                return _result(argv, 0, json.dumps(rows))
            case (name, "--project", project, "--json", "restart") if name == PACKAGE_NAME:
                return self._restart(argv, project)
            case _:
                raise AssertionError(argv)

    def _restart(self, argv: Sequence[str], project: str) -> ProcessResult:
        if project in self.busy:
            return _result(
                argv, EXIT_COMMAND_REJECTED, "", "restart refused while tasks are running"
            )
        self.restarts.append((project, self.installed))
        if project in self.broken and self.installed == NEW_VERSION:
            return _result(argv, BROKEN_RESTART, "", UNSTARTED_ENGINE_MESSAGE)
        self.engines[project] = ENGINE_UP
        return _result(argv, 0, json.dumps({"restarted": True, "engine": ENGINE_UP}))


def _result(argv: Sequence[str], code: int, stdout: str = "", stderr: str = "") -> ProcessResult:
    return ProcessResult(argv=tuple(argv), returncode=code, stdout=stdout, stderr=stderr)


def publish(
    store: ReleaseStore, name: str, *, age: float | None = None, current: bool = False
) -> None:
    store.root.mkdir(parents=True, exist_ok=True)
    wheel = store.root / name
    wheel.write_bytes(b"wheel")
    if age is not None:
        os.utime(wheel, (age, age))
    if current:
        store.current_file.write_text(f"{name}\n", encoding="utf-8")


def new_wheel(tmp_path: Path, version: str = NEW_VERSION) -> Path:
    wheel = tmp_path / "dist" / wheel_name(version)
    wheel.parent.mkdir(parents=True, exist_ok=True)
    wheel.write_bytes(b"wheel")
    return wheel


@pytest.fixture
def store(tmp_path: Path) -> ReleaseStore:
    return ReleaseStore(tmp_path / "store")


async def test_update_installs_restarts_idle_engines_and_reports_busy_ones(
    tmp_path: Path, store: ReleaseStore
) -> None:
    host = FakeHost(
        engines={PROJECT_A: ENGINE_UP, PROJECT_B: ENGINE_DOWN, PROJECT_C: ENGINE_UP},
        busy=(PROJECT_C,),
    )
    messages: list[str] = []

    code = await update_from_wheel(host, store, new_wheel(tmp_path), messages.append)

    assert code == EXIT_SUCCESS
    assert host.installs == [NEW_WHEEL]
    assert host.installed == NEW_VERSION
    assert host.restarts == [(PROJECT_A, NEW_VERSION)]
    assert any(PROJECT_C in message and "busy" in message for message in messages)
    assert store.current_file.read_text(encoding="utf-8") == f"{NEW_WHEEL}\n"


async def test_update_does_not_import_shikiru_modules_after_the_install_step(
    tmp_path: Path, store: ReleaseStore
) -> None:
    host = FakeHost(engines={PROJECT_A: ENGINE_UP})
    modules_before = loaded_shikiru_modules()

    await update_from_wheel(host, store, new_wheel(tmp_path), lambda _message: None)

    assert host.modules_at_install == modules_before
    assert loaded_shikiru_modules() == modules_before


@pytest.mark.parametrize(
    ("name", "exists"),
    [
        pytest.param(NEW_WHEEL, False, id="missing-wheel"),
        pytest.param("otherpkg-0.1.0-py3-none-any.whl", True, id="not-a-shikiru-wheel"),
        pytest.param("shikiru-0.1.0.tar.gz", True, id="not-a-wheel-file"),
    ],
)
async def test_update_refuses_a_path_that_is_not_a_shikiru_wheel_before_installing(
    tmp_path: Path, store: ReleaseStore, name: str, exists: bool
) -> None:
    path = tmp_path / name
    if exists:
        path.write_bytes(b"wheel")
    host = FakeHost(engines={PROJECT_A: ENGINE_UP})

    code = await update_from_wheel(host, store, path, lambda _message: None)

    assert code == EXIT_FAILURE
    assert host.installs == []
    assert not store.current_file.exists()


async def test_failed_verification_reinstalls_the_current_release(
    tmp_path: Path, store: ReleaseStore
) -> None:
    publish(store, OLD_WHEEL, current=True)
    host = FakeHost(engines={PROJECT_A: ENGINE_UP}, misreporting=(NEW_VERSION,))

    code = await update_from_wheel(host, store, new_wheel(tmp_path), lambda _message: None)

    assert code == EXIT_FAILURE
    assert host.installs == [NEW_WHEEL, OLD_WHEEL]
    assert host.installed == OLD_VERSION
    assert host.restarts == []
    assert store.current_file.read_text(encoding="utf-8") == f"{OLD_WHEEL}\n"


async def test_engine_that_does_not_come_up_rolls_back_every_engine_restarted(
    tmp_path: Path, store: ReleaseStore
) -> None:
    publish(store, OLD_WHEEL, current=True)
    host = FakeHost(engines={PROJECT_A: ENGINE_UP, PROJECT_B: ENGINE_UP}, broken=(PROJECT_B,))

    code = await update_from_wheel(host, store, new_wheel(tmp_path), lambda _message: None)

    assert code == EXIT_FAILURE
    assert host.installed == OLD_VERSION
    assert host.restarts == [
        (PROJECT_A, NEW_VERSION),
        (PROJECT_B, NEW_VERSION),
        (PROJECT_A, OLD_VERSION),
        (PROJECT_B, OLD_VERSION),
    ]
    assert store.current_file.read_text(encoding="utf-8") == f"{OLD_WHEEL}\n"


async def test_failed_update_without_a_current_release_says_so(
    tmp_path: Path, store: ReleaseStore
) -> None:
    host = FakeHost(engines={}, misreporting=(NEW_VERSION,))
    messages: list[str] = []

    code = await update_from_wheel(host, store, new_wheel(tmp_path), messages.append)

    assert code == EXIT_FAILURE
    assert host.installs == [NEW_WHEEL]
    assert any("nothing to roll back to" in message for message in messages)


async def test_successful_update_keeps_the_five_newest_wheels_and_the_current_one(
    tmp_path: Path, store: ReleaseStore
) -> None:
    for index in range(1, 7):
        publish(store, wheel_name(f"0.0.{index}"), age=1000 + index)
    store.current_file.write_text(f"{wheel_name('0.0.1')}\n", encoding="utf-8")
    host = FakeHost(engines={})

    code = await update_from_wheel(host, store, new_wheel(tmp_path), lambda _message: None)

    assert code == EXIT_SUCCESS
    kept = {wheel.name for wheel in store.root.glob("*.whl")}
    assert kept == {NEW_WHEEL, *(wheel_name(f"0.0.{index}") for index in range(3, 7))}
    assert len(kept) == RETAINED_WHEEL_COUNT
    assert store.current_file.read_text(encoding="utf-8") == f"{NEW_WHEEL}\n"


async def test_rollback_reinstalls_the_current_release_and_restarts_engines_that_are_up(
    store: ReleaseStore,
) -> None:
    publish(store, OLD_WHEEL, current=True)
    host = FakeHost(engines={PROJECT_A: ENGINE_UP, PROJECT_B: ENGINE_DOWN}, installed=NEW_VERSION)

    code = await roll_back_on_demand(host, store, lambda _message: None)

    assert code == EXIT_SUCCESS
    assert host.installs == [OLD_WHEEL]
    assert host.installed == OLD_VERSION
    assert host.restarts == [(PROJECT_A, OLD_VERSION)]


def test_update_without_options_says_the_wheel_is_required() -> None:
    result = CliRunner().invoke(app, ["update"])

    assert result.exit_code == NO_OPTIONS_EXIT
    assert "--wheel" in result.output


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
async def test_commands_run_without_the_checkout_venv_bin_on_path(
    environ: Mapping[str, str], expected_path: str
) -> None:
    commands = HostCommands(environment=environ, runner=SubprocessRunner())

    result = await commands.run((sys.executable, "-c", PRINT_PATH), timeout=COMMAND_TIMEOUT_SECONDS)

    assert result.returncode == 0
    assert result.stdout == expected_path


async def test_timed_out_command_is_a_failed_result_not_an_exception() -> None:
    commands = HostCommands(environment={}, runner=SubprocessRunner())

    result = await commands.run(
        (sys.executable, "-c", SLEEP_PROGRAM), timeout=SHORT_TIMEOUT_SECONDS
    )

    assert result.returncode == TIMEOUT_EXIT_CODE
    assert result.stderr


def test_command_environment_keeps_other_path_entries_in_order() -> None:
    environment = command_environment(
        {"VIRTUAL_ENV": CHECKOUT_VENV, "PATH": os.pathsep.join(["/a", CHECKOUT_VENV_BIN, "/b"])}
    )

    assert environment["PATH"] == os.pathsep.join(["/a", "/b"])
