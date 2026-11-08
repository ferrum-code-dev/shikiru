from __future__ import annotations

import asyncio
import json
import os
import shlex
import shutil
import signal
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, cast

import psutil
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from shikiru.bootstrap import build_operator
from shikiru.clock import SystemClock
from shikiru.domain.commands import ResolutionKind
from shikiru.domain.escalation import EscalationReason
from shikiru.domain.events import Event, EventKind
from shikiru.domain.reports import REPORT_FILENAME, Verdict
from shikiru.domain.tasks import Hold, Role, Status, Step
from shikiru.engine.takeover import HAND_BACK_MESSAGE
from shikiru.engine.turn_processes import TURN_PID_NAME, read_turn_pid
from shikiru.errors import ShikiruError
from shikiru.operator.commands import AckStatus
from shikiru.operator.lifecycle import CLI_ENGINE_COMMAND
from shikiru.paths import MESSAGES_DIR_NAME, run_dir, state_db_path, turns_dir
from shikiru.process import SubprocessRunner
from shikiru.store.database import Database
from shikiru.store.events import EventLog
from shikiru.store.runs import RunStore
from shikiru.store.tasks import TASK_ID_PREFIX
from shikiru.tmux.client import exact_target
from shikiru.tmux.session import session_name
from shikiru.view_log import view_log_path
from tests.e2e.budgets import (
    AUTOMATIC_RERUNS,
    CLEANUP_TIMEOUT_SECONDS,
    CLI_TIMEOUT_SECONDS,
    INVOCATION_BUDGET,
    WAIT_CEILING_SECONDS,
    InvocationBudget,
    outer_timeout_seconds,
)
from tests.e2e.diagnostics import (
    SANDBOX_DENIAL_NOTE,
    ShikiruFailure,
    Snapshot,
    rerun_reason,
    take_snapshot,
    write_diagnostics,
)
from tests.e2e.real_agent_config import REAL_AGENT, REAL_MODEL, REAL_ROLE_LADDERS
from tests.e2e.watchdog import guarded
from tests.support.e2e_attempts import ATTEMPT_FAILED, ATTEMPT_PASSED, AttemptRecord
from tests.support.factories import CPU_UNCAPPED_PCT
from tests.support.git_repo import commit_all, init_repo
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable

    from shikiru.process import ProcessIdentity
    from tests.support.tmux import TmuxServer


REAL_E2E_ENV_VAR = "SHIKIRU_E2E"
REAL_E2E_ENABLED = "1"
BASE_BRANCH = "main"
TASK_ID = "T-1"
DIAGNOSTICS_DATE_FORMAT = "%Y-%m-%d"
DIAGNOSTICS_INVOCATION_FORMAT = "%Y%m%dT%H%M%SZ"
CLOCK = SystemClock()
DIAGNOSTICS_DIRECTORY = (
    Path(__file__).parents[2]
    / "build"
    / "e2e"
    / "diagnostics"
    / CLOCK.now().strftime(DIAGNOSTICS_INVOCATION_FORMAT)
)
INVOCATION = InvocationBudget(CLOCK, INVOCATION_BUDGET)
INTEGRATION_WAIT_SECONDS = 60
MAX_QUOTA_WAIT_SECONDS = 300
RUN_STATUS_LIVE = "live"
TASK_LIST_KEYS = ("active_tasks", "queued_tasks")
pytestmark = [pytest.mark.asyncio, pytest.mark.e2e, pytest.mark.real_e2e]
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
MAX_PARALLEL = 2
E3_REQUEST = "Implement all tasks in phase B"
E3_TRACKER_TASKS = ("B1", "B2", "B3")
E3_CHECK = "triple(4) == 12 and negate(5) == -5 and triple_negated(2) == -6"
CONFLICT_TURN_PATTERN = f"*-{Step.INTEGRATE.value}-i*"
CONFLICT_MARKER = "^<<<<<<< "
E4_REQUEST = "Implement task C1 from tasks/plan.md"
E4_ANSWER = (
    "Product decision: amounts exactly halfway between two whole numbers round up, away from "
    "zero, so round_price(2.5) must return 3 and round_price(3.5) must return 4."
)
E4_CHECK = "round_price(2.5) == 3 and round_price(3.5) == 4 and round_price(2.4) == 2"
MCP_COMMAND = "mcp"
MCP_WAIT_SECONDS = 60.0
MCP_RESULT_KEY = "result"
E6_SECOND_TASK_ID = "T-2"
E6_TUI_EXIT_KEYS = ("C-c", "C-c")
PANE_FORMAT_SEPARATOR = "\t"
PANE_STATE_FORMAT = PANE_FORMAT_SEPARATOR.join(
    ("#{pane_dead}", "#{pane_current_command}", "#{pane_start_command}")
)
PANE_ALIVE = "0"


