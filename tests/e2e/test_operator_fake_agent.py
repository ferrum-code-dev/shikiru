from __future__ import annotations

import asyncio
import json
import os
import shlex
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from fake_agent.repl import TICK_A1_ENABLED_VALUE, TICK_A1_ENV_VAR

from shikiru.process import SubprocessRunner
from shikiru.tmux.client import exact_target
from shikiru.tmux.session import session_name
from tests.support.git_repo import commit_all, init_repo
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from tests.support.tmux import TmuxServer

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]

CLI_TIMEOUT_SECONDS = 30.0
TASK_TIMEOUT_SECONDS = 45.0
BASE_BRANCH = "main"
TASK_ID = "T-1"
ACTING_ROLES = ("leader", "implementer")
TMUX_COMMAND_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class Scenario:
    fixture_name: str
    repository_paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PreparedProject:
    root: Path
    repositories: tuple[Path, ...]
    initial_shas: tuple[str, ...]


SCENARIOS = (
    Scenario("sample-project", (".",)),
    Scenario("sample-project-multi", ("backend", "frontend")),
)


def _repository_path(root: Path, relative_path: str) -> Path:
    return root if relative_path == "." else root / relative_path


async def _prepare_project(tmp_path: Path, scenario: Scenario) -> PreparedProject:
    source = Path(__file__).parents[1] / "fixtures" / scenario.fixture_name
    root = tmp_path / "project"
    shutil.copytree(source, root)
    runner = SubprocessRunner()
    repositories: list[Path] = []
    initial_shas: list[str] = []
    for relative_path in scenario.repository_paths:
        repository = _repository_path(root, relative_path)
        await init_repo(runner, repository, initial_branch=BASE_BRANCH)
        await commit_all(runner, repository, "fixture")
        result = await runner.run(
            ["git", "rev-parse", "HEAD"],
            timeout=CLI_TIMEOUT_SECONDS,
            cwd=repository,
            check=True,
        )
        repositories.append(repository)
        initial_shas.append(result.stdout.strip())
    return PreparedProject(root, tuple(repositories), tuple(initial_shas))


def _tmux_environment(project_root: Path, tmux_server: TmuxServer) -> dict[str, str]:
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


async def _status(project_root: Path, environment: dict[str, str]) -> dict[str, object] | None:
    code, stdout, _ = await _cli(project_root, environment, "--json", "status", TASK_ID)
    if code != 0:
        return None
    value = json.loads(stdout)
    return cast("dict[str, object]", value) if isinstance(value, dict) else None


def _task_data(status: dict[str, object]) -> dict[str, object] | None:
    detail = _task_detail(status)
    if detail is None:
        return None
    task = detail.get("task")
    return cast("dict[str, object]", task) if isinstance(task, dict) else None


def _task_detail(status: dict[str, object]) -> dict[str, object] | None:
    detail = status.get("task")
    return cast("dict[str, object]", detail) if isinstance(detail, dict) else None


async def _engine_is_down(project_root: Path, environment: dict[str, str]) -> bool:
    code, stdout, _ = await _cli(project_root, environment, "--json", "status")
    if code != 0:
        return False
    value = json.loads(stdout)
    if not isinstance(value, dict):
        return False
    status = cast("dict[str, object]", value)
    return status.get("engine_up") is False


async def _cleanup_project(
    project: PreparedProject, environment: dict[str, str], target: str
) -> None:
    code, _, stderr = await _cli(project.root, environment, "stop", "--kill-session")
    assert code == 0, stderr
    await wait_until(
        lambda: _engine_is_down(project.root, environment), timeout=CLI_TIMEOUT_SECONDS
    )
    runner = SubprocessRunner()

    async def session_gone() -> bool:
        result = await runner.run(
            ["tmux", "has-session", "-t", target],
            timeout=TMUX_COMMAND_TIMEOUT_SECONDS,
            env=environment,
        )
        return result.returncode != 0

    await wait_until(session_gone, timeout=CLI_TIMEOUT_SECONDS)


async def _assert_tracker_ticked(project: PreparedProject) -> None:
    tracker = project.root / "tasks" / "todo.md"
    contents = await asyncio.to_thread(tracker.read_text)
    assert "- [x] A1" in contents
    if len(project.repositories) == 1:
        result = await SubprocessRunner().run(
            ["git", "show", "HEAD:tasks/todo.md"],
            timeout=CLI_TIMEOUT_SECONDS,
            cwd=project.repositories[0],
            check=True,
        )
        assert "- [x] A1" in result.stdout


