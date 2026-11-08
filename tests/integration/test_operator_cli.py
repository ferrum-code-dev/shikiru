from __future__ import annotations

import asyncio
import json
import os
import shlex
import stat
import sys
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from shikiru.bootstrap import build_operator
from shikiru.clock import SystemClock
from shikiru.config.loader import load_settings
from shikiru.domain.commands import Submit
from shikiru.domain.escalation import options_for
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import EscalationReason, Hold, Status, Step
from shikiru.engine.daemon import EngineLock
from shikiru.operator.lifecycle import PALETTE_COMMAND, ProjectLifecycle, StartResult
from shikiru.paths import engine_lock_path, state_db_path
from shikiru.process import SubprocessRunner
from shikiru.store.commands import CommandQueue
from shikiru.store.database import Database
from shikiru.store.escalations import EscalationStore
from shikiru.store.tasks import TaskStore
from shikiru.tmux.client import Tmux, exact_target
from shikiru.tmux.session import (
    DETACHED_SIZE,
    HOME_WINDOW_NAME,
    PROJECT_DIR_ENV_VAR,
    SessionView,
    session_name,
)
from tests.support.clock import FakeClock
from tests.support.store import seed_task
from tests.support.waiting import PROCESS_WAIT_SECONDS, wait_until

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


def _assert_unknown_task_status(
    result: tuple[int, str, str], project_root: Path, task_id: str
) -> None:
    code, stdout, stderr = result
    assert code == 1
    assert stdout == ""
    assert task_id in stderr
    assert "--project" in stderr
    assert str(project_root) in stderr
    assert "status" in stderr


async def test_cli_human_init_submit_status_and_already_stopped(tmp_path: Path) -> None:
    await _git_project(tmp_path)

    code, stdout, stderr = await _cli(tmp_path, "init", "--yes")
    assert code == 0
    assert stderr == ""
    assert str(tmp_path / ".shikiru" / "config.toml") in stdout

    code, stdout, stderr = await _cli(tmp_path, "status")
    assert code == 0
    assert stderr == ""
    assert "⚙︎ down" in stdout
    assert "queued 0" in stdout
    assert all(section not in stdout for section in ("Tasks", "Queue", "Escalations", "Faults"))
    _assert_unknown_task_status(await _cli(tmp_path, "status", "T-999"), tmp_path, "T-999")

    code, stdout, stderr = await _cli(tmp_path, "submit", "human task")
    assert code == 0
    assert stderr == ""
    assert "T-1" in stdout
    assert "C-1" in stdout
    assert "pending" in stdout.lower()

    code, stdout, stderr = await _cli(tmp_path, "status")
    assert code == 0
    assert stderr == ""
    assert "⚙︎ down" in stdout
    assert "queued 0" in stdout

    code, stdout, stderr = await _cli(tmp_path, "stop")
    assert code == 0
    assert stderr == ""
    assert stdout.lstrip().startswith("✔")
    assert len(stdout.splitlines()) == 1
    assert "\x1b[" not in stdout


@pytest.mark.parametrize("outcome", ["pending", "applied", "rejected"])
async def test_human_submit_reports_the_engine_acknowledgement_and_allocated_identity(
    tmp_path: Path,
    outcome: str,
) -> None:
    await _git_project(tmp_path)
    code, _, stderr = await _cli(tmp_path, "init", "--yes")
    assert code == 0
    assert stderr == ""

    database = Database(state_db_path(tmp_path))
    connection = database.connect()
    queue = CommandQueue(connection, SystemClock())
    lock = EngineLock(engine_lock_path(tmp_path))
    if outcome != "pending":
        lock.acquire()
    unsafe_value = f"[bold]$(touch {tmp_path / 'must-not-exist'})"
    try:
        cli_task = asyncio.create_task(_cli(tmp_path, "submit", "human task"))
        if outcome != "pending":
            await wait_until(lambda: bool(queue.pending()), timeout=PROCESS_WAIT_SECONDS)
            command = queue.pending()[0]
            assert isinstance(command.command, Submit)
            if outcome == "applied":
                queue.mark_done(command.id, {"task_id": command.command.task_id})
            else:
                queue.mark_rejected(command.id, {"error": unsafe_value})
        code, stdout, stderr = await cli_task
    finally:
        if outcome != "pending":
            lock.release()
        connection.close()

    assert stderr == ""
    assert "T-1" in stdout
    assert "C-1" in stdout
    if outcome == "pending":
        assert code == 0
        assert stdout.lstrip().startswith("·")
    elif outcome == "applied":
        assert code == 0
        assert stdout.lstrip().startswith("✔")
        assert "shikiru status T-1" in " ".join(stdout.split())
        assert not (tmp_path / "must-not-exist").exists()
    else:
        assert code == 4
        assert stdout.lstrip().startswith("✕")
        assert unsafe_value in stdout
        assert not (tmp_path / "must-not-exist").exists()