FIXTURE_NAME = "sample-project"


@dataclass(frozen=True, slots=True)
class PreparedProject:
    root: Path
    initial_sha: str


@dataclass(slots=True)
class AttemptState:
    lifecycle_started: bool = False
    task_ids: list[str] = field(default_factory=lambda: [TASK_ID])


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _require_shikiru(condition: bool, message: str) -> None:
    if not condition:
        raise ShikiruFailure(message)


REAL_ROLE_OVERRIDES = tuple(
    argument
    for role, ladder in REAL_ROLE_LADDERS.items()
    for rung in ladder
    for argument in (
        "--set",
        f'roles.{role}.ladder=[{{agent="{rung["agent"]}",model="{rung["model"]}",effort="{rung["effort"]}"}}]',
    )
)
REAL_OVERRIDES = (
    "--set",
    f"orchestration.timeouts.integration_wait={INTEGRATION_WAIT_SECONDS}",
    "--set",
    f"orchestration.timeouts.max_quota_wait={MAX_QUOTA_WAIT_SECONDS}",
    "--set",
    "orchestration.poll_seconds=1",
    "--set",
    f"orchestration.resources.max_cpu_pct={CPU_UNCAPPED_PCT}",
    "--set",
    "orchestration.resources.min_free_ram_gb=0",
    "--set",
    'dashboard.widgets=["header"]',
    "--set",
    f"orchestration.max_parallel={MAX_PARALLEL}",
    *REAL_ROLE_OVERRIDES,
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
        _require_shikiru(False, "tmux is required for real-agent E2E")
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


def _task_status(status: dict[str, object] | None) -> object:
    detail = _task_detail(status)
    task = None if detail is None else detail.get("task")
    return cast("dict[str, object]", task).get("status") if isinstance(task, dict) else None


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


async def _wait_done(
    project: PreparedProject, environment: dict[str, str], state: AttemptState
) -> None:
    async def done() -> bool:
        statuses = [
            _task_status(status)
            for status in await _task_tree_statuses(project.root, environment, state)
        ]
        if all(status == Status.DONE.value for status in statuses):
            return True
        _require(
            not any(
                status in {Status.ESCALATED.value, Status.CANCELLED.value} for status in statuses
            ),
            f"task tree ended before completion: {statuses}",
        )
        status = await _status(project.root, environment, task_id=None)
        _require_shikiru(
            status is not None and status.get("engine_up") is True,
            "engine stopped before task completed",
        )
        return False

    await wait_until(done, timeout=WAIT_CEILING_SECONDS)


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


def _assert_real_role_usage(
    statuses: dict[str, object] | list[dict[str, object] | None] | None,
) -> None:
    values = statuses if isinstance(statuses, list) else [statuses]
    roles: set[str] = set()
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
            model = session.get("model")
            role = session.get("role")
            usage = session.get("usage")
            _require(isinstance(role, str), "task session role missing")
            if not isinstance(role, str):
                continue
            _require(agent == REAL_AGENT, f"unexpected E2E agent for {role}: {agent}")
            _require(model == REAL_MODEL, f"unexpected E2E model for {role}: {model}")
            _require(isinstance(usage, dict), f"{role} usage missing")
            if not isinstance(usage, dict):
                continue
            input_tokens = cast("dict[str, object]", usage).get("input_tokens")
            output_tokens = cast("dict[str, object]", usage).get("output_tokens")
            _require(
                isinstance(input_tokens, int) and input_tokens > 0,
                f"{role} input usage missing",
            )
            _require(
                isinstance(output_tokens, int) and output_tokens > 0,
                f"{role} output usage missing",
            )
            roles.add(role)
    _require(
        {Role.LEADER.value, Role.IMPLEMENTER.value} <= roles,
        f"leader and implementer session usage was not recorded: {roles}",
    )


async def _assert_git_and_tracker(project: PreparedProject, *task_names: str) -> None:
    tracker = await _read_text(project.root / "tasks" / "todo.md")
    for task_name in task_names:
        _require(f"- [x] {task_name}" in tracker, f"{task_name} was not ticked")
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


async def _assert_behaviour(project: PreparedProject, assertion: str) -> None:
    check = f"from sample_project import *; assert {assertion}"
    result = await SubprocessRunner().run(
        [sys.executable, "-c", check], timeout=CLI_TIMEOUT_SECONDS, cwd=project.root, check=False
    )
    _require(result.returncode == 0, f"{assertion} failed: {result.stderr[-1000:]}")
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


def _assert_views_recorded(project: PreparedProject, task_ids: list[str]) -> None:
    for role in ACTING_ROLES:
        views = [view_log_path(run_dir(project.root, task_id), role.value) for task_id in task_ids]
        _require(
            any(view.is_file() and view.stat().st_size > 0 for view in views),
            f"{role.value} view log is empty",
        )


async def _send_edge_case_during_first_implementation(project: PreparedProject) -> None:
    def implementing() -> bool:
        _require(not _verify_turns(project), "implementation finished before the message was sent")
        return any(turns_dir(project.root, TASK_ID).glob(FIRST_IMPLEMENT_TURN_PATTERN))

    await wait_until(implementing, timeout=WAIT_CEILING_SECONDS)
    services = build_operator(project.root)
    try:
        ack = await services.commands.send_message(TASK_ID, Role.LEADER, E2_MESSAGE)
    finally:
        services.close()
    _require(ack.status is not AckStatus.REJECTED, f"send_message rejected: {ack.message}")


async def _open_task_windows(project: PreparedProject, tmux_server: TmuxServer) -> set[str]:
    target = exact_target(session_name(project.root.name))
    windows = await tmux_server.run(
        "list-windows",
        "-t",
        target,
        "-F",
        "#{window_name}",
        check=True,
    )
    return {
        name.split(maxsplit=1)[0]
        for name in windows.stdout.splitlines()
        if name.startswith(TASK_ID_PREFIX)
    }


async def _assert_windows_closed(
    project: PreparedProject, tmux_server: TmuxServer, task_ids: list[str]
) -> None:
    remaining = await _open_task_windows(project, tmux_server) & set(task_ids)
    _require(not remaining, f"task windows remained open: {sorted(remaining)}")


async def _read_text(path: Path) -> str:
    return await asyncio.to_thread(path.read_text)


type ScenarioBody = Callable[
    [PreparedProject, dict[str, str], TmuxServer, AttemptState], Awaitable[None]
]


class AttemptFailed(Exception):
    def __init__(
        self, error: Exception, rerun_reason: str | None, diagnostics: Path | None
    ) -> None:
        super().__init__(str(error))
        self.error = error
        self.rerun_reason = rerun_reason
        self.diagnostics = diagnostics


class AttemptLog:
    def __init__(self, attempt: int, record: Callable[[AttemptRecord], None]) -> None:
        self._attempt = attempt
        self._record = record
        self._started = CLOCK.now()
        self._cost: float | None = None

    def observe_cost(self, cost: float | None) -> None:
        if cost is not None:
            self._cost = (self._cost or 0.0) + cost

    def finish(
        self,
        error: BaseException | None = None,
        rerun_reason: str | None = None,
        diagnostics: Path | None = None,
    ) -> None:
        self._record(
            AttemptRecord(
                attempt=self._attempt,
                status=ATTEMPT_PASSED if error is None else ATTEMPT_FAILED,
                failure_class=None if error is None else type(error).__name__,
                rerun_reason=rerun_reason,
                cost=self._cost,
                duration_seconds=(CLOCK.now() - self._started).total_seconds(),
                diagnostics=None if diagnostics is None else str(diagnostics),
            )
        )


async def _run_attempt(
    scenario: str,
    tmp_path: Path,
    tmux_server: TmuxServer,
    record_cost: Callable[[float | None], None],
    body: ScenarioBody,
    *,
    attempt: int,
) -> None:
    timeout = INVOCATION.attempt_timeout_seconds(scenario)
    project = await _prepare_project(tmp_path)
    environment = _environment(project.root, tmux_server)
    state = AttemptState()
    runner = SubprocessRunner()

    def probe() -> Awaitable[Snapshot]:
        return take_snapshot(project.root, runner, CLOCK)

    try:
        try:
            await guarded(
                _execute(project, environment, tmux_server, state, body),
                probe=probe,
                clock=CLOCK,
                timeout=timeout,
            )
        except (AssertionError, OSError, ShikiruError) as error:
            snapshot = await probe()
            stamp = CLOCK.now().strftime(DIAGNOSTICS_DATE_FORMAT)
            path = DIAGNOSTICS_DIRECTORY / f"{stamp}-{scenario}-attempt-{attempt}.txt"
            write_diagnostics(path, error, snapshot)
            error.add_note(f"diagnostics: {path}")
            if snapshot.sandbox_denial is not None:
                error.add_note(SANDBOX_DENIAL_NOTE.format(marker=snapshot.sandbox_denial))
            raise AttemptFailed(error, rerun_reason(error, snapshot), path) from error
    finally:
        await _record_observed_cost(project, environment, state, record_cost)
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
    await body(project, environment, tmux_server, state)
    task_statuses = await _task_tree_statuses(project.root, environment, state)
    _assert_real_role_usage(task_statuses)
    _assert_views_recorded(project, state.task_ids)
    await _assert_live_checkout_untouched(project)
    await _assert_windows_closed(project, tmux_server, state.task_ids)


async def _run_with_reruns(
    scenario: str,
    tmp_path: Path,
    tmux_server: TmuxServer,
    record_attempt: Callable[[AttemptRecord], None],
    body: ScenarioBody,
) -> None:
    if os.environ.get(REAL_E2E_ENV_VAR) != REAL_E2E_ENABLED:
        pytest.skip(f"set {REAL_E2E_ENV_VAR}=1 to run real-agent E2E")
    for attempt in range(AUTOMATIC_RERUNS + 1):
        log = AttemptLog(attempt, record_attempt)
        try:
            await _run_attempt(
                scenario,
                tmp_path / f"attempt-{attempt}",
                tmux_server,
                log.observe_cost,
                body,
                attempt=attempt,
            )
        except AttemptFailed as failed:
            log.finish(failed.error, failed.rerun_reason, failed.diagnostics)
            if attempt == AUTOMATIC_RERUNS or failed.rerun_reason is None:
                raise failed.error from None
        except BaseException as error:
            log.finish(error)
            raise
        else:
            log.finish()
            return


async def _e1_body(
    project: PreparedProject,
    environment: dict[str, str],
    _tmux_server: TmuxServer,
    state: AttemptState,
) -> None:
    await _submit(project, environment, E1_REQUEST)
    await _wait_done(project, environment, state)
    await _assert_git_and_tracker(project, "A1")
    await _assert_a1_implementation(project)


async def _e2_body(
    project: PreparedProject,
    environment: dict[str, str],
    _tmux_server: TmuxServer,
    state: AttemptState,
) -> None:
    await _submit(project, environment, E2_REQUEST)
    await _send_edge_case_during_first_implementation(project)
    await _wait_done(project, environment, state)
    _assert_message_drove_review(project)
    await _assert_git_and_tracker(project, "A2")
    await _assert_behaviour(project, f"{E2_EDGE_CASE_CALL} == {E2_EDGE_CASE_RESULT}")


async def _e5_body(
    project: PreparedProject,
    environment: dict[str, str],
    _tmux_server: TmuxServer,
    state: AttemptState,
) -> None:
    await _submit(project, environment, E1_REQUEST)
    orphan, session_id = await _wait_for_implement_turn(project)
    try:
        await asyncio.to_thread(_kill_engine, project)
        await _start(project, environment, state)
        await _wait_done(project, environment, state)
        _require(not SubprocessRunner().is_running(orphan), "the orphaned turn process survived")
        _assert_same_implementer_session(project, session_id)
        await _assert_no_duplicate_commits(project)
        await _assert_git_and_tracker(project, "A1")
    finally:
        SubprocessRunner().signal_group(orphan, signal.SIGKILL)


async def _wait_for_implement_turn(project: PreparedProject) -> tuple[ProcessIdentity, str]:
    pid_path = run_dir(project.root, TASK_ID) / Role.IMPLEMENTER.value / TURN_PID_NAME
    connection = Database(state_db_path(project.root)).connect_readonly()
    try:
        events = EventLog(connection, CLOCK)
        runs = RunStore(connection, CLOCK)

        def implementing() -> bool:
            started = any(
                event.data.get("step") == Step.IMPLEMENT.value
                for event in events.since(0, kinds=[EventKind.TURN_STARTED], task_id=TASK_ID)
            )
            run = runs.get_live(TASK_ID, Role.IMPLEMENTER)
            return started and pid_path.is_file() and run is not None and bool(run.session_id)

        await wait_until(implementing, timeout=WAIT_CEILING_SECONDS)
        run = runs.get_live(TASK_ID, Role.IMPLEMENTER)
        assert run is not None
        assert run.session_id is not None
        return read_turn_pid(pid_path), run.session_id
    finally:
        connection.close()


def _kill_engine(project: PreparedProject) -> None:
    engines = [
        process
        for process in psutil.process_iter(["cmdline"])
        if (command := process.info["cmdline"])
        and command[-1] == CLI_ENGINE_COMMAND
        and str(project.root) in command
    ]
    _require(len(engines) == 1, f"expected one engine process, found {len(engines)}")
    engines[0].send_signal(signal.SIGKILL)
    engines[0].wait(CLI_TIMEOUT_SECONDS)


def _assert_same_implementer_session(project: PreparedProject, session_id: str) -> None:
    connection = Database(state_db_path(project.root)).connect_readonly()
    try:
        runs = [
            run
            for run in RunStore(connection, CLOCK).for_task(TASK_ID)
            if run.role is Role.IMPLEMENTER
        ]
    finally:
        connection.close()
    sessions = [run.session_id for run in runs]
    _require(sessions == [session_id], f"implementer sessions changed: {sessions}")
    view = view_log_path(run_dir(project.root, TASK_ID), Role.IMPLEMENTER.value)
    started = [
        row["session_id"]
        for row in map(json.loads, view.read_text().splitlines())
        if row["signal"] == "SessionStarted"
    ]
    _require(
        len(started) > 1 and set(started) == {session_id},
        f"the turn did not re-run on the same session: {started}",
    )


async def _assert_no_duplicate_commits(project: PreparedProject) -> None:
    log = await SubprocessRunner().run(
        ["git", "log", "-p", f"{project.initial_sha}..HEAD"],
        timeout=CLI_TIMEOUT_SECONDS,
        cwd=project.root,
        check=True,
    )
    patches = await SubprocessRunner().run(
        ["git", "patch-id", "--stable"],
        timeout=CLI_TIMEOUT_SECONDS,
        cwd=project.root,
        input_text=log.stdout,
        check=True,
    )
    patch_ids = [line.split()[0] for line in patches.stdout.splitlines()]
    _require(len(patch_ids) == len(set(patch_ids)), f"duplicate commits: {patches.stdout}")


@pytest.mark.timeout(outer_timeout_seconds("e5"))
async def test_real_agents_e5_restart_resumes_the_interrupted_turn(
    e2e_project_dir: Path,
    tmux_server: TmuxServer,
    record_e2e_attempt: Callable[[AttemptRecord], None],
) -> None:
    await _run_with_reruns("e5", e2e_project_dir, tmux_server, record_e2e_attempt, _e5_body)


@pytest.mark.timeout(outer_timeout_seconds("e1"))
async def test_real_agents_e1_completes_and_integrates(
    e2e_project_dir: Path,
    tmux_server: TmuxServer,
    record_e2e_attempt: Callable[[AttemptRecord], None],
) -> None:
    await _run_with_reruns("e1", e2e_project_dir, tmux_server, record_e2e_attempt, _e1_body)


@pytest.mark.timeout(outer_timeout_seconds("e2"))
async def test_real_agents_e2_delivers_operator_message_to_verify(
    e2e_project_dir: Path,
    tmux_server: TmuxServer,
    record_e2e_attempt: Callable[[AttemptRecord], None],
) -> None:
    await _run_with_reruns("e2", e2e_project_dir, tmux_server, record_e2e_attempt, _e2_body)


async def _record_observed_cost(
    project: PreparedProject,
    environment: dict[str, str],
    state: AttemptState,
    record_cost: Callable[[float | None], None],
) -> None:
    try:
        statuses = await _task_tree_statuses(project.root, environment, state)
    except OSError, ShikiruError, json.JSONDecodeError:
        return
    record_cost(_cost(statuses))


def _task_summaries(status: dict[str, object] | None) -> dict[str, dict[str, object]]:
    if status is None:
        return {}
    values: list[object] = []
    for key in TASK_LIST_KEYS:
        listed = status.get(key)
        if isinstance(listed, list):
            values.extend(cast("list[object]", listed))
    summaries: dict[str, dict[str, object]] = {}
    for value in values:
        if not isinstance(value, dict):
            continue
        summary = cast("dict[str, object]", value)
        task_id = summary.get("id")
        if isinstance(task_id, str):
            summaries[task_id] = summary
    return summaries


def _track_task_tree(status: dict[str, object] | None, task_ids: list[str]) -> None:
    tasks = _task_summaries(status)
    for task_id in task_ids:
        task_ids.extend(
            child_id
            for child_id, task in tasks.items()
            if task.get("parent_id") == task_id and child_id not in task_ids
        )


async def _task_tree_statuses(
    project_root: Path, environment: dict[str, str], state: AttemptState
) -> list[dict[str, object] | None]:
    _track_task_tree(await _status(project_root, environment, task_id=None), state.task_ids)
    return [await _status(project_root, environment, task_id) for task_id in state.task_ids]


async def _initialize(project: PreparedProject, environment: dict[str, str]) -> None:
    await _run_checked(project, environment, "init", "--yes")
    config = await _read_text(project.root / ".shikiru" / "config.toml")
    missing = [REAL_AGENT] if f"[agents.{REAL_AGENT}]" not in config else []
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
        _require_shikiru(code == 0, f"{operation} failed with exit {code}: {detail[:1000]}")
    return stdout, stderr


async def _cleanup_attempt(
    project: PreparedProject, environment: dict[str, str], state: AttemptState
) -> None:
    if not state.lifecycle_started:
        return
    try:
        await asyncio.wait_for(_stop(project, environment), timeout=CLEANUP_TIMEOUT_SECONDS)
    except TimeoutError as error:
        _require_shikiru(False, "real-agent cleanup timed out")
        raise AssertionError from error


@pytest.mark.timeout(outer_timeout_seconds("e3"))
async def test_real_agents_e3_plans_and_runs_dependent_tasks_in_parallel(
    e2e_project_dir: Path,
    tmux_server: TmuxServer,
    record_e2e_attempt: Callable[[AttemptRecord], None],
) -> None:
    await _run_with_reruns("e3", e2e_project_dir, tmux_server, record_e2e_attempt, _e3_body)


@pytest.mark.timeout(outer_timeout_seconds("e4"))
async def test_real_agents_e4_mcp_operator_resolves_a_product_decision(
    e2e_project_dir: Path,
    tmux_server: TmuxServer,
    record_e2e_attempt: Callable[[AttemptRecord], None],
) -> None:
    """A scripted MCP client stands in for the AI Operator.

    It proves the MCP escalation flow (`wait_for_events` → `resolve_escalation`) with a fixed
    answer, not a model's judgement.
    """
    await _run_with_reruns("e4", e2e_project_dir, tmux_server, record_e2e_attempt, _e4_body)


@pytest.mark.timeout(outer_timeout_seconds("e6"))
async def test_real_agents_e6_take_over_holds_only_that_task(
    e2e_project_dir: Path,
    tmux_server: TmuxServer,
    record_e2e_attempt: Callable[[AttemptRecord], None],
) -> None:
    await _run_with_reruns("e6", e2e_project_dir, tmux_server, record_e2e_attempt, _e6_body)


async def _e3_body(
    project: PreparedProject,
    environment: dict[str, str],
    tmux_server: TmuxServer,
    state: AttemptState,
) -> None:
    await _submit(project, environment, E3_REQUEST)
    await _wait_parallel_new_task_windows(project, environment, tmux_server, state)
    await _wait_done(project, environment, state)
    new_task_ids = state.task_ids[1:]
    _assert_dependent_new_tasks(
        [await _status(project.root, environment, task_id) for task_id in new_task_ids]
    )
    _require(
        any(
            any(turns_dir(project.root, task_id).glob(CONFLICT_TURN_PATTERN))
            for task_id in new_task_ids
        ),
        "no new task resolved an integration conflict",
    )
    await _assert_no_conflict_markers(project)
    await _assert_git_and_tracker(project, *E3_TRACKER_TASKS)
    await _assert_behaviour(project, E3_CHECK)


async def _wait_parallel_new_task_windows(
    project: PreparedProject,
    environment: dict[str, str],
    tmux_server: TmuxServer,
    state: AttemptState,
) -> None:
    async def parallel() -> bool:
        if len(state.task_ids) == 1:
            status = await _status(project.root, environment, TASK_ID)
            _track_task_tree(status, state.task_ids)
            if len(state.task_ids) == 1 and _task_status(status) == Status.DONE.value:
                _require(False, "E3 request completed without planning child tasks")
        new_task_ids = set(state.task_ids[1:])
        return len(await _open_task_windows(project, tmux_server) & new_task_ids) >= MAX_PARALLEL

    await wait_until(parallel, timeout=WAIT_CEILING_SECONDS)


def _assert_dependent_new_tasks(statuses: list[dict[str, object] | None]) -> None:
    tasks = [_task_record(status) for status in statuses]
    new_task_ids = {task.get("id") for task in tasks}
    dependent = [
        task
        for task in tasks
        if (depends_on := task.get("depends_on"))
        and isinstance(depends_on, list)
        and set(cast("list[object]", depends_on)) & new_task_ids
    ]
    _require(
        len(tasks) >= MAX_PARALLEL and bool(dependent),
        f"the plan did not enqueue dependent new tasks: {tasks}",
    )


def _task_record(status: dict[str, object] | None) -> dict[str, object]:
    detail = _task_detail(status)
    task = None if detail is None else detail.get("task")
    _require(isinstance(task, dict), "task record missing")
    return cast("dict[str, object]", task)


async def _assert_no_conflict_markers(project: PreparedProject) -> None:
    result = await SubprocessRunner().run(
        ["git", "grep", "-n", "-E", CONFLICT_MARKER],
        timeout=CLI_TIMEOUT_SECONDS,
        cwd=project.root,
        check=False,
    )
    _require(not result.stdout.strip(), f"conflict markers remain: {result.stdout}")


async def _e4_body(
    project: PreparedProject,
    environment: dict[str, str],
    _tmux_server: TmuxServer,
    state: AttemptState,
) -> None:
    await _submit(project, environment, E4_REQUEST)
    async with _mcp_session(project, environment) as session:
        escalation_id = await _wait_for_decision_escalation(session)
        packet = await _call_tool(session, "get_escalation", {"escalation_id": escalation_id})
        _require(
            isinstance(packet, dict) and bool(cast("dict[str, object]", packet).get("packet")),
            f"escalation {escalation_id} has no packet",
        )
        ack = await _call_tool(
            session,
            "resolve_escalation",
            {
                "escalation_id": escalation_id,
                "resolution": {"kind": ResolutionKind.ANSWER.value, "text": E4_ANSWER},
            },
        )
        _require(
            isinstance(ack, dict)
            and cast("dict[str, object]", ack).get("status") == AckStatus.DONE,
            f"resolve_escalation was not applied: {ack}",
        )
    await _wait_done(project, environment, state)
    await _assert_git_and_tracker(project, "C1")
    await _assert_behaviour(project, E4_CHECK)


@asynccontextmanager
async def _mcp_session(
    project: PreparedProject, environment: dict[str, str]
) -> AsyncGenerator[ClientSession]:
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "shikiru", "--project", str(project.root), MCP_COMMAND],
        env=environment,
        cwd=project.root,
    )
    try:
        async with stdio_client(server) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            yield session
    except BaseExceptionGroup as group:
        raise _sole_error(group) from group