def _assert_usage(task: dict[str, object]) -> None:
    sessions = task.get("sessions")
    assert isinstance(sessions, list)
    session_values = cast("list[object]", sessions)
    runs_by_role: dict[str, dict[str, object]] = {}
    for value in session_values:
        if not isinstance(value, dict):
            continue
        run = cast("dict[str, object]", value)
        role = run.get("role")
        if isinstance(role, str):
            runs_by_role[role] = run
    assert set(ACTING_ROLES) <= runs_by_role.keys()
    for role in ACTING_ROLES:
        usage = runs_by_role[role].get("usage")
        assert isinstance(usage, dict)
        usage_data = cast("dict[str, object]", usage)
        input_tokens = usage_data.get("input_tokens")
        output_tokens = usage_data.get("output_tokens")
        assert isinstance(input_tokens, int)
        assert input_tokens > 0
        assert isinstance(output_tokens, int)
        assert output_tokens > 0


async def _assert_base_commits(project: PreparedProject) -> None:
    runner = SubprocessRunner()
    for repository, initial_sha in zip(project.repositories, project.initial_shas, strict=True):
        branch = await runner.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            timeout=CLI_TIMEOUT_SECONDS,
            cwd=repository,
            check=True,
        )
        current = await runner.run(
            ["git", "rev-parse", "HEAD"],
            timeout=CLI_TIMEOUT_SECONDS,
            cwd=repository,
            check=True,
        )
        subject = await runner.run(
            ["git", "log", "-1", "--format=%s"],
            timeout=CLI_TIMEOUT_SECONDS,
            cwd=repository,
            check=True,
        )
        assert branch.stdout.strip() == BASE_BRANCH
        assert current.stdout.strip() != initial_sha
        assert subject.stdout.startswith("fake:")


async def _assert_task_window_closed(project: PreparedProject, tmux_server: TmuxServer) -> None:
    target = exact_target(session_name(project.root.name))
    windows = await tmux_server.run(
        "list-windows", "-t", target, "-F", "#{window_name}", check=True
    )
    assert not any(name.startswith(TASK_ID) for name in windows.stdout.splitlines())


@pytest.mark.parametrize("scenario", SCENARIOS, ids=("monorepo", "multi-repo"))
async def test_fake_agent_operator_flow_completes_for_each_topology(
    tmp_path: Path, tmux_server: TmuxServer, scenario: Scenario
) -> None:
    project = await _prepare_project(tmp_path, scenario)
    environment = _tmux_environment(project.root, tmux_server)
    await tmux_server.run("set-environment", "-g", TICK_A1_ENV_VAR, TICK_A1_ENABLED_VALUE)
    target = exact_target(session_name(project.root.name))
    overrides = (
        "--set",
        "orchestration.poll_seconds=1",
        "--set",
        "orchestration.resources.max_cpu_pct=100",
        "--set",
        "orchestration.resources.min_free_ram_gb=0",
        "--set",
        'dashboard.widgets=["header"]',
    )
    lifecycle_started = False
    session_stopped = False
    try:
        code, _, stderr = await _cli(project.root, environment, "init", "--yes")
        assert code == 0, stderr

        lifecycle_started = True
        code, _, stderr = await _cli(project.root, environment, *overrides, "start")
        assert code == 0, stderr

        code, stdout, stderr = await _cli(
            project.root,
            environment,
            *overrides,
            "submit",
            "Implement task A1 from tasks/plan.md",
        )
        assert code == 0, stderr
        assert stdout.strip() == TASK_ID

        async def task_done() -> bool:
            status = await _status(project.root, environment)
            task = _task_data(status) if status is not None else None
            return task is not None and task.get("status") == "done"

        await wait_until(task_done, timeout=TASK_TIMEOUT_SECONDS)
        status = await _status(project.root, environment)
        assert status is not None
        task = _task_data(status)
        detail = _task_detail(status)
        assert task is not None
        assert detail is not None
        _assert_usage(detail)
        await _assert_base_commits(project)
        await _assert_tracker_ticked(project)
        await _assert_task_window_closed(project, tmux_server)

        await _cleanup_project(project, environment, target)
        session_stopped = True
    finally:
        if lifecycle_started and not session_stopped:
            await _cleanup_project(project, environment, target)
