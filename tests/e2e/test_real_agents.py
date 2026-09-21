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
from shikiru.domain.tasks import Role
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
CLAUDE_DIRECTORY_NAME = ".claude"
CLAUDE_CREDENTIALS_FILENAME = ".credentials.json"
CLAUDE_OAUTH_TOKEN_ENV = "CLAUDE_CODE_" + "OAUTH_TOKEN"
CLAUDE_AUTH_CONFIG_KEYS = (
    "firstStartTime",
    "hasCompletedOnboarding",
    "machineID",
    "migrationVersion",
    "oauthAccount",
    "userID",
)
CODEX_UPDATE_CONFIG = "check_for_update_on_startup = false\n"
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.e2e,
    pytest.mark.real_e2e,
    pytest.mark.timeout(OUTER_TIMEOUT_SECONDS),
]
E1_REQUEST = "Implement task A1 from tasks/plan.md"
E2_MESSAGE = (
    "Please verify the whitespace-only input edge case: empty text returns 0, while whitespace-"
    "only text is invalid."
)
E2_REQUEST = "Implement task A2 from tasks/plan.md"


@dataclass(frozen=True, slots=True)
class RealScenario:
    fixture_name: str
    repository_paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PreparedProject:
    root: Path
    repositories: tuple[Path, ...]
    initial_shas: tuple[str, ...]


@dataclass(slots=True)
class AttemptState:
    lifecycle_started: bool = False


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


REAL_SCENARIOS = (
    RealScenario("sample-project", (".",)),
    RealScenario("sample-project-multi", ("backend", "frontend")),
)
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
    'roles.implementer.ladder=[{agent="codex",model="gpt-5.6-sol",effort="low"}]',
    "--set",
    'roles.helper.ladder=[{agent="claude",model="haiku",effort="low"}]',
)


async def _prepare_project(tmp_path: Path, scenario: RealScenario) -> PreparedProject:
    source = Path(__file__).parents[1] / "fixtures" / scenario.fixture_name
    root = tmp_path / "project"
    shutil.copytree(source, root)
    runner = SubprocessRunner()
    repositories: list[Path] = []
    initial_shas: list[str] = []
    for relative_path in scenario.repository_paths:
        repository = root if relative_path == "." else root / relative_path
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
    isolated_home = project_root.parent / "agent-home"
    isolated_home.mkdir(mode=PRIVATE_DIRECTORY_MODE)
    _copy_agent_state(isolated_home, project_root)
    environment = dict(os.environ)
    environment.pop("TMUX", None)
    environment.pop("TMUX_PANE", None)
    environment["HOME"] = str(isolated_home)
    environment["CODEX_HOME"] = str(isolated_home / ".codex")
    environment["NO_COLOR"] = "1"
    environment["PATH"] = f"{shim_dir}{os.pathsep}{environment.get('PATH', os.defpath)}"
    return environment


def _copy_agent_state(isolated_home: Path, project_root: Path) -> None:
    _copy_claude_auth(isolated_home)
    _copy_codex_auth(isolated_home / ".codex", project_root)


def _copy_claude_auth(isolated_home: Path) -> None:
    source = Path.home() / ".claude.json"
    if not source.is_file():
        _require(False, f"Claude auth config is missing at {source}")
        raise AssertionError
    try:
        payload = json.loads(source.read_text())
    except (OSError, json.JSONDecodeError) as error:
        _require(False, "Claude auth config could not be read")
        raise AssertionError from error
    if not isinstance(payload, dict):
        _require(False, "Claude auth config is not a JSON object")
        raise TypeError
    minimal: dict[str, object] = {
        key: payload[key] for key in CLAUDE_AUTH_CONFIG_KEYS if key in payload
    }
    if "oauthAccount" not in minimal:
        _require(False, "Claude auth config has no authenticated account")
        raise AssertionError
    destination = isolated_home / ".claude.json"
    destination.write_text(json.dumps(minimal, separators=(",", ":")))
    destination.chmod(PRIVATE_FILE_MODE)
    credentials = Path.home() / CLAUDE_DIRECTORY_NAME / CLAUDE_CREDENTIALS_FILENAME
    if credentials.is_file():
        target_directory = isolated_home / CLAUDE_DIRECTORY_NAME
        target_directory.mkdir(mode=PRIVATE_DIRECTORY_MODE)
        target = target_directory / CLAUDE_CREDENTIALS_FILENAME
        shutil.copyfile(credentials, target)
        target.chmod(PRIVATE_FILE_MODE)
    elif not os.environ.get(CLAUDE_OAUTH_TOKEN_ENV):
        _require(
            False,
            "Claude credentials cannot be isolated; provide CLAUDE_CODE_OAUTH_TOKEN or "
            f"{credentials}",
        )


