from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shlex
import shutil
import sys
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from textual.widgets import ListView
from typer.testing import CliRunner

from shikiru.cli.app import app
from shikiru.dashboard.session_picker import SessionPicker
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import EscalationReason, Status, Step
from shikiru.engine.daemon import EngineLock, engine_is_running
from shikiru.errors import CommandFailed, DatabaseError, UsageError
from shikiru.operator.sessions import SessionAttacher, SessionLister, SessionRow
from shikiru.paths import engine_lock_path
from shikiru.process import SubprocessRunner
from shikiru.store.database import Database
from shikiru.store.reader import StateReader
from shikiru.store.tasks import TaskStore
from shikiru.tmux.client import Tmux
from shikiru.tmux.session import PROJECT_DIR_ENV_VAR, SESSION_PREFIX
from tests.support.clock import FakeClock
from tests.support.store import seed_task

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from tests.support.tmux import TmuxServer

START = datetime(2026, 1, 1, tzinfo=UTC)
SSH_TIMEOUT_SECONDS = 30
JSON_ROWS_ENV = "SHIKIRU_TEST_SESSION_ROWS"
TMUX_SOCKET_ENV = "SHIKIRU_TEST_TMUX_SOCKET"
REAL_TMUX_ENV = "SHIKIRU_TEST_REAL_TMUX"
CAPTURE_ENV = "SHIKIRU_TEST_CAPTURE"


def _make_project(root: Path, statuses: tuple[Status, ...]) -> None:
    state_dir = root / ".shikiru"
    state_dir.mkdir(parents=True)
    connection = Database(state_dir / "state.db").connect()
    clock = FakeClock(START)
    try:
        tasks = TaskStore(connection, clock)
        for index, status in enumerate(statuses):
            task = seed_task(connection, clock, title=f"task {index}")
            if status is Status.ESCALATED:
                transition = Transition.escalate_current(task, EscalationReason.AGENT_ERROR)
            elif status is Status.QUEUED:
                continue
            else:
                transition = Transition.stay(task, step=Step.IMPLEMENT, status=status)
            tasks.apply_transition(task.id, transition)
    finally:
        connection.close()


async def _add_session(server: TmuxServer, name: str, project_dir: Path) -> None:
    await server.run(
        "new-session",
        "-d",
        "-s",
        name,
        "-e",
        f"{PROJECT_DIR_ENV_VAR}={project_dir}",
    )


def _lister(server: TmuxServer) -> SessionLister:
    clock = FakeClock(START)

    def read_project(root: Path) -> StateReader:
        return StateReader(Database(root / ".shikiru" / "state.db"), clock)

    return SessionLister(
        tmux=Tmux(server.runner, socket=server.socket_name, env=server.env),
        runner=server.runner,
        engine_up=lambda root: engine_is_running(engine_lock_path(root)),
        reader_factory=read_project,
        remote_timeout_seconds=SSH_TIMEOUT_SECONDS,
    )


