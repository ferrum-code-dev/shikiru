from __future__ import annotations

import json
import os
import sys
from typing import TYPE_CHECKING

import pytest

from shikiru.process import SubprocessRunner

if TYPE_CHECKING:
    from pathlib import Path

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
    names = {check["name"] for check in report["checks"]}
    assert {"tmux", "git", "config", "db", "cow", "plugin:fake", "plugins"} <= names
    assert (root / ".shikiru" / "state.db").is_file()
    assert code in (0, 1)


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
    assert "broken" not in stdout


async def test_doctor_probe_reports_each_configured_plugin(tmp_path: Path) -> None:
    root = await _project(tmp_path)

    _code, stdout, _stderr = await _cli(root, "--json", "doctor", "--probe")

    report = json.loads(stdout)
    probe_checks = [check for check in report["checks"] if check["name"] == "plugin:fake:probe"]
    assert len(probe_checks) == 1