def _copy_codex_auth(destination: Path, project_root: Path) -> None:
    source_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    source = source_home / "auth.json"
    if not source.is_file():
        _require(False, f"Codex auth file is missing at {source}")
        raise AssertionError
    destination.mkdir(mode=PRIVATE_DIRECTORY_MODE)
    auth_destination = destination / "auth.json"
    shutil.copyfile(source, auth_destination)
    auth_destination.chmod(PRIVATE_FILE_MODE)
    project = json.dumps(str(project_root))
    config = destination / "config.toml"
    config.write_text(CODEX_UPDATE_CONFIG + f'[projects.{project}]\ntrust_level = "trusted"\n')
    config.chmod(PRIVATE_FILE_MODE)


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
        return (_task(await _status(project.root, environment)) or {}).get("status") == "done"

    await wait_until(done, timeout=ATTEMPT_TIMEOUT_SECONDS)


def _cost(status: dict[str, object] | None) -> float:
    detail = _task_detail(status)
    if detail is None:
        return 0.0
    sessions = detail.get("sessions")
    if not isinstance(sessions, list):
        return 0.0
    total = 0.0
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


def _assert_both_agents_used(status: dict[str, object] | None) -> None:
    detail = _task_detail(status)
    _require(detail is not None, "task detail missing")
    if detail is None:
        return
    sessions = detail.get("sessions")
    _require(isinstance(sessions, list), "task sessions missing")
    if not isinstance(sessions, list):
        return
    agents: set[str] = set()
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
        _require(
            branch.stdout.strip() == BASE_BRANCH and current.stdout.strip() != initial_sha,
            f"repository did not merge on {BASE_BRANCH}: {repository}",
        )


async def _assert_a1_implementation(project: PreparedProject) -> None:
    runner = SubprocessRunner()
    for repository in project.repositories:
        package_sources = list(repository.glob("*/__init__.py"))
        _require(bool(package_sources), f"package source missing in {repository}")
        _require(
            any("def double" in source.read_text() for source in package_sources),
            f"A1 implementation missing in {repository}",
        )
        test_sources = list((repository / "tests").glob("test_*.py"))
        _require(
            any("double(21)" in source.read_text() for source in test_sources),
            f"A1 test missing in {repository}",
        )
        result = await runner.run(
            [sys.executable, "-m", "pytest", "-q"],
            timeout=CLI_TIMEOUT_SECONDS,
            cwd=repository,
            check=False,
        )
        _require(result.returncode == 0, f"A1 tests failed in {repository}: {result.stdout}")


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
            (
                "from sample_project import parse_count; "
                "assert parse_count('alpha, beta') == 2; "
                "assert parse_count('') == 0; "
                "try: parse_count('   ')\n"
                "except ValueError: pass\n"
                "else: raise AssertionError('whitespace-only input was accepted')"
            ),
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
    async def implementer_turn_started() -> bool:
        status = await _status(project.root, environment)
        task = _task(status)
        detail = _task_detail(status)
        if task is None or detail is None or task.get("step") != "implement":
            return False
        sessions = detail.get("sessions")
        if not isinstance(sessions, list):
            return False
        return any(_is_live_implementer(value) for value in cast("list[object]", sessions))

    await wait_until(implementer_turn_started, timeout=ATTEMPT_TIMEOUT_SECONDS)
    settings = load_settings(project.root, REAL_OVERRIDES[1::2])
    services = build_operator(project.root, settings)
    try:
        ack = await services.commands.send_message(TASK_ID, Role.IMPLEMENTER, E2_MESSAGE)
    finally:
        services.close()
    _require(ack.status is AckStatus.DONE, f"operator message was not delivered: {ack.message}")