def _sole_error(group: BaseExceptionGroup[BaseException]) -> BaseException:
    errors: tuple[BaseException, ...] = group.exceptions
    if len(errors) != 1:
        return group
    sole = errors[0]
    if isinstance(sole, BaseExceptionGroup):
        return _sole_error(cast("BaseExceptionGroup[BaseException]", sole))
    return sole


async def _call_tool(session: ClientSession, name: str, arguments: dict[str, object]) -> object:
    result = await session.call_tool(name, arguments)
    _require_shikiru(not result.isError, f"MCP {name} failed: {result.content}")
    content = result.structuredContent
    _require_shikiru(content is not None, f"MCP {name} returned no structured content")
    assert content is not None
    return content[MCP_RESULT_KEY] if set(content) == {MCP_RESULT_KEY} else content


async def _wait_for_decision_escalation(session: ClientSession) -> str:
    cursor = 0
    escalation_ids: list[str] = []

    async def escalated() -> bool:
        nonlocal cursor
        events = await _call_tool(
            session,
            "wait_for_events",
            {
                "since_id": cursor,
                "kinds": [EventKind.ESCALATION.value, EventKind.TASK_DONE.value],
                "task_id": TASK_ID,
                "timeout_s": MCP_WAIT_SECONDS,
            },
        )
        _require(isinstance(events, list), f"wait_for_events returned {events}")
        for value in cast("list[object]", events):
            event = Event.model_validate(value)
            cursor = max(cursor, event.id)
            _require(event.kind is EventKind.ESCALATION, "the task finished without escalating")
            reason = event.data.get("reason")
            _require(
                reason == EscalationReason.DECISION_REQUIRED.value,
                f"escalated for {reason}, not a product decision",
            )
            escalation_ids.append(cast("str", event.data["escalation_id"]))
        return bool(escalation_ids)

    await wait_until(escalated, timeout=WAIT_CEILING_SECONDS)
    return escalation_ids[0]


