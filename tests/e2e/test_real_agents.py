from __future__ import annotations

import asyncio
import json
import os
import shlex
import shutil
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from shikiru.bootstrap import build_operator
from shikiru.config.loader import load_settings
from shikiru.domain.tasks import Role, Status, Step
from shikiru.errors import ShikiruError
from shikiru.operator.commands import AckStatus
from shikiru.process import SubprocessRunner
from shikiru.tmux.client import exact_target
from shikiru.tmux.session import session_name
from tests.support.git_repo import commit_all, init_repo
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from collections.abc import Callable

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
PRIVATE_DIRECTORY_MODE = stat.S_IRWXU
PRIVATE_FILE_MODE = stat.S_IRUSR | stat.S_IWUSR
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.e2e,
    pytest.mark.real_e2e,
    pytest.mark.timeout(OUTER_TIMEOUT_SECONDS),
]
E1_REQUEST = "Implement task A1 from tasks/plan.md"
E2_MESSAGE = (
    "Operator requirement: parse_count must raise ValueError for whitespace-only input such as "
    '"   ". Request changes if the implementation does not.'
)
E2_MESSAGE_ROLE = Role.LEADER
E2_MESSAGE_STEP = Step.VERIFY
E2_REQUEST = "Implement task A2 from tasks/plan.md"
A2_EDGE_CASE_CHECK = """
from sample_project import parse_count
assert parse_count("alpha, beta") == 2
assert parse_count("") == 0
try:
    parse_count("   ")
except ValueError:
    pass
else:
    raise AssertionError("whitespace-only input was accepted")
"""


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
    "--set",
    'agents.claude.options.args=["--permission-mode","bypassPermissions"]',
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
    codex_home = project_root.parent / "codex-home"
    _copy_codex_auth(codex_home)
    environment = dict(os.environ)
    environment.pop("TMUX", None)
    environment.pop("TMUX_PANE", None)
    environment["CODEX_HOME"] = str(codex_home)
    environment["NO_COLOR"] = "1"
    environment["PATH"] = os.pathsep.join(
        (str(shim_dir), str(Path(sys.executable).parent), environment.get("PATH", os.defpath))
    )
    return environment


def _copy_codex_auth(destination: Path) -> None:
    source_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    source = source_home / "auth.json"
    if not source.is_file():
        _require(False, f"Codex auth file is missing at {source}")
        raise AssertionError
    destination.mkdir(mode=PRIVATE_DIRECTORY_MODE)
    auth_destination = destination / "auth.json"
    shutil.copyfile(source, auth_destination)
    auth_destination.chmod(PRIVATE_FILE_MODE)


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


def _task(status: dict[str, object] | None) -> dict[str, object] | None:
    if status is None:
        return None
    detail = status.get("task")
    if not isinstance(detail, dict):
        return None
    value = cast("dict[str, object]", detail).get("task")
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


def _cost(statuses: dict[str, object] | list[dict[str, object] | None] | None) -> float:
    values = statuses if isinstance(statuses, list) else [statuses]
    total = 0.0
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
                total += float(amount)
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
    result = await SubprocessRunner().run(
        [sys.executable, "-m", "pytest", "-q"],
        timeout=CLI_TIMEOUT_SECONDS,
        cwd=repository,
        check=False,
    )
    _require(result.returncode == 0, f"A1 tests failed: {result.stdout}")


async def _assert_a2_implementation(project: PreparedProject) -> None:
    repository = project.root
    source = repository / "sample_project" / "__init__.py"
    _require("def parse_count" in await _read_text(source), "A2 implementation missing")
    tests = list((repository / "tests").glob("test_*.py"))
    _require(
        any("parse_count" in test.read_text() for test in tests),
        "A2 tests missing",
    )
    result = await SubprocessRunner().run(
        [
            sys.executable,
            "-c",
            A2_EDGE_CASE_CHECK,
        ],
        timeout=CLI_TIMEOUT_SECONDS,
        cwd=repository,
        check=False,
    )
    _require(result.returncode == 0, f"A2 edge case failed: {result.stdout}{result.stderr}")


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