def _is_live_implementer(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    session = cast("dict[str, object]", value)
    return (
        session.get("role") == Role.IMPLEMENTER.value and session.get("status") == RUN_STATUS_LIVE
    )


def _assert_e2_events(status: dict[str, object] | None) -> None:
    detail = _task_detail(status)
    _require(detail is not None, "task detail missing")
    if detail is None:
        return
    events = detail.get("events")
    _require(isinstance(events, list), "task events missing")
    if not isinstance(events, list):
        return
    event_values = [
        cast("dict[str, object]", event)
        for event in cast("list[object]", events)
        if isinstance(event, dict)
    ]
    event_kinds = [event.get("kind") for event in event_values]
    verdicts = [
        (index, cast("dict[str, object]", event["data"]).get("decision"))
        for index, event in enumerate(event_values)
        if event.get("kind") == "verdict" and isinstance(event.get("data"), dict)
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
    _require("operator_message" in event_kinds, "operator message was not observed")
    _require("intervention" not in event_kinds, "operator intervention was observed")
    _require(task is not None and task.get("status") == "done", "task did not finish")


async def _run_e1(
    tmp_path: Path,
    tmux_server: TmuxServer,
    scenario: RealScenario,
    record_cost: Callable[[float | None], None],
) -> None:
    project = await _prepare_project(tmp_path, scenario)
    environment = _environment(project.root, tmux_server)
    state = AttemptState()
    try:
        await asyncio.wait_for(
            _execute_e1(project, environment, tmux_server, record_cost, state),
            timeout=ATTEMPT_TIMEOUT_SECONDS,
        )
    finally:
        await _cleanup_attempt(project, environment, state)


async def _execute_e1(
    project: PreparedProject,
    environment: dict[str, str],
    tmux_server: TmuxServer,
    record_cost: Callable[[float | None], None],
    state: AttemptState,
) -> None:
    await _initialize(project, environment)
    await _start(project, environment, state)
    await _submit(project, environment, E1_REQUEST)
    await _wait_done(project, environment)
    status = await _status(project.root, environment)
    _assert_both_agents_used(status)
    await _assert_git_and_tracker(project)
    await _assert_a1_implementation(project)
    await _assert_window_closed(project, tmux_server)
    record_cost(_cost(status))


@pytest.mark.parametrize("scenario", REAL_SCENARIOS, ids=("monorepo", "multi-repo"))
async def test_real_agents_e1_completes_for_each_topology(
    tmp_path: Path,
    tmux_server: TmuxServer,
    scenario: RealScenario,
    record_e2e_cost: Callable[[float | None], None],
) -> None:
    if os.environ.get(REAL_E2E_ENV_VAR) != REAL_E2E_ENABLED:
        pytest.skip(f"set {REAL_E2E_ENV_VAR}=1 to run real-agent E2E")
    for attempt in range(AUTOMATIC_RERUNS + 1):
        try:
            await _run_e1(tmp_path / f"attempt-{attempt}", tmux_server, scenario, record_e2e_cost)
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
    project = await _prepare_project(tmp_path, REAL_SCENARIOS[0])
    environment = _environment(project.root, tmux_server)
    state = AttemptState()
    try:
        await asyncio.wait_for(
            _execute_e2(project, environment, tmux_server, record_cost, state),
            timeout=ATTEMPT_TIMEOUT_SECONDS,
        )
    finally:
        await _cleanup_attempt(project, environment, state)


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