async def test_human_status_limits_rows_and_json_keeps_full_task_collections(
    tmp_path: Path,
) -> None:
    await _git_project(tmp_path)
    code, stdout, stderr = await _cli(tmp_path, "init", "--yes")
    assert code == 0
    assert stderr == ""

    database = Database(state_db_path(tmp_path))
    connection = database.connect()
    clock = FakeClock(START)
    tasks = TaskStore(connection, clock)
    escalations = EscalationStore(connection, clock)
    project_tasks = [
        seed_task(connection, clock, title="escalated one"),
        seed_task(connection, clock, title="escalated two"),
        seed_task(connection, clock, title="running one"),
        seed_task(connection, clock, title="integrating"),
        seed_task(connection, clock, title="running two"),
    ]
    for task, status, step in (
        (project_tasks[0], Status.ESCALATED, Step.CONSULT),
        (project_tasks[1], Status.ESCALATED, Step.VERIFY),
        (project_tasks[2], Status.RUNNING, Step.IMPLEMENT),
        (project_tasks[3], Status.INTEGRATING, Step.INTEGRATE),
        (project_tasks[4], Status.RUNNING, Step.TRIAGE),
    ):
        tasks.apply_transition(task.id, Transition.stay(task, step=step, status=status))
    for index, task in enumerate(project_tasks[:2], start=1):
        escalations.open(
            task.id,
            step=task.step,
            reason=EscalationReason.DECISION_REQUIRED,
            headline=f"decision {index}",
            packet_path=f"runs/{task.id}/escalations/E-{index}.md",
            options=options_for(EscalationReason.DECISION_REQUIRED),
        )
    tasks.set_slot(project_tasks[2].id, 0)
    tasks.set_slot(project_tasks[3].id, 1)
    queued_tasks = [
        seed_task(connection, clock, title=f"queued {index}", priority=index)
        for index in range(1, 11)
    ]
    latest = seed_task(connection, clock, title="done latest")
    higher_sequence = seed_task(connection, clock, title="done earlier")
    clock.advance(1)
    tasks.apply_transition(
        higher_sequence.id,
        Transition.stay(higher_sequence, step=None, status=Status.DONE),
    )
    clock.advance(1)
    tasks.apply_transition(latest.id, Transition.stay(latest, step=None, status=Status.DONE))
    tasks.set_holds(latest.id, frozenset({Hold.PAUSED}))
    connection.close()

    code, stdout, stderr = await _cli(tmp_path, "status")

    assert code == 0
    assert stderr == ""
    task_rows = [line for line in stdout.splitlines() if line.startswith("T-")]
    expected_ids = [
        *(task.id for task in project_tasks[:5]),
        *(task.id for task in reversed(queued_tasks[-4:])),
        latest.id,
    ]
    assert [line.split(" · ", 1)[0] for line in task_rows] == expected_ids
    assert len(task_rows) == 10
    assert task_rows[-1].rstrip() == f"{latest.id} · done latest · Done"
    assert "queued 10" in stdout
    assert "escalations 2" in stdout

    _assert_unknown_task_status(await _cli(tmp_path, "status", "T-999"), tmp_path, "T-999")
    _assert_unknown_task_status(
        await _cli(tmp_path, "--json", "status", "T-999"), tmp_path, "T-999"
    )

    code, stdout, stderr = await _cli(tmp_path, "--json", "status")

    assert code == 0
    assert stderr == ""
    status_value = json.loads(stdout)
    assert [task["id"] for task in status_value["active_tasks"]] == [
        task.id for task in project_tasks
    ]
    assert [task["id"] for task in status_value["queued_tasks"]] == [
        task.id for task in reversed(queued_tasks)
    ]


@pytest.mark.parametrize("partial_state", [False, True])
async def test_cli_requires_a_complete_project_and_shows_recovery_options(
    tmp_path: Path, partial_state: bool
) -> None:
    if partial_state:
        (tmp_path / ".shikiru").mkdir()

    code, stdout, stderr = await _cli(tmp_path, "status")

    assert code == 3
    assert stdout == ""
    assert str(tmp_path) in stderr
    assert "shikiru --project" in stderr
    assert "shikiru --project PATH COMMAND" in stderr
    assert "shikiru --help" in stderr
    assert "defaults.toml" not in stderr
    if partial_state:
        assert str(tmp_path / ".shikiru" / "config.toml") in stderr
    assert "\x1b[" not in stderr


