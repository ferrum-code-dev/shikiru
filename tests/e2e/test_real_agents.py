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

from shikiru.bootstrap import build_operator
from shikiru.domain.reports import REPORT_FILENAME, Verdict
from shikiru.domain.tasks import Role, Status, Step
from shikiru.errors import ShikiruError
from shikiru.operator.commands import AckStatus
from shikiru.paths import MESSAGES_DIR_NAME, run_dir, turns_dir
from shikiru.process import SubprocessRunner
from shikiru.tmux.client import exact_target
from shikiru.tmux.session import session_name
from shikiru.view_log import view_log_path
from tests.support.git_repo import commit_all, init_repo
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from tests.support.tmux import TmuxServer


REAL_E2E_ENV_VAR = "SHIKIRU_E2E"
REAL_E2E_ENABLED = "1"
BASE_BRANCH = "main"
TASK_ID = "T-1"
CLI_TIMEOUT_SECONDS = 60.0
ATTEMPT_TIMEOUT_SECONDS = 1800.0
AUTOMATIC_RERUNS = 1
CLEANUP_TIMEOUT_SECONDS = 60.0
OUTER_TIMEOUT_SECONDS = (AUTOMATIC_RERUNS + 1) * (
    ATTEMPT_TIMEOUT_SECONDS + CLEANUP_TIMEOUT_SECONDS
) + CLI_TIMEOUT_SECONDS
RUN_STATUS_LIVE = "live"
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.e2e,
    pytest.mark.real_e2e,
    pytest.mark.timeout(OUTER_TIMEOUT_SECONDS),
]
E1_REQUEST = "Implement task A1 from tasks/plan.md"
E2_REQUEST = "Implement task A2 from tasks/plan.md"
E2_EDGE_CASE_CALL = 'parse_count("a;b, c")'
E2_EDGE_CASE_RESULT = 3
E2_EDGE_CASE_MARKER = "semicolon"
E2_MESSAGE = (
    "Additional Operator requirement for A2: names may also be separated by semicolons, "
    f"so {E2_EDGE_CASE_CALL} must return {E2_EDGE_CASE_RESULT}. Do not accept an "
    "implementation that does not handle semicolons and test this case."
)
VERIFY_TURN_PATTERN = f"*-{Step.VERIFY.value}-i*"
FIRST_IMPLEMENT_TURN_PATTERN = f"*-{Step.IMPLEMENT.value}-i1"
ACTING_ROLES = (Role.LEADER, Role.IMPLEMENTER)


FIXTURE_NAME = "sample-project"


@dataclass(frozen=True, slots=True)
class PreparedProject:
    root: Path
    initial_sha: str


@dataclass(slots=True)
class AttemptState:
    lifecycle_started: bool = False


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


REAL_OVERRIDES = (
    "--set",
    "orchestration.poll_seconds=1",
    "--set",
    "orchestration.resources.max_cpu_pct=100",
    "--set",
    "orchestration.resources.min_free_ram_gb=0",
    "--set",
    'dashboard.widgets=["header"]',
    "--set",
    'roles.leader.ladder=[{agent="claude",model="haiku",effort="low"}]',
    "--set",
    'roles.implementer.ladder=[{agent="codex",model="gpt-5.6-luna",effort="low"}]',
    "--set",
    'roles.helper.ladder=[{agent="claude",model="haiku",effort="low"}]',
)


async def _prepare_project(tmp_path: Path) -> PreparedProject:
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


def _environment(project_root: Path, tmux_server: TmuxServer) -> dict[str, str]:
    tmux_binary = shutil.which("tmux")
    if tmux_binary is None:
        _require(False, "tmux is required for real-agent E2E")
        raise AssertionError
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
    environment["PATH"] = os.pathsep.join(
        (str(shim_dir), str(Path(sys.executable).parent), environment.get("PATH", os.defpath))
    )
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


async def _status(
    project_root: Path,
    environment: dict[str, str],
    task_id: str | None = TASK_ID,
) -> dict[str, object] | None:
    arguments = ("--json", "status") if task_id is None else ("--json", "status", task_id)
    code, stdout, _ = await _cli(project_root, environment, *arguments)
    if code != 0:
        return None
    value = json.loads(stdout)
    return cast("dict[str, object]", value) if isinstance(value, dict) else None


def _task_detail(status: dict[str, object] | None) -> dict[str, object] | None:
    if status is None:
        return None
    value = status.get("task")
    return cast("dict[str, object]", value) if isinstance(value, dict) else None


async def _stop(project: PreparedProject, environment: dict[str, str]) -> None:
    code, _stdout, _stderr = await _cli(
        project.root,
        environment,
        *REAL_OVERRIDES,
        "stop",
        "--kill-session",
    )
    if code != 0:
        message = "real-agent stop failed"
        raise AssertionError(message)


