"""A copy of the sample project, a private-tmux environment and the real `shikiru` CLI."""

from __future__ import annotations

import os
import shlex
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from fake_agent.scripted import TICK_A1_ENABLED_VALUE, TICK_A1_ENV_VAR

from shikiru.process import SubprocessRunner
from tests.support.git_repo import commit_all, init_repo

if TYPE_CHECKING:
    from tests.support.tmux import TmuxServer

CLI_TIMEOUT_SECONDS = 30.0
BASE_BRANCH = "main"
FIXTURE_NAME = "sample-project"


@dataclass(frozen=True, slots=True)
class PreparedProject:
    root: Path
    initial_sha: str


async def prepare_project(tmp_path: Path) -> PreparedProject:
    source = Path(__file__).parents[1] / "fixtures" / FIXTURE_NAME
    root = tmp_path / "project"
    shutil.copytree(source, root)
    runner = SubprocessRunner()
    await init_repo(runner, root, initial_branch=BASE_BRANCH)
    await commit_all(runner, root, "fixture")
    result = await runner.run(
        ["git", "rev-parse", "HEAD"], timeout=CLI_TIMEOUT_SECONDS, cwd=root, check=True
    )
    return PreparedProject(root, result.stdout.strip())


def tmux_environment(project_root: Path, tmux_server: TmuxServer) -> dict[str, str]:
    tmux_binary = shutil.which("tmux")
    assert tmux_binary is not None
    shim_dir = project_root.parent / "tmux-bin"
    shim_dir.mkdir()
    shim = shim_dir / "tmux"
    shim.write_text(
        f"#!/bin/sh\nexec {shlex.quote(tmux_binary)} -L "
        f'{shlex.quote(tmux_server.socket_name)} "$@"\n'
    )
    shim.chmod(0o755)
    environment = dict(os.environ)
    environment.pop("TMUX", None)
    environment.pop("TMUX_PANE", None)
    environment["NO_COLOR"] = "1"
    environment[TICK_A1_ENV_VAR] = TICK_A1_ENABLED_VALUE
    environment["PATH"] = f"{shim_dir}{os.pathsep}{environment['PATH']}"
    return environment


async def run_cli(
    project_root: Path, environment: dict[str, str], *arguments: str
) -> tuple[int, str, str]:
    result = await SubprocessRunner().run(
        [sys.executable, "-m", "shikiru", "--project", str(project_root), *arguments],
        timeout=CLI_TIMEOUT_SECONDS,
        cwd=project_root,
        env=environment,
    )
    return result.returncode, result.stdout, result.stderr