async def _send_e2_message(project: PreparedProject, environment: dict[str, str]) -> None:
    async def verify_turn_started() -> bool:
        status = await _status(project.root, environment)
        return _live_session(status, E2_MESSAGE_ROLE) and _has_turn_started(
            status, E2_MESSAGE_ROLE, E2_MESSAGE_STEP
        )

    await wait_until(verify_turn_started, timeout=ATTEMPT_TIMEOUT_SECONDS)
    settings = load_settings(project.root, REAL_OVERRIDES[1::2])
    services = build_operator(project.root, settings)
    try:
        ack = await services.commands.send_message(TASK_ID, E2_MESSAGE_ROLE, E2_MESSAGE)
    finally:
        services.close()
    _require(
        ack.status in {AckStatus.DONE, AckStatus.QUEUED},
        f"operator message was rejected: {ack.message}",
    )

    async def message_delivered() -> bool:
        return _has_operator_message(await _status(project.root, environment))

    await wait_until(message_delivered, timeout=CLI_TIMEOUT_SECONDS)


def _live_session(status: dict[str, object] | None, role: Role) -> bool:
    _require(
        status is not None and status.get("engine_up") is True,
        f"engine stopped before the {role.value} turn started",
    )
    task = _task(status)
    detail = _task_detail(status)
    _require(
        task is not None and detail is not None,
        f"task detail missing before the {role.value} turn started",
    )
    if task is None or detail is None:
        return False
    _require(
        task.get("status")
        not in {Status.ESCALATED.value, Status.CANCELLED.value, Status.DONE.value},
        f"task ended before the {role.value} turn started: {task.get('status')}",
    )
    sessions = detail.get("sessions")
    _require(isinstance(sessions, list), "task sessions missing")
    if not isinstance(sessions, list):
        return False
    session = next(
        (
            cast("dict[str, object]", value)
            for value in cast("list[object]", sessions)
            if isinstance(value, dict)
            and cast("dict[str, object]", value).get("role") == role.value
        ),
        None,
    )
    if session is None:
        return False
    _require(
        session.get("status") == RUN_STATUS_LIVE,
        f"{role.value} session exited before its turn started: {session.get('status')}",
    )
    return True


def _has_turn_started(status: dict[str, object] | None, role: Role, step: Step) -> bool:
    detail = _task_detail(status)
    if detail is None:
        return False
    events = detail.get("events")
    if not isinstance(events, list):
        return False
    return any(
        event.get("kind") == "turn_started"
        and _event_data(event).get("role") == role.value
        and _event_data(event).get("step") == step.value
        for event in _event_dicts(cast("list[object]", events))
    )


def _has_operator_message(status: dict[str, object] | None) -> bool:
    detail = _task_detail(status)
    if detail is None:
        return False
    events = detail.get("events")
    if not isinstance(events, list):
        return False
    return any(
        event.get("kind") == "operator_message"
        and event.get("message") == E2_MESSAGE
        and _event_data(event).get("role") == E2_MESSAGE_ROLE.value
        for event in _event_dicts(cast("list[object]", events))
    )


def _event_dicts(events: list[object]) -> list[dict[str, object]]:
    return [cast("dict[str, object]", event) for event in events if isinstance(event, dict)]


def _event_data(event: dict[str, object]) -> dict[str, object]:
    data = event.get("data")
    return cast("dict[str, object]", data) if isinstance(data, dict) else {}


def _assert_e2_events(status: dict[str, object] | None) -> None:
    detail = _task_detail(status)
    _require(detail is not None, "task detail missing")
    if detail is None:
        return
    events = detail.get("events")
    _require(isinstance(events, list), "task events missing")
    if not isinstance(events, list):
        return
    event_values = _event_dicts(cast("list[object]", events))
    event_kinds = [event.get("kind") for event in event_values]
    verdicts = [
        (index, _event_data(event).get("decision"))
        for index, event in enumerate(event_values)
        if event.get("kind") == "verdict"
    ]
    changes_requested = next(
        (index for index, decision in verdicts if decision == "changes_requested"), None
    )
    accepted_after = next(
        (
            index
            for index, decision in verdicts
            if decision == "accept" and changes_requested is not None and index > changes_requested
        ),
        None,
    )
    task = _task(status)
    _require(
        changes_requested is not None and accepted_after is not None,
        "changes-requested verdict was not followed by final accept",
    )
    message_index = next(
        (
            index
            for index, event in enumerate(event_values)
            if event.get("kind") == "operator_message"
            and _event_data(event).get("role") == E2_MESSAGE_ROLE.value
        ),
        None,
    )
    _require(message_index is not None, f"{E2_MESSAGE_ROLE.value} did not receive the message")
    if message_index is not None and changes_requested is not None:
        started = [
            index
            for index, event in enumerate(event_values)
            if event.get("kind") == "turn_started"
            and _event_data(event).get("role") == E2_MESSAGE_ROLE.value
        ]
        _require(
            any(start < message_index < changes_requested for start in started),
            "operator message was not delivered during the verify turn that requested changes",
        )
    _require("intervention" not in event_kinds, "operator intervention was observed")
    _require(task is not None and task.get("status") == "done", "task did not finish")