async def _wait_done(project: PreparedProject, environment: dict[str, str]) -> None:
    async def done() -> bool:
        status = await _status(project.root, environment, task_id=None)
        task_ids = _task_tree_ids(status, TASK_ID)
        tasks = _task_summaries(status)
        tree = [tasks[task_id] for task_id in task_ids if task_id in tasks]
        if tree and all(task.get("status") == Status.DONE.value for task in tree):
            return True
        _require(
            not any(
                task.get("status") in {Status.ESCALATED.value, Status.CANCELLED.value}
                for task in tree
            ),
            f"task tree ended before completion: {[task.get('status') for task in tree]}",
        )
        _require(
            status is not None and status.get("engine_up") is True,
            "engine stopped before task completed",
        )
        return False

    await wait_until(done, timeout=ATTEMPT_TIMEOUT_SECONDS)


def _cost(statuses: dict[str, object] | list[dict[str, object] | None] | None) -> float | None:
    values = statuses if isinstance(statuses, list) else [statuses]
    total: float | None = None
    for status in values:
        detail = _task_detail(status)
        if detail is None:
            continue
        sessions = detail.get("sessions")
        if not isinstance(sessions, list):
            continue
        for value in cast("list[object]", sessions):
            if not isinstance(value, dict):
                continue
            usage = cast("dict[str, object]", value).get("usage")
            if not isinstance(usage, dict):
                continue
            amount = cast("dict[str, object]", usage).get("cost_usd")
            if isinstance(amount, int | float):
                total = (total or 0.0) + float(amount)
    return total


def _assert_both_agents_used(
    statuses: dict[str, object] | list[dict[str, object] | None] | None,
) -> None:
    values = statuses if isinstance(statuses, list) else [statuses]
    agents: set[str] = set()
    for status in values:
        detail = _task_detail(status)
        _require(detail is not None, "task detail missing")
        if detail is None:
            continue
        sessions = detail.get("sessions")
        _require(isinstance(sessions, list), "task sessions missing")
        if not isinstance(sessions, list):
            continue
        for value in cast("list[object]", sessions):
            if not isinstance(value, dict):
                continue
            session = cast("dict[str, object]", value)
            agent = session.get("agent")
            usage = session.get("usage")
            if isinstance(agent, str) and isinstance(usage, dict):
                agents.add(agent)
                input_tokens = cast("dict[str, object]", usage).get("input_tokens")
                output_tokens = cast("dict[str, object]", usage).get("output_tokens")
                _require(
                    isinstance(input_tokens, int) and input_tokens > 0,
                    f"{agent} input usage missing",
                )
                _require(
                    isinstance(output_tokens, int) and output_tokens > 0,
                    f"{agent} output usage missing",
                )
    _require({"claude", "codex"} <= agents, f"both real agents were not used: {agents}")


async def _assert_git_and_tracker(project: PreparedProject, task_name: str = "A1") -> None:
    tracker = project.root / "tasks" / "todo.md"
    _require(f"- [x] {task_name}" in await _read_text(tracker), f"{task_name} was not ticked")
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
    _require(
        branch.stdout.strip() == BASE_BRANCH and current.stdout.strip() != project.initial_sha,
        f"project did not merge on {BASE_BRANCH}",
    )


async def _assert_live_checkout_untouched(project: PreparedProject) -> None:
    runner = SubprocessRunner()
    status = await runner.run(
        ["git", "status", "--porcelain"], timeout=CLI_TIMEOUT_SECONDS, cwd=project.root, check=True
    )
    _require(not status.stdout.strip(), f"live checkout was edited: {status.stdout}")
    fast_forward = await runner.run(
        ["git", "merge-base", "--is-ancestor", project.initial_sha, "HEAD"],
        timeout=CLI_TIMEOUT_SECONDS,
        cwd=project.root,
    )
    _require(fast_forward.returncode == 0, "live base moved other than by the integration merge")


async def _assert_a1_implementation(project: PreparedProject) -> None:
    repository = project.root
    package_sources = list(repository.glob("*/__init__.py"))
    _require(
        any("def double" in source.read_text() for source in package_sources),
        "A1 implementation missing",
    )
    test_sources = list((repository / "tests").glob("test_*.py"))
    _require(any("double(21)" in source.read_text() for source in test_sources), "A1 test missing")
    await _assert_project_tests_pass(project)


async def _assert_project_tests_pass(project: PreparedProject) -> None:
    result = await SubprocessRunner().run(
        [sys.executable, "-m", "pytest", "-q"],
        timeout=CLI_TIMEOUT_SECONDS,
        cwd=project.root,
        check=False,
    )
    _require(result.returncode == 0, f"project tests failed: {result.stdout}")


