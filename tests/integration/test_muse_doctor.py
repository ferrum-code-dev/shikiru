from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from shikiru.agents.contracts import AgentContext, CheckResult
from shikiru.plugins.agents.muse.agent import MuseAgent, create
from shikiru.plugins.agents.muse.options import API_KEY_ENV_VAR, MUSE_COMMAND
from shikiru.process import SubprocessRunner
from tests.support.clock import LONG_AGO, FakeClock

_FIXTURES = Path(__file__).parents[1] / "fixtures" / "agents" / "muse"
_LOGGED_IN_VERSION = (_FIXTURES / "muse_version.txt").read_text(encoding="utf-8")
_LOGGED_OUT_STDERR = (_FIXTURES / "usage_logged_out.stderr.log").read_text(encoding="utf-8")
_UNTESTED_VERSION = "Muse Code 1.5.0 (1.5.0-R1)\n"
_SYNTHETIC_API_KEY = "synthetic-test-key"
_VERSION_ENV = "FAKE_MUSE_VERSION"
_USAGE_EXIT_ENV = "FAKE_MUSE_USAGE_EXIT"
_USAGE_STDERR_ENV = "FAKE_MUSE_USAGE_STDERR"
_FAKE_MUSE = """#!{python}
import os
import sys

if sys.argv[1:] == ["--version"]:
    sys.stdout.write(os.environ["FAKE_MUSE_VERSION"])
    sys.exit(0)
if sys.argv[1:] == ["usage", "--json"]:
    sys.stderr.write(os.environ["FAKE_MUSE_USAGE_STDERR"])
    sys.exit(int(os.environ["FAKE_MUSE_USAGE_EXIT"]))
sys.exit(2)
"""


def _agent() -> MuseAgent:
    return create(AgentContext(options={}, runner=SubprocessRunner(), clock=FakeClock(LONG_AGO)))


def _install_fake_muse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    version: str,
    usage_exit: int,
    usage_stderr: str = "",
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    executable = fake_bin / MUSE_COMMAND
    executable.write_text(_FAKE_MUSE.format(python=sys.executable), encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv(_VERSION_ENV, version)
    monkeypatch.setenv(_USAGE_EXIT_ENV, str(usage_exit))
    monkeypatch.setenv(_USAGE_STDERR_ENV, usage_stderr)
    monkeypatch.delenv(API_KEY_ENV_VAR, raising=False)


def _check(checks: list[CheckResult], name: str) -> CheckResult:
    return next(check for check in checks if check.name == name)


@pytest.mark.asyncio
async def test_doctor_reports_every_check_unavailable_when_the_binary_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty_path = tmp_path / "empty"
    empty_path.mkdir()
    monkeypatch.setenv("PATH", str(empty_path))

    checks = await _agent().doctor(())

    assert [check.name for check in checks] == ["binary", "version", "login"]
    assert not _check(checks, "binary").ok
    assert not _check(checks, "version").ok
    assert not _check(checks, "login").ok


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("version_output", "warns"),
    [
        (_LOGGED_IN_VERSION, False),
        (_UNTESTED_VERSION, True),
    ],
)
async def test_version_check_passes_and_warns_only_off_the_tested_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version_output: str, warns: bool
) -> None:
    _install_fake_muse(tmp_path, monkeypatch, version=version_output, usage_exit=0)

    version = _check(await _agent().doctor(()), "version")

    assert version.ok
    assert ("warning" in version.detail) is warns


@pytest.mark.asyncio
async def test_login_check_passes_for_a_stored_login(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_muse(tmp_path, monkeypatch, version=_LOGGED_IN_VERSION, usage_exit=0)

    login = _check(await _agent().doctor(()), "login")

    assert login.ok


@pytest.mark.asyncio
async def test_login_check_fails_when_logged_out_without_an_api_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_muse(
        tmp_path,
        monkeypatch,
        version=_LOGGED_IN_VERSION,
        usage_exit=1,
        usage_stderr=_LOGGED_OUT_STDERR,
    )

    login = _check(await _agent().doctor(()), "login")

    assert not login.ok
    assert "muse login" in login.detail


@pytest.mark.asyncio
async def test_login_check_passes_unverified_when_an_api_key_is_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_muse(
        tmp_path,
        monkeypatch,
        version=_LOGGED_IN_VERSION,
        usage_exit=1,
        usage_stderr=_LOGGED_OUT_STDERR,
    )
    monkeypatch.setenv(API_KEY_ENV_VAR, _SYNTHETIC_API_KEY)

    login = _check(await _agent().doctor(()), "login")

    assert login.ok
    assert "not verified by doctor" in login.detail


@pytest.mark.asyncio
async def test_doctor_passes_every_check_for_a_working_logged_in_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_muse(tmp_path, monkeypatch, version=_LOGGED_IN_VERSION, usage_exit=0)

    checks = await _agent().doctor(())

    assert all(check.ok for check in checks)
    assert [check.name for check in checks] == ["binary", "version", "login"]