async def _run_e1(
    tmp_path: Path,
    tmux_server: TmuxServer,
    record_cost: Callable[[float | None], None],
) -> None:
    project = await _prepare_project(tmp_path)
    environment = _environment(project.root, tmux_server)
    state = AttemptState()
    try:
        await asyncio.wait_for(
            _execute_e1(project, environment, tmux_server, record_cost=record_cost, state=state),
            timeout=ATTEMPT_TIMEOUT_SECONDS,
        )
    except AssertionError, OSError, ShikiruError, TimeoutError:
        await _record_observed_cost(project, environment, record_cost)
        raise
    finally:
        await _cleanup_attempt(project, environment, state)


async def _execute_e1(
    project: PreparedProject,
    environment: dict[str, str],
    tmux_server: TmuxServer,
    *,
    record_cost: Callable[[float | None], None],
    state: AttemptState,
) -> None:
    await _initialize(project, environment)
    await _start(project, environment, state)
    await _submit(project, environment, E1_REQUEST)
    await _wait_done(project, environment)
    task_statuses = await _task_tree_statuses(project.root, environment)
    _assert_both_agents_used(task_statuses)
    await _assert_git_and_tracker(project)
    await _assert_live_checkout_untouched(project)
    await _assert_a1_implementation(project)
    await _assert_window_closed(project, tmux_server)
    record_cost(_cost(task_statuses))


async def test_real_agents_e1_completes_and_integrates(
    tmp_path: Path,
    tmux_server: TmuxServer,
    record_e2e_cost: Callable[[float | None], None],
) -> None:
    if os.environ.get(REAL_E2E_ENV_VAR) != REAL_E2E_ENABLED:
        pytest.skip(f"set {REAL_E2E_ENV_VAR}=1 to run real-agent E2E")
    for attempt in range(AUTOMATIC_RERUNS + 1):
        try:
            await _run_e1(tmp_path / f"attempt-{attempt}", tmux_server, record_e2e_cost)
        except AssertionError, OSError, ShikiruError, TimeoutError:
            if attempt == AUTOMATIC_RERUNS:
                raise
        else:
            return


async def _run_e2(
    tmp_path: Path,
    tmux_server: TmuxServer,
    record_cost: Callable[[float | None], None],
) -> None:
    project = await _prepare_project(tmp_path)
    environment = _environment(project.root, tmux_server)
    state = AttemptState()
    try:
        await asyncio.wait_for(
            _execute_e2(project, environment, tmux_server, record_cost, state),
            timeout=ATTEMPT_TIMEOUT_SECONDS,
        )
    except AssertionError, OSError, ShikiruError, TimeoutError:
        await _record_observed_cost(project, environment, record_cost)
        raise
    finally:
        await _cleanup_attempt(project, environment, state)


async def _record_observed_cost(
    project: PreparedProject,
    environment: dict[str, str],
    record_cost: Callable[[float | None], None],
) -> None:
    try:
        status = await _status(project.root, environment)
    except OSError, ShikiruError, json.JSONDecodeError:
        return
    if status is not None:
        record_cost(_cost(status))


async def _execute_e2(
    project: PreparedProject,
    environment: dict[str, str],
    tmux_server: TmuxServer,
    record_cost: Callable[[float | None], None],
    state: AttemptState,
) -> None:
    await _initialize(project, environment)
    await _start(project, environment, state)
    await _submit(project, environment, E2_REQUEST)
    await _send_e2_message(project, environment)
    await _wait_done(project, environment)
    status = await _status(project.root, environment)
    _assert_e2_events(status)
    _assert_both_agents_used(status)
    await _assert_git_and_tracker(project, "A2")
    await _assert_a2_implementation(project)
    await _assert_window_closed(project, tmux_server)
    record_cost(_cost(status))


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


async def test_real_agents_e2_handles_message_and_rework(
    tmp_path: Path,
    tmux_server: TmuxServer,
    record_e2e_cost: Callable[[float | None], None],
) -> None:
    if os.environ.get(REAL_E2E_ENV_VAR) != REAL_E2E_ENABLED:
        pytest.skip(f"set {REAL_E2E_ENV_VAR}=1 to run real-agent E2E")
    for attempt in range(AUTOMATIC_RERUNS + 1):
        try:
            await _run_e2(tmp_path / f"attempt-{attempt}", tmux_server, record_e2e_cost)
        except AssertionError, OSError, ShikiruError, TimeoutError:
            if attempt == AUTOMATIC_RERUNS:
                raise
        else:
            return