async def _e6_body(
    project: PreparedProject,
    environment: dict[str, str],
    tmux_server: TmuxServer,
    state: AttemptState,
) -> None:
    await _submit(project, environment, E1_REQUEST)
    await _submit(project, environment, E2_REQUEST)
    state.task_ids.append(E6_SECOND_TASK_ID)
    _, session_id = await _wait_for_implement_turn(project)
    await _take_over_implementer(project)
    pane_id = await _wait_for_agent_tui(project, environment, tmux_server, session_id)
    await _wait_second_task_done_while_first_held(project, environment)
    await tmux_server.run("send-keys", "-t", pane_id, *E6_TUI_EXIT_KEYS, check=True)
    await wait_until(
        lambda: bool(_task_events(project, EventKind.HANDED_BACK)), timeout=CLI_TIMEOUT_SECONDS
    )
    await _wait_done(project, environment, state)
    _assert_no_turn_while_held(project)
    _assert_hand_back_message_delivered(project)
    _assert_same_implementer_session(project, session_id)
    await _assert_git_and_tracker(project, "A1", "A2")


async def _take_over_implementer(project: PreparedProject) -> None:
    services = build_operator(project.root)
    try:
        ack = await services.commands.take_over(TASK_ID, Role.IMPLEMENTER)
    finally:
        services.close()
    _require(ack.status is AckStatus.DONE, f"take_over was not applied: {ack.message}")