async def test_submit_parser_rejects_missing_request_before_project_or_queue_writes(
    tmp_path: Path,
) -> None:
    await _git_project(tmp_path)

    for arguments in (("submit",), ("--json", "submit"), ("submit", "--priority", "invalid")):
        code, stdout, stderr = await _cli(tmp_path, *arguments)
        assert code == 2
        assert stdout == ""
        assert stderr
        assert not (tmp_path / ".shikiru").exists()

    code, _, stderr = await _cli(tmp_path, "init", "--yes")
    assert code == 0
    assert stderr == ""

    connection = Database(state_db_path(tmp_path)).connect()
    queue = CommandQueue(connection, FakeClock(START))
    try:
        code, stdout, stderr = await _cli(tmp_path, "submit")
        assert code == 2
        assert stdout == ""
        assert stderr
        assert queue.pending() == []

        code, stdout, stderr = await _cli(tmp_path, "--json", "submit", "")
        assert code == 0
        assert stderr == ""
        acknowledgement = json.loads(stdout)
        assert acknowledgement["task_id"] == "T-1"
        assert acknowledgement["status"] == "queued"

        pending = queue.pending()
        assert len(pending) == 1
        assert isinstance(pending[0].command, Submit)
        assert pending[0].command.task_id == "T-1"
        assert pending[0].command.request == ""
    finally:
        connection.close()


async def test_missing_project_repair_keeps_shell_metacharacters_literal(tmp_path: Path) -> None:
    project = tmp_path / "[bold]$(unsafe); project"
    await asyncio.to_thread(project.mkdir, parents=True)

    code, stdout, stderr = await _cli(project, "status")

    assert code == 3
    assert stdout == ""
    assert str(project) in stderr
    assert shlex.quote(str(project)) in " ".join(stderr.split())
    assert not (tmp_path / "unsafe").exists()


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
    binding_tokens = shlex.split(binding)
    if_shell_index = binding_tokens.index("if-shell")
    dashboard_route = shlex.split(binding_tokens[if_shell_index + 3])
    assert dashboard_route[0:2] == ["if-shell", "-F"]
    assert shlex.split(dashboard_route[3]) == ["send-keys", "F12"]
    run_shell_tokens = shlex.split(dashboard_route[4])
    popup_command = shlex.split(run_shell_tokens[1])
    assert popup_command == ["tmux", "display-popup", "-E", *PALETTE_COMMAND]


@pytest.mark.parametrize("repo_relative", ["child", ".."])
async def test_init_rejects_a_project_root_that_is_not_a_repository_root(
    tmp_path: Path, repo_relative: str
) -> None:
    project = tmp_path / "workspace" / "project"
    await asyncio.to_thread(project.mkdir, parents=True)
    repository = (project / repo_relative).resolve()
    await _git_project(repository)

    code, _, stderr = await _cli(project, "init", "--yes")
    flat_stderr = " ".join(stderr.split())

    assert code == 1
    assert str(project) in stderr
    if repo_relative == "..":
        assert shlex.quote(str(repository)) in flat_stderr
        assert "init --yes" in flat_stderr
    else:
        assert f"git -C {shlex.quote(str(project))} init" in flat_stderr
        assert "commit --allow-empty" in flat_stderr
    assert not (project / ".shikiru" / "config.toml").exists()


async def test_init_rejects_an_unborn_repository_before_writing_project_state(
    tmp_path: Path,
) -> None:
    await asyncio.to_thread(tmp_path.mkdir, parents=True, exist_ok=True)
    await SubprocessRunner().run(["git", "init", "-q"], timeout=10, cwd=tmp_path, check=True)

    code, stdout, stderr = await _cli(tmp_path, "init", "--yes")
    flat_stderr = " ".join(stderr.split())

    assert code == 1
    assert stdout == ""
    assert str(tmp_path) in stderr
    assert f"git -C {shlex.quote(str(tmp_path))} commit --allow-empty" in flat_stderr
    assert f"shikiru --project {shlex.quote(str(tmp_path))} init --yes" in flat_stderr
    assert "rev-parse" not in stderr
    assert not (tmp_path / ".shikiru").exists()
    exclude = tmp_path / ".git" / "info" / "exclude"
    assert not exclude.exists() or "/.shikiru/" not in exclude.read_text()


async def test_init_and_submit_create_the_state_directory_and_database_owner_only(
    tmp_path: Path,
) -> None:
    await _git_project(tmp_path)

    assert (await _cli(tmp_path, "init", "--yes"))[0] == 0
    assert (await _cli(tmp_path, "submit", "private task"))[0] == 0

    state_dir = tmp_path / ".shikiru"
    assert stat.S_IMODE(state_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE((state_dir / "state.db").stat().st_mode) & 0o077 == 0