def _write_executable(directory: Path, name: str, source: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    executable = directory / name
    executable.write_text(f"#!{sys.executable}\n{source}")
    executable.chmod(0o755)
    return executable


def _fake_tmux(directory: Path) -> None:
    _write_executable(
        directory,
        "tmux",
        """
import json
import os
import sys
from pathlib import Path

arguments = sys.argv[1:]
if arguments[0] in {"attach", "switch-client"}:
    Path(os.environ["SHIKIRU_TEST_CAPTURE"]).write_text(json.dumps(arguments))
    raise SystemExit(0)
real_tmux = os.environ["SHIKIRU_TEST_REAL_TMUX"]
os.execv(real_tmux, [real_tmux, "-L", os.environ["SHIKIRU_TEST_TMUX_SOCKET"], *arguments])
""",
    )


def _test_env(directory: Path, capture: Path, server: TmuxServer) -> dict[str, str]:
    real_tmux = shutil.which("tmux")
    if real_tmux is None:
        message = "tmux is required for this integration test"
        raise RuntimeError(message)
    path = os.environ.get("PATH", "")
    return {
        **os.environ,
        "PATH": f"{directory}{os.pathsep}{path}",
        REAL_TMUX_ENV: real_tmux,
        TMUX_SOCKET_ENV: server.socket_name,
        CAPTURE_ENV: str(capture),
        "COLUMNS": "180",
        "NO_COLOR": "1",
    }


@pytest.mark.asyncio
async def test_lister_reads_two_projects_progress_and_liveness_without_mutations(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    first = tmp_path / "first project"
    second = tmp_path / "second project"
    _make_project(
        first,
        (Status.RUNNING, Status.INTEGRATING, Status.QUEUED, Status.ESCALATED),
    )
    _make_project(second, (Status.QUEUED,))
    first_session = f"{SESSION_PREFIX}first project"
    second_session = f"{SESSION_PREFIX}second project"
    await _add_session(tmux_server, first_session, first)
    await _add_session(tmux_server, second_session, second)

    first_state = first / ".shikiru"
    second_state = second / ".shikiru"
    first_lock = EngineLock(first_state / "engine.lock")
    first_lock.acquire()
    active_writer = Database(first_state / "state.db").connect()
    committed_task = seed_task(active_writer, FakeClock(START), title="committed WAL task")
    assert committed_task.status is Status.QUEUED
    wal_path = first_state / "state.db-wal"
    assert wal_path.is_file()
    assert wal_path.stat().st_size > 0

    def snapshot() -> dict[Path, dict[str, tuple[int, str, int]]]:
        return {
            state: {
                path.name: (
                    path.stat().st_size,
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                    path.stat().st_mtime_ns,
                )
                for path in state.iterdir()
                if path.is_file() and path.name != "state.db-shm"
            }
            for state in (first_state, second_state)
        }

    snapshots = snapshot()
    try:
        rows = await _lister(tmux_server).list_sessions()
        after_listing = snapshot()
    finally:
        active_writer.close()
        first_lock.release()

    by_session = {row.session: row for row in rows}
    assert set(by_session) == {first_session, second_session}
    assert by_session[first_session].project_dir == str(first)
    assert by_session[first_session].engine_state == "up"
    assert (
        by_session[first_session].running,
        by_session[first_session].queued,
        by_session[first_session].escalated,
    ) == (2, 2, 1)
    assert by_session[second_session].engine_state == "down"
    assert (by_session[second_session].running, by_session[second_session].queued) == (0, 1)
    changes: dict[str, dict[str, object]] = {}
    for state, before_files in snapshots.items():
        after_files = after_listing[state]
        for name in before_files.keys() | after_files.keys():
            before = before_files.get(name)
            after = after_files.get(name)
            if before == after or name == "state.db-shm":
                continue
            if (
                name == "state.db-wal"
                and before is None
                and after is not None
                and after[0] == 0
                and after[1] == hashlib.sha256(b"").hexdigest()
            ):
                continue
            changes.setdefault(state.parent.name, {})[name] = (before, after)
    assert not changes, changes


@pytest.mark.asyncio
async def test_lister_treats_an_absent_tmux_server_as_empty(tmp_path: Path) -> None:
    tmux = Tmux(SubprocessRunner(), socket=f"shikiru-missing-{tmp_path.name}")

    assert await tmux.list_sessions() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    [
        "no server running on /tmp/tmux-test/default\n",
        "error connecting to /tmp/tmux-test/default (No such file or directory)\n",
    ],
)
async def test_tmux_session_listing_treats_both_missing_socket_errors_as_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    message: str,
) -> None:
    bin_dir = tmp_path / "bin"
    escaped_message = repr(message)
    _write_executable(
        bin_dir,
        "tmux",
        f"import sys\nprint({escaped_message}, end='', file=sys.stderr)\nraise SystemExit(1)\n",
    )
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    assert await Tmux(SubprocessRunner()).list_sessions() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("corrupt", [False, True])
async def test_lister_surfaces_unavailable_project_database(
    tmp_path: Path, tmux_server: TmuxServer, corrupt: bool
) -> None:
    project = tmp_path / "unavailable"
    state_dir = project / ".shikiru"
    state_dir.mkdir(parents=True)
    if corrupt:
        (state_dir / "state.db").touch()
    await _add_session(tmux_server, f"{SESSION_PREFIX}unavailable", project)

    with pytest.raises(DatabaseError):
        await _lister(tmux_server).list_sessions()


@pytest.mark.asyncio
async def test_tmux_session_listing_does_not_hide_non_socket_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = tmp_path / "bin"
    _write_executable(
        bin_dir,
        "tmux",
        "import sys\nprint('permission denied', file=sys.stderr)\nraise SystemExit(1)\n",
    )
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    with pytest.raises(CommandFailed):
        await Tmux(SubprocessRunner()).list_sessions()


