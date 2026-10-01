from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, cast

import pytest
from fake_agent.scripted import TICK_A1_ENABLED_VALUE, TICK_A1_ENV_VAR

from shikiru.process import SubprocessRunner
from shikiru.tmux.client import exact_target
from shikiru.tmux.session import session_name
from tests.e2e.budgets import ATTEMPT_BUDGETS
from tests.support.cli_project import (
    BASE_BRANCH,
    CLI_TIMEOUT_SECONDS,
    PreparedProject,
    prepare_project,
    run_cli,
    tmux_environment,
)
from tests.support.factories import CPU_UNCAPPED_PCT
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from tests.support.tmux import TmuxServer

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]

TASK_TIMEOUT_SECONDS = ATTEMPT_BUDGETS["fake"].total_seconds()
TASK_ID = "T-1"
ACTING_ROLES = ("leader", "implementer")
TMUX_COMMAND_TIMEOUT_SECONDS = 5.0
FAKE_OVERRIDES = (
    "--set",
    'roles.leader.ladder=[{agent="fake",model="fake-1"}]',
    "--set",
    'roles.implementer.ladder=[{agent="fake",model="fake-1"}]',
    "--set",
    'roles.helper.ladder=[{agent="fake",model="fake-1"}]',
)


async def _status(project_root: Path, environment: dict[str, str]) -> dict[str, object] | None:
    code, stdout, _ = await run_cli(project_root, environment, "--json", "status", TASK_ID)
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
    code, stdout, _ = await run_cli(project_root, environment, "--json", "status")
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
    code, _, stderr = await run_cli(project.root, environment, "stop", "--kill-session")
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
    result = await SubprocessRunner().run(
        ["git", "show", "HEAD:tasks/todo.md"],
        timeout=CLI_TIMEOUT_SECONDS,
        cwd=project.root,
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
    branch = await runner.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        timeout=CLI_TIMEOUT_SECONDS,
        cwd=project.root,
        check=True,
    )
    current = await runner.run(
        ["git", "rev-parse", "HEAD"], timeout=CLI_TIMEOUT_SECONDS, cwd=project.root, check=True
    )
    subject = await runner.run(
        ["git", "log", "-1", "--format=%s"],
        timeout=CLI_TIMEOUT_SECONDS,
        cwd=project.root,
        check=True,
    )
    assert branch.stdout.strip() == BASE_BRANCH
    assert current.stdout.strip() != project.initial_sha
    assert subject.stdout.startswith("fake:")


async def _assert_task_window_closed(project: PreparedProject, tmux_server: TmuxServer) -> None:
    target = exact_target(session_name(project.root.name))
    windows = await tmux_server.run(
        "list-windows", "-t", target, "-F", "#{window_name}", check=True
    )
    assert not any(name.startswith(TASK_ID) for name in windows.stdout.splitlines())


async def test_fake_agent_operator_flow_completes_and_integrates(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    project = await prepare_project(tmp_path)
    environment = tmux_environment(project.root, tmux_server)
    await tmux_server.run("set-environment", "-g", TICK_A1_ENV_VAR, TICK_A1_ENABLED_VALUE)
    target = exact_target(session_name(project.root.name))
    overrides = (
        "--set",
        "orchestration.poll_seconds=1",
        "--set",
        f"orchestration.resources.max_cpu_pct={CPU_UNCAPPED_PCT}",
        "--set",
        "orchestration.resources.min_free_ram_gb=0",
        "--set",
        'dashboard.widgets=["header"]',
    )
    lifecycle_started = False
    session_stopped = False
    try:
        code, _, stderr = await run_cli(project.root, environment, "init", "--yes")
        assert code == 0, stderr

        lifecycle_started = True
        code, _, stderr = await run_cli(
            project.root, environment, *overrides, *FAKE_OVERRIDES, "start"
        )
        assert code == 0, stderr

        code, stdout, stderr = await run_cli(
            project.root,
            environment,
            *overrides,
            *FAKE_OVERRIDES,
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
