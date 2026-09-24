from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import TYPE_CHECKING

import pytest

from shikiru.process import SubprocessRunner

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.asyncio


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

    code, stdout, stderr = await _cli(tmp_path, "submit", "human task")
    assert code == 0
    assert stderr == ""
    assert stdout.strip() == "T-1"

    code, stdout, stderr = await _cli(tmp_path, "status")
    assert code == 0
    assert stderr == ""
    assert stdout.strip()

    code, stdout, stderr = await _cli(tmp_path, "stop")
    assert code == 0
    assert stderr == ""
    assert stdout.strip()


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