async def _assert_edge_case_handled(project: PreparedProject) -> None:
    check = (
        "from sample_project import parse_count; "
        f"assert {E2_EDGE_CASE_CALL} == {E2_EDGE_CASE_RESULT}"
    )
    result = await SubprocessRunner().run(
        [sys.executable, "-c", check], timeout=CLI_TIMEOUT_SECONDS, cwd=project.root, check=False
    )
    _require(result.returncode == 0, f"edge case not handled: {result.stderr[-1000:]}")
    await _assert_project_tests_pass(project)


def _verify_turns(project: PreparedProject) -> list[Path]:
    return sorted(turns_dir(project.root, TASK_ID).glob(VERIFY_TURN_PATTERN))


def _verdict(turn_dir: Path) -> Verdict:
    return Verdict.model_validate_json((turn_dir / REPORT_FILENAME).read_text())


def _assert_message_drove_review(project: PreparedProject) -> None:
    verify_turns = _verify_turns(project)
    _require(bool(verify_turns), "no verify turn ran")
    first, last = verify_turns[0], verify_turns[-1]
    delivered = [path.read_text() for path in (first / MESSAGES_DIR_NAME).glob("*")]
    _require(E2_MESSAGE in delivered, f"message not delivered with {first.name}")
    requested = _verdict(first)
    cited = " ".join(issue.description for issue in requested.issues)
    _require(
        requested.decision == "changes_requested" and E2_EDGE_CASE_MARKER in cited.lower(),
        f"first verdict did not request the edge case: {requested.model_dump_json()}",
    )
    _require(_verdict(last).decision == "accept", f"final verdict {last.name} was not accept")


def _assert_views_recorded(project: PreparedProject) -> None:
    task_run_dir = run_dir(project.root, TASK_ID)
    for role in ACTING_ROLES:
        view = view_log_path(task_run_dir, role.value)
        _require(view.is_file() and view.stat().st_size > 0, f"{role.value} view log is empty")


async def _send_edge_case_during_first_implementation(project: PreparedProject) -> None:
    def implementing() -> bool:
        _require(not _verify_turns(project), "implementation finished before the message was sent")
        return any(turns_dir(project.root, TASK_ID).glob(FIRST_IMPLEMENT_TURN_PATTERN))

    await wait_until(implementing, timeout=ATTEMPT_TIMEOUT_SECONDS)
    services = build_operator(project.root)
    try:
        ack = await services.commands.send_message(TASK_ID, Role.LEADER, E2_MESSAGE)
    finally:
        services.close()
    _require(ack.status is not AckStatus.REJECTED, f"send_message rejected: {ack.message}")


async def _assert_window_closed(project: PreparedProject, tmux_server: TmuxServer) -> None:
    target = exact_target(session_name(project.root.name))
    windows = await tmux_server.run(
        "list-windows",
        "-t",
        target,
        "-F",
        "#{window_name}",
        check=True,
    )
    _require(
        not any(name.startswith(TASK_ID) for name in windows.stdout.splitlines()),
        "task window remained open",
    )


async def _read_text(path: Path) -> str:
    return await asyncio.to_thread(path.read_text)


type ScenarioBody = Callable[[PreparedProject, dict[str, str], TmuxServer], Awaitable[None]]


async def _run_attempt(
    tmp_path: Path,
    tmux_server: TmuxServer,
    record_cost: Callable[[float | None], None],
    body: ScenarioBody,
) -> None:
    project = await _prepare_project(tmp_path)
    environment = _environment(project.root, tmux_server)
    state = AttemptState()
    try:
        await asyncio.wait_for(
            _execute(project, environment, tmux_server, state, body),
            timeout=ATTEMPT_TIMEOUT_SECONDS,
        )
    finally:
        await _record_observed_cost(project, environment, record_cost)
        await _cleanup_attempt(project, environment, state)


async def _execute(
    project: PreparedProject,
    environment: dict[str, str],
    tmux_server: TmuxServer,
    state: AttemptState,
    body: ScenarioBody,
) -> None:
    await _initialize(project, environment)
    await _start(project, environment, state)
    await body(project, environment, tmux_server)
    task_statuses = await _task_tree_statuses(project.root, environment)
    _assert_both_agents_used(task_statuses)
    _assert_views_recorded(project)
    await _assert_live_checkout_untouched(project)
    await _assert_window_closed(project, tmux_server)


async def _run_with_reruns(
    tmp_path: Path,
    tmux_server: TmuxServer,
    record_cost: Callable[[float | None], None],
    body: ScenarioBody,
) -> None:
    if os.environ.get(REAL_E2E_ENV_VAR) != REAL_E2E_ENABLED:
        pytest.skip(f"set {REAL_E2E_ENV_VAR}=1 to run real-agent E2E")
    for attempt in range(AUTOMATIC_RERUNS + 1):
        try:
            await _run_attempt(tmp_path / f"attempt-{attempt}", tmux_server, record_cost, body)
        except AssertionError, OSError, ShikiruError, TimeoutError:
            if attempt == AUTOMATIC_RERUNS:
                raise
        else:
            return