@pytest.mark.asyncio
async def test_cli_local_and_remote_ls_work_without_an_initialized_current_project(
    tmp_path: Path,
    tmux_server: TmuxServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project with spaces"
    _make_project(project, (Status.RUNNING, Status.QUEUED))
    engine_lock = EngineLock(engine_lock_path(project))
    engine_lock.acquire()
    session = f"{SESSION_PREFIX}project with spaces"
    await _add_session(tmux_server, session, project)
    uninitialized = tmp_path / "outside project"
    uninitialized.mkdir()
    monkeypatch.chdir(uninitialized)
    bin_dir = tmp_path / "bin"
    _fake_tmux(bin_dir)
    capture = tmp_path / "ssh-list-argv.json"
    remote_row = SessionRow(
        session=f"{SESSION_PREFIX}remote",
        project_dir="/remote project",
        engine_state="up",
        running=3,
        queued=2,
        escalated=1,
    )
    _write_executable(
        bin_dir,
        "ssh",
        """
import json
import os
import sys
from pathlib import Path

Path(os.environ["SHIKIRU_TEST_CAPTURE"]).write_text(json.dumps(sys.argv[1:]))
print(os.environ["SHIKIRU_TEST_SESSION_ROWS"])
""",
    )
    env = _test_env(bin_dir, capture, tmux_server)
    env[JSON_ROWS_ENV] = json.dumps([remote_row.model_dump(mode="json")])
    runner = CliRunner()

    try:
        local_result = await asyncio.to_thread(runner.invoke, app, ["ls", "--json"], env=env)
        human_result = await asyncio.to_thread(runner.invoke, app, ["ls"], env=env)
        remote_result = await asyncio.to_thread(
            runner.invoke,
            app,
            ["ls", "--host", "remote.example", "--json"],
            env=env,
        )
    finally:
        engine_lock.release()

    local_rows = json.loads(local_result.stdout)
    assert local_result.exit_code == 0
    assert local_rows[0]["session"] == session
    assert local_rows[0]["project_dir"] == str(project)
    assert local_rows[0]["engine_state"] == "up"
    assert local_rows[0]["running"] == 1
    assert local_rows[0]["queued"] == 1
    assert human_result.exit_code == 0
    assert session in human_result.stdout
    assert f"/{project.name}" in human_result.stdout
    assert remote_result.exit_code == 0
    assert json.loads(remote_result.stdout) == [remote_row.model_dump(mode="json")]
    assert json.loads(capture.read_text()) == [
        "--",
        "remote.example",
        "shikiru",
        "ls",
        "--json",
    ]
    capture_before_invalid_host = capture.read_text()
    invalid_timeout_result = await asyncio.to_thread(
        runner.invoke,
        app,
        [
            "--set",
            "sessions.ssh_timeout_seconds=0",
            "ls",
            "--host",
            "remote.example",
            "--json",
        ],
        env=env,
    )
    assert invalid_timeout_result.exit_code == 1
    assert "ssh_timeout_seconds" in invalid_timeout_result.stderr
    assert capture.read_text() == capture_before_invalid_host
    invalid_host_result = await asyncio.to_thread(
        runner.invoke,
        app,
        ["ls", "--host=-oProxyCommand=touch /tmp/not-run", "--json"],
        env=env,
    )
    assert invalid_host_result.exit_code == 2
    assert "invalid SSH host" in invalid_host_result.stderr
    assert capture.read_text() == capture_before_invalid_host


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("inside_tmux", "expected_operation"),
    [(False, "attach"), (True, "switch-client")],
)
async def test_cli_attach_uses_a_listed_exact_target_and_tmux_context(
    tmp_path: Path,
    tmux_server: TmuxServer,
    inside_tmux: bool,
    expected_operation: str,
) -> None:
    project = tmp_path / "project dir"
    _make_project(project, ())
    session = f"{SESSION_PREFIX}quoted ' project"
    await _add_session(tmux_server, session, project)
    bin_dir = tmp_path / "bin"
    _fake_tmux(bin_dir)
    capture = tmp_path / "tmux-argv.json"
    env = _test_env(bin_dir, capture, tmux_server)
    if inside_tmux:
        env["TMUX"] = "private-session"
    else:
        env.pop("TMUX", None)
    result = await SubprocessRunner().run(
        [sys.executable, "-m", "shikiru", "attach", session.removeprefix(SESSION_PREFIX)],
        timeout=10,
        env=env,
        cwd=tmp_path,
    )

    assert result.returncode == 0
    assert json.loads(capture.read_text()) == [expected_operation, "-t", f"={session}"]