async def _wait_for_agent_tui(
    project: PreparedProject,
    environment: dict[str, str],
    tmux_server: TmuxServer,
    session_id: str,
) -> str:
    first = _task_record(await _status(project.root, environment))
    second = _task_record(await _status(project.root, environment, E6_SECOND_TASK_ID))
    _require(
        Hold.INTERVENTION.value in cast("list[str]", first.get("holds"))
        and Hold.INTERVENTION.value not in cast("list[str]", second.get("holds")),
        f"intervention holds: {first.get('holds')} and {second.get('holds')}",
    )
    pane_id = cast("dict[str, str]", first.get("panes"))[Role.IMPLEMENTER.value]
    agent = _taken_over_agent(project)

    async def tui_shown() -> bool:
        state = await tmux_server.run("display-message", "-p", "-t", pane_id, PANE_STATE_FORMAT)
        dead, command, start_command = state.stdout.rstrip("\n").split(PANE_FORMAT_SEPARATOR)
        _require(dead == PANE_ALIVE, "the taken-over pane exited before hand back")
        _require(session_id in start_command, f"pane does not resume {session_id}")
        screen = await tmux_server.run("capture-pane", "-p", "-t", pane_id)
        return command == agent and bool(screen.stdout.strip())

    await wait_until(tui_shown, timeout=CLI_TIMEOUT_SECONDS)
    return pane_id