async def _e1_body(
    project: PreparedProject, environment: dict[str, str], _tmux_server: TmuxServer
) -> None:
    await _submit(project, environment, E1_REQUEST)
    await _wait_done(project, environment)
    await _assert_git_and_tracker(project, "A1")
    await _assert_a1_implementation(project)


async def _e2_body(
    project: PreparedProject, environment: dict[str, str], _tmux_server: TmuxServer
) -> None:
    await _submit(project, environment, E2_REQUEST)
    await _send_edge_case_during_first_implementation(project)
    await _wait_done(project, environment)
    _assert_message_drove_review(project)
    await _assert_git_and_tracker(project, "A2")
    await _assert_edge_case_handled(project)


async def test_real_agents_e1_completes_and_integrates(
    tmp_path: Path,
    tmux_server: TmuxServer,
    record_e2e_cost: Callable[[float | None], None],
) -> None:
    await _run_with_reruns(tmp_path, tmux_server, record_e2e_cost, _e1_body)


async def test_real_agents_e2_delivers_operator_message_to_verify(
    tmp_path: Path,
    tmux_server: TmuxServer,
    record_e2e_cost: Callable[[float | None], None],
) -> None:
    await _run_with_reruns(tmp_path, tmux_server, record_e2e_cost, _e2_body)


async def _record_observed_cost(
    project: PreparedProject,
    environment: dict[str, str],
    record_cost: Callable[[float | None], None],
) -> None:
    try:
        statuses = await _task_tree_statuses(project.root, environment)
    except OSError, ShikiruError, json.JSONDecodeError:
        return
    record_cost(_cost(statuses))


def _task_summaries(status: dict[str, object] | None) -> dict[str, dict[str, object]]:
    if status is None:
        return {}
    values = status.get("tasks")
    if not isinstance(values, list):
        return {}
    summaries: dict[str, dict[str, object]] = {}
    for value in cast("list[object]", values):
        if not isinstance(value, dict):
            continue
        summary = cast("dict[str, object]", value)
        task_id = summary.get("id")
        if isinstance(task_id, str):
            summaries[task_id] = summary
    return summaries


def _task_tree_ids(status: dict[str, object] | None, root_id: str) -> list[str]:
    tasks = _task_summaries(status)
    if root_id not in tasks:
        return []
    result = [root_id]
    for task_id in result:
        result.extend(
            child_id
            for child_id, task in tasks.items()
            if task.get("parent_id") == task_id and child_id not in result
        )
    return result


async def _task_tree_statuses(
    project_root: Path, environment: dict[str, str]
) -> list[dict[str, object] | None]:
    status = await _status(project_root, environment, task_id=None)
    return [
        await _status(project_root, environment, task_id)
        for task_id in _task_tree_ids(status, TASK_ID)
    ]


async def _initialize(project: PreparedProject, environment: dict[str, str]) -> None:
    await _run_checked(project, environment, "init", "--yes")
    config = await _read_text(project.root / ".shikiru" / "config.toml")
    missing = [name for name in ("claude", "codex") if f"[agents.{name}]" not in config]
    _require(
        not missing,
        f"init did not configure required real plugins: {', '.join(missing)}",
    )


async def _start(
    project: PreparedProject, environment: dict[str, str], state: AttemptState
) -> None:
    state.lifecycle_started = True
    await _run_checked(project, environment, *REAL_OVERRIDES, "start")


async def _submit(project: PreparedProject, environment: dict[str, str], request: str) -> None:
    await _run_checked(project, environment, *REAL_OVERRIDES, "submit", request)


async def _run_checked(
    project: PreparedProject, environment: dict[str, str], *arguments: str
) -> tuple[str, str]:
    code, stdout, stderr = await _cli(project.root, environment, *arguments)
    if code != 0:
        operation = " ".join(arguments[:2])
        detail = f"{stdout.strip()} {stderr.strip()}".strip()
        _require(code == 0, f"{operation} failed with exit {code}: {detail[:1000]}")
    return stdout, stderr


async def _cleanup_attempt(
    project: PreparedProject, environment: dict[str, str], state: AttemptState
) -> None:
    if not state.lifecycle_started:
        return
    try:
        await asyncio.wait_for(_stop(project, environment), timeout=CLEANUP_TIMEOUT_SECONDS)
    except TimeoutError as error:
        _require(False, "real-agent cleanup timed out")
        raise AssertionError from error
