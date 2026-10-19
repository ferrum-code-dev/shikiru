from __future__ import annotations

import asyncio
import json
import os
import shutil
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
from shikiru.workspace.caches import CacheCloner
from shikiru.workspace.git import Git
from tests.support.git_repo import CACHE_COPY_TIMEOUT, GIT_COMMAND_TIMEOUT

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
    human_code, human_stdout, human_stderr = await _cli(root, "doctor")

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
    assert human_code == 0
    assert human_stderr == ""
    human_text = "".join(human_stdout.split())
    for check in checks.values():
        assert check["name"] in human_stdout
        assert "".join(str(check["detail"]).split()) in human_text


FAKE_VERSION_SCRIPT = """#!/bin/sh
if [ "$1" = "{flag}" ]; then echo "{output}"; exit 0; fi
exec {real} "$@"
"""


def _tool_reporting(directory: Path, tool: str, flag: str, output: str) -> Path:
    real = shutil.which(tool)
    assert real is not None
    script = directory / tool
    script.write_text(FAKE_VERSION_SCRIPT.format(flag=flag, output=output, real=real))
    script.chmod(0o755)
    return directory


@pytest.mark.parametrize(
    ("tool", "flag", "output", "ok"),
    [
        ("tmux", "-V", "tmux 3.3a", False),
        ("tmux", "-V", "tmux 3.4", True),
        ("tmux", "-V", "tmux 3.10", True),
        ("tmux", "-V", "tmux master", False),
        ("git", "--version", "git version 2.44.0", False),
        ("git", "--version", "git version 2.45.0", True),
        ("git", "--version", "git version 2.100.1", True),
    ],
)
async def test_doctor_fails_a_tool_older_than_the_documented_floor(
    tmp_path: Path, tool: str, flag: str, output: str, ok: bool
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    root = await _project(project)
    shims = tmp_path / "shims"
    shims.mkdir()
    _tool_reporting(shims, tool, flag, output)

    code, stdout, _ = await _cli(
        root, "--json", "doctor", PATH=f"{shims}{os.pathsep}{os.environ['PATH']}"
    )

    check = {check["name"]: check for check in json.loads(stdout)["checks"]}[tool]
    assert check["ok"] is ok
    assert (code == 0) is ok
    if not ok:
        assert output in check["detail"]


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
    project = tmp_path / "[literal]"
    project.mkdir()
    root = await _project(project, '[project\nname = "broken"\n')

    code, stdout, stderr = await _cli(root, "--json", "doctor")
    human_code, human_stdout, human_stderr = await _cli(root, "doctor")

    assert code == 1
    assert stderr == ""
    report = json.loads(stdout)
    config_checks = [check for check in report["checks"] if check["name"] == "config"]
    assert len(config_checks) == 1
    assert config_checks[0]["ok"] is False
    assert report["ok"] is False
    assert "broken" not in stdout
    assert human_code == 1
    assert human_stderr == ""
    human_text = "".join(human_stdout.split())
    config_detail = config_checks[0]["detail"]
    assert "".join(str(config_detail).split()) in human_text
    assert "[literal]" in human_stdout


async def test_doctor_keeps_unsupported_cow_as_a_passing_degraded_check(
    tmp_path: Path,
) -> None:
    root = await _project(tmp_path)
    runner = SubprocessRunner()
    clock = SystemClock()
    doctor = Doctor(
        root,
        cache_cloner=CacheCloner(runner, cow_args=None, copy_timeout=CACHE_COPY_TIMEOUT),
        git=Git(runner, command_timeout=GIT_COMMAND_TIMEOUT),
        runner=runner,
        clock=clock,
        plugin_checks=PluginChecks(AgentRegistry(), runner, clock),
        probe=AgentProbe(AgentRegistry(), runner, clock),
    )

    report = await doctor.run()

    checks = {check.name: check for check in report.checks}
    assert report.ok is True
    assert checks["cow"].ok is True


MULTI_RUNG_CONFIG = """
[project]
name = "doctor-project"

[agents.fake]

[roles.leader]
ladder = [{agent = "fake", model = "default"}]

[roles.implementer]
escalate_after = 2
ladder = [{agent = "fake", model = "default"}, {agent = "fake", model = "strong", effort = "high"}]

[roles.implementer.complexity.high]
ladder = [{agent = "fake", model = "strong", effort = "high"}]

[roles.helper]
ladder = [{agent = "fake", model = "default", effort = "low"}]
"""
MULTI_RUNG_PROBES = ["probe:fake:default", "probe:fake:strong:high", "probe:fake:default:low"]


def _probe_checks(stdout: str) -> dict[str, dict[str, object]]:
    checks = json.loads(stdout)["checks"]
    return {check["name"]: check for check in checks if check["name"].startswith("probe:")}


async def test_doctor_probe_passes_when_each_ladder_agent_submits_a_valid_report(
    tmp_path: Path,
) -> None:
    root = await _project(tmp_path)

    code, stdout, _ = await _cli(root, "--json", "doctor", "--probe")

    assert code == 0
    assert _probe_checks(stdout)["probe:fake:default"]["ok"] is True


async def test_doctor_probe_runs_one_turn_per_distinct_ladder_rung(tmp_path: Path) -> None:
    root = await _project(tmp_path, MULTI_RUNG_CONFIG)

    code, stdout, _ = await _cli(root, "--json", "doctor", "--probe")

    probes = [check for check in json.loads(stdout)["checks"] if check["name"].startswith("probe:")]
    assert code == 0
    assert [probe["name"] for probe in probes] == MULTI_RUNG_PROBES
    assert all(probe["ok"] for probe in probes)


async def test_doctor_probe_names_the_failing_rung(tmp_path: Path) -> None:
    root = await _project(tmp_path, MULTI_RUNG_CONFIG)

    code, stdout, _ = await _cli(
        root,
        "--json",
        "doctor",
        "--probe",
        SHIKIRU_FAKE_SCRIPT='[{"kind": "accept"}, {"kind": "crash"}]',
    )

    probes = _probe_checks(stdout)
    assert code == 1
    assert {name for name, probe in probes.items() if not probe["ok"]} == {"probe:fake:strong:high"}
    assert CRASH_STDERR in str(probes["probe:fake:strong:high"]["detail"])


async def test_doctor_probe_failure_shows_the_stream_tail_and_stderr(tmp_path: Path) -> None:
    root = await _project(tmp_path)
    human_project = tmp_path / "human"
    human_project.mkdir()
    human_root = await _project(human_project)

    code, stdout, _ = await _cli(
        root, "--json", "doctor", "--probe", SHIKIRU_FAKE_SCRIPT='[{"kind": "crash"}]'
    )
    human_code, human_stdout, human_stderr = await _cli(
        human_root, "doctor", "--probe", SHIKIRU_FAKE_SCRIPT='[{"kind": "crash"}]'
    )

    probe = _probe_checks(stdout)["probe:fake:default"]
    assert code == 1
    assert probe["ok"] is False
    assert '"event":"session_started"' in str(probe["detail"])
    assert CRASH_STDERR in str(probe["detail"])
    assert human_code == 1
    assert human_stderr == ""
    human_text = "".join(human_stdout.split())
    assert '"event":"session_started"' in human_text
    assert "".join(CRASH_STDERR.split()) in human_text
