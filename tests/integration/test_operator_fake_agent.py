from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
from typing import TYPE_CHECKING, cast

import pytest

from shikiru.process import SubprocessRunner
from shikiru.tmux.client import exact_target
from shikiru.tmux.session import session_name
from tests.support.flow import init_project_repo
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from tests.support.tmux import TmuxServer

pytestmark = pytest.mark.asyncio

CLI_TIMEOUT_SECONDS = 30.0
TASK_TIMEOUT_SECONDS = 90.0


def _tmux_environment(project_root: Path, tmux_server: TmuxServer) -> dict[str, str]:
    tmux_binary = shutil.which("tmux")
    assert tmux_binary is not None
    shim_dir = project_root.parent / f"{project_root.name}-operator-bin"
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
    environment["PATH"] = f"{shim_dir}{os.pathsep}{environment['PATH']}"
    return environment


async def _cli(
    project_root: Path, environment: dict[str, str], *arguments: str
) -> tuple[int, str, str]:
    result = await SubprocessRunner().run(
        [sys.executable, "-m", "shikiru", "--project", str(project_root), *arguments],
        timeout=CLI_TIMEOUT_SECONDS,
        cwd=project_root,
        env=environment,
    )
    return result.returncode, result.stdout, result.stderr


async def _task_status(project_root: Path, environment: dict[str, str]) -> str | None:
    code, stdout, _ = await _cli(project_root, environment, "--json", "status", "T-1")
    if code != 0:
        return None
    value = cast("dict[str, object]", json.loads(stdout))
    detail = value.get("task")
    if not isinstance(detail, dict):
        return None
    detail_data = cast("dict[str, object]", detail)
    task = detail_data.get("task")
    if not isinstance(task, dict):
        return None
    task_data = cast("dict[str, object]", task)
    status = task_data.get("status")
    return status if isinstance(status, str) else None


async def test_operator_cli_fake_agent_completes_and_integrates(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    await init_project_repo(tmp_path)
    environment = _tmux_environment(tmp_path, tmux_server)
    runner = SubprocessRunner()
    initial = await runner.run(
        ["git", "rev-parse", "HEAD"], timeout=CLI_TIMEOUT_SECONDS, cwd=tmp_path, check=True
    )
    overrides = (
        "--set",
        "orchestration.poll_seconds=1",
        "--set",
        "orchestration.resources.max_cpu_pct=100",
        "--set",
        "orchestration.resources.min_free_ram_gb=0",
    )
    started = False
    session_stopped = False
    try:
        code, stdout, stderr = await _cli(tmp_path, environment, "init", "--yes")
        assert code == 0, stderr
        assert stdout

        started = True
        code, _, stderr = await _cli(tmp_path, environment, *overrides, "start")
        assert code == 0, stderr

        code, stdout, stderr = await _cli(
            tmp_path, environment, *overrides, "submit", "make a change"
        )
        assert code == 0, stderr
        assert stdout.strip() == "T-1"

        async def task_done() -> bool:
            return await _task_status(tmp_path, environment) == "done"

        await wait_until(task_done, timeout=TASK_TIMEOUT_SECONDS)

        current = await runner.run(
            ["git", "rev-parse", "HEAD"], timeout=CLI_TIMEOUT_SECONDS, cwd=tmp_path, check=True
        )
        assert current.stdout.strip() != initial.stdout.strip()
        subject = await runner.run(
            ["git", "log", "-1", "--format=%s"],
            timeout=CLI_TIMEOUT_SECONDS,
            cwd=tmp_path,
            check=True,
        )
        assert subject.stdout.startswith("fake:")

        target = exact_target(session_name(tmp_path.name))
        windows = await tmux_server.run(
            "list-windows", "-t", target, "-F", "#{window_name}", check=True
        )
        assert not any(name.startswith("T-1") for name in windows.stdout.splitlines())

        code, _, stderr = await _cli(tmp_path, environment, *overrides, "stop")
        assert code == 0, stderr

        async def engine_stopped() -> bool:
            code, stdout, _ = await _cli(tmp_path, environment, "--json", "status")
            if code != 0:
                return False
            value = cast("dict[str, object]", json.loads(stdout))
            return value.get("engine_up") is False

        await wait_until(engine_stopped, timeout=CLI_TIMEOUT_SECONDS)
        code, _, stderr = await _cli(tmp_path, environment, *overrides, "stop", "--kill-session")
        assert code == 0, stderr
        session_stopped = True
        session = await tmux_server.run("has-session", "-t", target, check=False)
        assert session.returncode != 0
    finally:
        if started and not session_stopped:
            code, _, stderr = await _cli(
                tmp_path, environment, *overrides, "stop", "--kill-session"
            )
            assert code == 0, stderr
