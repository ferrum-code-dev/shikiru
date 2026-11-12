"""Muse quota probe through the real process runner and a fake `muse` executable on `PATH`."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from shikiru.agents.contracts import AgentContext, QuotaReading
from shikiru.errors import ShikiruError
from shikiru.plugins.agents.muse.agent import create
from shikiru.plugins.agents.muse.options import MUSE_COMMAND
from shikiru.process import SubprocessRunner
from tests.support.clock import LONG_AGO, FakeClock

_FIXTURES = Path(__file__).parents[1] / "fixtures" / "agents" / "muse"
_LOGGED_IN_USAGE = (_FIXTURES / "usage_logged_in.json").read_text(encoding="utf-8")
_LOGGED_OUT_STDERR = (_FIXTURES / "usage_logged_out.stderr.log").read_text(encoding="utf-8")
_NULL_ALLOWANCE_USAGE = json.dumps({"schemaVersion": 1, "allowance": None, "reason": None})
_STDOUT_ENV = "FAKE_MUSE_USAGE_STDOUT"
_STDERR_ENV = "FAKE_MUSE_USAGE_STDERR"
_EXIT_ENV = "FAKE_MUSE_USAGE_EXIT"
_FAKE_MUSE = """#!{python}
import os
import sys

if sys.argv[1:] == ["usage", "--json"]:
    sys.stdout.write(os.environ["FAKE_MUSE_USAGE_STDOUT"])
    sys.stderr.write(os.environ["FAKE_MUSE_USAGE_STDERR"])
    sys.exit(int(os.environ["FAKE_MUSE_USAGE_EXIT"]))
sys.exit(2)
"""


def _install_fake_muse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    stdout: str = "",
    stderr: str = "",
    exit_code: int = 0,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    executable = fake_bin / MUSE_COMMAND
    executable.write_text(_FAKE_MUSE.format(python=sys.executable), encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv(_STDOUT_ENV, stdout)
    monkeypatch.setenv(_STDERR_ENV, stderr)
    monkeypatch.setenv(_EXIT_ENV, str(exit_code))


def _read_quota() -> QuotaReading | None:
    agent = create(AgentContext(options={}, runner=SubprocessRunner(), clock=FakeClock(LONG_AGO)))
    return agent.read_quota(LONG_AGO)


def test_quota_probe_returns_the_logged_in_allowance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_muse(tmp_path, monkeypatch, stdout=_LOGGED_IN_USAGE)

    reading = _read_quota()

    assert reading is not None
    assert sorted(window.name for window in reading.windows) == ["5h", "7d"]


def test_quota_probe_has_no_reading_for_a_null_allowance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_muse(tmp_path, monkeypatch, stdout=_NULL_ALLOWANCE_USAGE)

    assert _read_quota() is None


def test_quota_probe_raises_when_logged_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_muse(tmp_path, monkeypatch, stderr=_LOGGED_OUT_STDERR, exit_code=1)

    with pytest.raises(ShikiruError, match="muse login"):
        _read_quota()


def test_quota_probe_raises_when_the_binary_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty_path = tmp_path / "empty"
    empty_path.mkdir()
    monkeypatch.setenv("PATH", str(empty_path))

    with pytest.raises(ShikiruError):
        _read_quota()