def _taken_over_agent(project: PreparedProject) -> str:
    connection = Database(state_db_path(project.root)).connect_readonly()
    try:
        run = RunStore(connection, CLOCK).get_taken_over(TASK_ID, Role.IMPLEMENTER)
    finally:
        connection.close()
    _require(run is not None, "the implementer run is not taken over")
    assert run is not None
    return run.agent


async def _wait_second_task_done_while_first_held(
    project: PreparedProject, environment: dict[str, str]
) -> None:
    async def second_done() -> bool:
        first = _task_record(await _status(project.root, environment))
        _require(
            Hold.INTERVENTION.value in cast("list[str]", first.get("holds")),
            "the intervention hold was lost before hand back",
        )
        second = _task_status(await _status(project.root, environment, E6_SECOND_TASK_ID))
        _require(
            second not in {Status.ESCALATED.value, Status.CANCELLED.value},
            f"{E6_SECOND_TASK_ID} ended as {second}",
        )
        return second == Status.DONE.value

    await wait_until(second_done, timeout=WAIT_CEILING_SECONDS)


def _task_events(project: PreparedProject, *kinds: EventKind) -> list[Event]:
    connection = Database(state_db_path(project.root)).connect_readonly()
    try:
        return EventLog(connection, CLOCK).since(0, kinds=kinds, task_id=TASK_ID)
    finally:
        connection.close()


def _assert_no_turn_while_held(project: PreparedProject) -> None:
    events = _task_events(
        project, EventKind.TAKEN_OVER, EventKind.HANDED_BACK, EventKind.TURN_STARTED
    )
    taken = next(event.id for event in events if event.kind is EventKind.TAKEN_OVER)
    handed = next(event.id for event in events if event.kind is EventKind.HANDED_BACK)
    held_turns = [
        event
        for event in events
        if event.kind is EventKind.TURN_STARTED and taken < event.id < handed
    ]
    _require(not held_turns, f"turns started while taken over: {held_turns}")


def _assert_hand_back_message_delivered(project: PreparedProject) -> None:
    delivered = [
        path.read_text()
        for path in turns_dir(project.root, TASK_ID).glob(f"*/{MESSAGES_DIR_NAME}/*")
    ]
    _require(HAND_BACK_MESSAGE in delivered, "the hand-back message was not delivered")
