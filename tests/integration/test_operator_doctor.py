from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from shikiru.bootstrap import build_doctor
from shikiru.process import SubprocessRunner

pytestmark = pytest.mark.asyncio

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
    config_dir = tmp_path / ".shikiru"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text(config)
    return tmp_path


async def _cli(root: Path, *args: str) -> tuple[int, str, str]:
    environment = dict(os.environ, NO_COLOR="1")
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
        "config",
        "db",
        "cow",
        "plugin:fake",
        "plugin:fake:binary",
        "plugins",
    } <= checks.keys()
    for name in ("tmux", "git", "config", "db", "plugin:fake:binary", "plugin:fake", "plugins"):
        assert checks[name]["ok"] is True
        assert checks[name]["detail"]
    assert checks["cow"]["detail"]
    assert report["ok"] is True
    assert all(check["ok"] for check in checks.values())
    assert code == 0
    assert (root / ".shikiru" / "state.db").is_file()


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


async def test_doctor_probe_reports_each_configured_plugin(tmp_path: Path) -> None:
    root = await _project(tmp_path)

    code, stdout, stderr = await _cli(root, "--json", "doctor", "--probe")

    report = json.loads(stdout)
    probe_checks = [check for check in report["checks"] if check["name"] == "plugin:fake:probe"]
    assert len(probe_checks) == 1
    assert code == 0
    assert stderr == ""
    assert report["ok"] is True
    assert all(check["ok"] for check in report["checks"])
    assert probe_checks[0]["ok"] is True
    assert probe_checks[0]["detail"]


async def test_doctor_probe_reuses_the_project_probe_repository(tmp_path: Path) -> None:
    root = await _project(tmp_path)
    repo_git = root / ".shikiru" / "probe" / "repo" / ".git"

    first_code, _, _ = await _cli(root, "--json", "doctor", "--probe")
    first_inode = repo_git.stat().st_ino
    second_code, stdout, _ = await _cli(root, "--json", "doctor", "--probe")

    assert (first_code, second_code) == (0, 0)
    assert json.loads(stdout)["ok"] is True
    assert repo_git.stat().st_ino == first_inode


async def test_doctor_keeps_unsupported_cow_as_a_passing_degraded_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = await _project(tmp_path)
    monkeypatch.setattr("shikiru.operator.doctor.default_cow_args", lambda: None)

    report = await build_doctor(root).run()

    checks = {check.name: check for check in report.checks}
    assert report.ok is True
    assert checks["cow"].ok is True