@pytest.mark.asyncio
async def test_attach_picker_selection_and_remote_ssh_quoting_use_process_boundary(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    capture = tmp_path / "remote-argv.json"
    _write_executable(
        bin_dir,
        "ssh",
        """
import json
import os
import sys
from pathlib import Path

Path(os.environ["SHIKIRU_TEST_CAPTURE"]).write_text(json.dumps(sys.argv[1:]))
""",
    )
    selected = SessionRow(
        session=f"{SESSION_PREFIX}O'Brien / spaced project",
        project_dir="/projects/O'Brien / spaced project",
        engine_state="up",
        running=1,
        queued=2,
        escalated=0,
    )
    first_session = f"{SESSION_PREFIX}first"
    child = """
import asyncio
import json
import os
from shikiru.operator.sessions import SessionAttacher, SessionRow
from shikiru.process import SubprocessRunner

rows = [
    SessionRow.model_validate(row)
    for row in json.loads(os.environ["SHIKIRU_TEST_SESSION_ROWS"])
]

async def pick(options):
    return options[1]

attacher = SessionAttacher(SubprocessRunner(), pick)

async def attach():
    await attacher.attach(
        rows,
        name=None,
        host="remote.example",
        inside_tmux=True,
    )

asyncio.run(attach())
"""
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        CAPTURE_ENV: str(capture),
        JSON_ROWS_ENV: json.dumps(
            [
                {
                    "session": first_session,
                    "project_dir": "/projects/first",
                    "engine_state": "down",
                    "running": 0,
                    "queued": 0,
                    "escalated": 0,
                },
                selected.model_dump(mode="json"),
            ]
        ),
    }
    result = await SubprocessRunner().run(
        [sys.executable, "-c", child],
        timeout=10,
        env=env,
    )

    assert result.returncode == 0
    argv = json.loads(capture.read_text())
    assert argv[:3] == ["-t", "--", "remote.example"]
    assert len(argv) == 4
    assert shlex.split(argv[3]) == ["tmux", "attach", "-t", f"={selected.session}"]


@pytest.mark.asyncio
async def test_attach_rejects_an_option_like_name_without_using_it_as_a_target() -> None:
    row = SessionRow(
        session=f"{SESSION_PREFIX}valid",
        project_dir="/projects/valid",
        engine_state="down",
        running=0,
        queued=0,
        escalated=0,
    )

    async def pick(rows: Sequence[SessionRow]) -> SessionRow:
        return rows[0]

    attacher = SessionAttacher(SubprocessRunner(), pick)
    with pytest.raises(UsageError):
        await attacher.attach(
            [row],
            name="-t",
            host=None,
            inside_tmux=False,
        )


@pytest.mark.asyncio
async def test_session_picker_can_scroll_to_and_select_rows_beyond_small_viewport() -> None:
    rows = tuple(
        SessionRow(
            session=f"{SESSION_PREFIX}project-{index}",
            project_dir=f"/projects/project-{index}",
            engine_state="up",
            running=index,
            queued=0,
            escalated=0,
        )
        for index in range(5)
    )
    app_instance = SessionPicker(rows)

    async with app_instance.run_test(size=(72, 12)) as pilot:
        picker = app_instance.query_one("#session-picker-list", ListView)
        picker.focus()
        for _ in rows[1:]:
            await pilot.press("down")
        assert picker.index == len(rows) - 1
        assert picker.scroll_y > 0
        await pilot.press("enter")

    assert app_instance.return_value == rows[-1]


@pytest.mark.asyncio
async def test_session_picker_escape_returns_without_a_selection() -> None:
    row = SessionRow(
        session=f"{SESSION_PREFIX}project",
        project_dir="/projects/project",
        engine_state="down",
        running=0,
        queued=0,
        escalated=0,
    )
    app_instance = SessionPicker((row,))

    async with app_instance.run_test(size=(72, 12)) as pilot:
        await pilot.press("escape")

    assert app_instance.return_value is None
