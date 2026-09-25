from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest
from fake_agent.turn_cli import CRASH_STDERR

from shikiru.agents.registry import AgentRegistry
from shikiru.clock import SystemClock
from shikiru.operator.doctor import Doctor
from shikiru.operator.plugin_checks import PluginChecks
from shikiru.operator.probe import AgentProbe
from shikiru.process import SubprocessRunner
from shikiru.workspace.git import Git

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
    tmp_path: Path,
) -> None:
    root = await _project(tmp_path)
    runner = SubprocessRunner()
    clock = SystemClock()
    doctor = Doctor(
        root,
        cow_args=None,
        git=Git(runner),
        runner=runner,
        clock=clock,
        plugin_checks=PluginChecks(AgentRegistry(), runner, clock),
        probe=AgentProbe(AgentRegistry(), runner, clock),
    )

    report = await doctor.run()

    checks = {check.name: check for check in report.checks}
    assert report.ok is True
    assert checks["cow"].ok is True


async def test_doctor_probe_passes_when_each_ladder_agent_submits_a_valid_report(
    tmp_path: Path,
) -> None:
    root = await _project(tmp_path)

    code, stdout, _ = await _cli(root, "--json", "doctor", "--probe")

    checks = {check["name"]: check for check in json.loads(stdout)["checks"]}
    assert code == 0
    assert checks["probe:fake"]["ok"] is True


async def test_doctor_probe_failure_shows_the_stream_tail_and_stderr(tmp_path: Path) -> None:
    root = await _project(tmp_path)

    code, stdout, _ = await _cli(
        root, "--json", "doctor", "--probe", SHIKIRU_FAKE_SCRIPT='[{"kind": "crash"}]'
    )

    probe = next(check for check in json.loads(stdout)["checks"] if check["name"] == "probe:fake")
    assert code == 1
    assert probe["ok"] is False
    assert '"event":"session_started"' in probe["detail"]
    assert CRASH_STDERR in probe["detail"]
