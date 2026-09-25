from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

from shikiru.bootstrap import build_doctor
from shikiru.process import SubprocessRunner

pytestmark = pytest.mark.asyncio

SHORT_DIRECTORY_PREFIX = "s"

FAKE_CONFIG = """
[project]
name = "doctor-project"

[agents.fake]

[roles.leader]
ladder = [{agent = "fake", model = "default"}]

[roles.implementer]
escalate_after = 2
ladder = [{agent = "fake", model = "default"}]

[roles.helper]
ladder = [{agent = "fake", model = "default"}]
"""


async def _project(tmp_path: Path, config: str = FAKE_CONFIG) -> Path:
    await _git_init(tmp_path)
    return _write_config(tmp_path, config)


async def _git_init(root: Path) -> None:
    await SubprocessRunner().run(["git", "init", "-q"], timeout=10, cwd=root, check=True)


def _write_config(root: Path, config: str = FAKE_CONFIG) -> Path:
    config_dir = root / ".shikiru"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text(config)
    return root


async def _cli(root: Path, *args: str, **extra_environment: str) -> tuple[int, str, str]:
    environment = dict(os.environ, NO_COLOR="1", **extra_environment)
    environment["PATH"] = f"{Path(sys.executable).parent}{os.pathsep}{environment['PATH']}"
    result = await SubprocessRunner().run(
        [sys.executable, "-m", "shikiru", "--project", str(root), *args],
        timeout=60,
        cwd=root,
        env=environment,
    )
    return result.returncode, result.stdout, result.stderr


async def test_doctor_reports_core_and_configured_plugin_checks(tmp_path: Path) -> None:
    root = await _project(tmp_path)

    code, stdout, stderr = await _cli(root, "--json", "doctor")

    assert stderr == ""
    report = json.loads(stdout)
    checks = {check["name"]: check for check in report["checks"]}
    assert {
        "tmux",
        "git",
        "repository",
        "config",
        "db",
        "cow",
        "plugin:fake",
        "plugin:fake:binary",
        "plugins",
    } <= checks.keys()
    for name in (
        "tmux",
        "git",
        "repository",
        "config",
        "db",
        "plugin:fake:binary",
        "plugin:fake",
        "plugins",
    ):
        assert checks[name]["ok"] is True
        assert checks[name]["detail"]
    assert checks["cow"]["detail"]
    assert report["ok"] is True
    assert all(check["ok"] for check in checks.values())
    assert code == 0
    assert (root / ".shikiru" / "state.db").is_file()


def _sockets_in(directory: Path) -> list[Path]:
    return [path for path in directory.rglob("*") if path.is_socket()]


async def test_doctor_leaves_no_tmux_socket_behind(tmp_path: Path) -> None:
    root = await _project(tmp_path)
    with tempfile.TemporaryDirectory(prefix=SHORT_DIRECTORY_PREFIX) as sockets:
        code, _, _ = await _cli(root, "doctor", TMUX_TMPDIR=sockets)

        assert code == 0
        assert await asyncio.to_thread(_sockets_in, Path(sockets)) == []


async def test_doctor_fails_a_project_root_inside_a_repository(tmp_path: Path) -> None:
    await _git_init(tmp_path)
    nested = tmp_path / "nested"
    nested.mkdir()
    root = _write_config(nested)

    code, stdout, _ = await _cli(root, "--json", "doctor")

    checks = {check["name"]: check for check in json.loads(stdout)["checks"]}
    assert checks["repository"]["ok"] is False
    assert str(root) in checks["repository"]["detail"]
    assert code != 0


async def test_doctor_names_invalid_configuration_and_keeps_diagnostics_json_safe(
    tmp_path: Path,
) -> None:
    root = await _project(tmp_path, '[project\nname = "broken"\n')

    code, stdout, stderr = await _cli(root, "--json", "doctor")

    assert code == 1
    assert stderr == ""
    report = json.loads(stdout)
    config_checks = [check for check in report["checks"] if check["name"] == "config"]
    assert len(config_checks) == 1
    assert config_checks[0]["ok"] is False
    assert report["ok"] is False
    assert "broken" not in stdout


async def test_doctor_keeps_unsupported_cow_as_a_passing_degraded_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = await _project(tmp_path)
    monkeypatch.setattr("shikiru.operator.doctor.default_cow_args", lambda: None)

    report = await build_doctor(root).run()

    checks = {check.name: check for check in report.checks}
    assert report.ok is True
    assert checks["cow"].ok is True
