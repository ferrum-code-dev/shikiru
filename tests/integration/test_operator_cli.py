from __future__ import annotations

import asyncio
import json
import os
import shlex
import sys
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

import shikiru.cli.app as cli_app
from shikiru.bootstrap import build_operator
from shikiru.config.loader import load_settings
from shikiru.operator.lifecycle import PALETTE_COMMAND, ProjectLifecycle, StartResult
from shikiru.process import SubprocessRunner
from shikiru.tmux.client import Tmux, exact_target
from shikiru.tmux.session import (
    DETACHED_SIZE,
    HOME_WINDOW_NAME,
    PROJECT_DIR_ENV_VAR,
    SessionView,
    session_name,
)
from tests.support.clock import FakeClock

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.config.settings import Settings
    from shikiru.operator.services import OperatorServices
    from tests.support.tmux import TmuxServer

pytestmark = pytest.mark.asyncio

START = datetime(2026, 1, 1, tzinfo=UTC)


async def _git_project(root: Path) -> None:
    await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True)
    runner = SubprocessRunner()
    await runner.run(["git", "init", "-q"], timeout=10, cwd=root, check=True)
    (root / "README.md").write_text("operator test\n")
    await runner.run(["git", "add", "README.md"], timeout=10, cwd=root, check=True)
    await runner.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "initial",
        ],
        timeout=10,
        cwd=root,
        check=True,
    )


async def _cli(root: Path, *args: str) -> tuple[int, str, str]:
    env = dict(os.environ, NO_COLOR="1")
    result = await SubprocessRunner().run(
        [sys.executable, "-m", "shikiru", "--project", str(root), *args],
        timeout=20,
        cwd=root,
        env=env,
    )
    return result.returncode, result.stdout, result.stderr


async def test_cli_human_init_submit_status_and_already_stopped(tmp_path: Path) -> None:
    await _git_project(tmp_path)

    code, stdout, stderr = await _cli(tmp_path, "init", "--yes")
    assert code == 0
    assert stderr == ""
    assert str(tmp_path / ".shikiru" / "config.toml") in stdout
    assert "model placeholders" in stdout
    assert "shikiru doctor" in stdout

    code, stdout, stderr = await _cli(tmp_path, "submit", "human task")
    assert code == 0
    assert stderr == ""
    assert stdout.strip() == "T-1"

    code, stdout, stderr = await _cli(tmp_path, "status")
    assert code == 0
    assert stderr == ""
    assert "engine down" in stdout
    assert "queued 0" in stdout
    assert "none" in stdout

    code, stdout, stderr = await _cli(tmp_path, "stop")
    assert code == 0
    assert stderr == ""
    assert "engine already stopped" in stdout


async def test_cli_json_init_submit_status_and_stop(tmp_path: Path) -> None:
    await _git_project(tmp_path)

    code, stdout, stderr = await _cli(tmp_path, "--json", "init", "--yes")
    assert code == 0
    assert stderr == ""
    init_value = json.loads(stdout)
    assert init_value["config"].endswith(".shikiru/config.toml")

    code, stdout, stderr = await _cli(tmp_path, "--json", "submit", "json task")
    assert code == 0
    assert stderr == ""
    submit_value = json.loads(stdout)
    assert submit_value["task_id"] == "T-1"
    assert submit_value["status"] == "queued"

    code, stdout, stderr = await _cli(tmp_path, "--json", "status")
    assert code == 0
    assert stderr == ""
    status_value = json.loads(stdout)
    assert status_value["engine_up"] is False
    assert status_value["slots"] == {"active": 0, "maximum": 2}

    code, stdout, stderr = await _cli(tmp_path, "--json", "stop")
    assert code == 0
    assert stderr == ""
    stop_value = json.loads(stdout)
    assert stop_value["message"]
    assert stop_value["result"] == {"stopped": False}


async def test_palette_uses_the_invoking_tmux_project_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects = [tmp_path / "one", tmp_path / "two"]
    for project in projects:
        (project / ".shikiru").mkdir(parents=True)
    calls: list[tuple[Path, tuple[str, ...]]] = []

    async def run_palette(project_root: Path, overrides: tuple[str, ...]) -> None:
        calls.append((project_root, overrides))

    monkeypatch.setattr(cli_app, "run_palette", run_palette)
    runner = CliRunner()

    for project in projects:
        result = await asyncio.to_thread(
            runner.invoke,
            cli_app.app,
            ["--set", "project.name='from-override'", "palette"],
            env={PROJECT_DIR_ENV_VAR: str(project)},
        )

        assert result.exit_code == 0

    assert calls == [
        (projects[0], ("project.name='from-override'",)),
        (projects[1], ("project.name='from-override'",)),
    ]


async def test_start_reports_that_an_existing_engine_was_reused(
    tmp_path: Path,
    tmux_server: TmuxServer,
) -> None:
    await _git_project(tmp_path)
    config = tmp_path / ".shikiru" / "config.toml"
    config.parent.mkdir()
    config.write_text(
        """
        [project]
        name = "demo"

        [agents.fake]

        [roles.leader]
        ladder = [{agent = "fake", model = "default"}]

        [roles.implementer]
        escalate_after = 2
        ladder = [{agent = "fake", model = "default"}]

        [roles.helper]
        ladder = [{agent = "fake", model = "default"}]
        """
    )
    settings = load_settings(tmp_path)
    tmux = Tmux(tmux_server.runner, socket=tmux_server.socket_name, env=tmux_server.env)
    await tmux.new_session(
        session_name(settings.project.name),
        cwd=tmp_path,
        env={PROJECT_DIR_ENV_VAR: str(tmp_path)},
        window_name=HOME_WINDOW_NAME,
        size=DETACHED_SIZE,
    )

    async def validate_plugins(_settings: object) -> None:
        return None

    def build_services(project_root: Path, project_settings: Settings) -> OperatorServices:
        return build_operator(
            project_root,
            project_settings,
            clock=clock,
        )

    clock = FakeClock(START)

    lifecycle = ProjectLifecycle(
        runner=tmux_server.runner,
        tmux=tmux,
        session=SessionView(tmux),
        clock=clock,
        operator_factory=build_services,
        plugin_validator=validate_plugins,
        engine_up=lambda _root: True,
    )

    result = await lifecycle.start(tmp_path, (), attach=False)

    assert isinstance(result, StartResult)
    assert result.started is False
    assert result.engine == "up"
    assert result.session == exact_target(session_name(settings.project.name))
    key_lines = await tmux_server.run("list-keys", "-T", "prefix")
    binding = next(line for line in key_lines.stdout.splitlines() if " Space " in line)
    assert f"display-popup -E {shlex.join(PALETTE_COMMAND)}" in shlex.split(binding)


@pytest.mark.parametrize("repo_relative", ["child", ".."])
async def test_init_rejects_a_project_root_that_is_not_a_repository_root(
    tmp_path: Path, repo_relative: str
) -> None:
    project = tmp_path / "workspace" / "project"
    await asyncio.to_thread(project.mkdir, parents=True)
    await _git_project((project / repo_relative).resolve())

    code, _, stderr = await _cli(project, "init", "--yes")

    assert code != 0
    assert str(project) in stderr
    assert not (project / ".shikiru" / "config.toml").exists()
