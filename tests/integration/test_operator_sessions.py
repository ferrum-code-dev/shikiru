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
from typer.testing import CliRunner

from shikiru.cli.app import app
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import EscalationReason, Status, Step
from shikiru.engine.daemon import EngineLock, engine_is_running
from shikiru.errors import (
    EXIT_NOT_INITIALISED,
    EXIT_USAGE_ERROR,
    CommandFailed,
    DatabaseError,
    UsageError,
)
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
from tests.support.waiting import UI_WAIT_SECONDS, wait_until

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


async def _run_attach_cli(
    arguments: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    input_bytes: bytes | None = None,
    interrupt: bool = False,
) -> tuple[str, int]:
    process = await SubprocessRunner().spawn_terminal(
        [sys.executable, "-m", "shikiru", *arguments],
        cwd=cwd,
        env=env,
    )
    try:
        if input_bytes is not None or interrupt:
            await wait_until(
                lambda: b"Select a session number" in process.output,
                timeout=UI_WAIT_SECONDS,
            )
            if interrupt:
                process.send_interrupt()
            else:
                assert input_bytes is not None
                process.write_input(input_bytes)
        await wait_until(lambda: process.returncode is not None, timeout=UI_WAIT_SECONDS)
        assert process.returncode is not None
        return process.output.decode(errors="replace"), process.returncode
    finally:
        await process.stop()


def _assert_session_data(
    output: str, session: str, project_dir: str, counts: tuple[int, ...]
) -> None:
    compact_output = "".join(output.split())
    assert "".join(session.split()) in compact_output
    assert "".join(project_dir.split()) in compact_output
    assert "up" in output
    assert all(str(count) in compact_output for count in counts)


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
async def test_cli_lists_an_absent_tmux_server_as_empty(tmp_path: Path) -> None:
    socket = f"shikiru-missing-{tmp_path.name}"
    tmux = Tmux(SubprocessRunner(), socket=socket)

    assert await tmux.list_sessions() == []

    bin_dir = tmp_path / "bin"
    _fake_tmux(bin_dir)
    real_tmux = shutil.which("tmux")
    assert real_tmux is not None
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        REAL_TMUX_ENV: real_tmux,
        TMUX_SOCKET_ENV: socket,
        "COLUMNS": "80",
        "NO_COLOR": "1",
    }
    runner = SubprocessRunner()
    human = await runner.run(
        [sys.executable, "-m", "shikiru", "ls"],
        timeout=10,
        cwd=tmp_path,
        env=env,
        check=False,
    )
    structured = await runner.run(
        [sys.executable, "-m", "shikiru", "--json", "ls"],
        timeout=10,
        cwd=tmp_path,
        env=env,
        check=False,
    )

    assert human.returncode == 0
    assert human.stderr == ""
    assert "No Shikiru sessions found." in human.stdout
    assert structured.returncode == 0
    assert structured.stderr == ""
    assert json.loads(structured.stdout) == []


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
        session=f"{SESSION_PREFIX}remote [literal] " + "session-segment-" * 5,
        project_dir="/remote project/[literal]/" + "path-segment-" * 7,
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
        remote_result = await asyncio.to_thread(
            runner.invoke,
            app,
            ["ls", "--host", "remote.example", "--json"],
            env=env,
        )
        remote_argv = capture.read_text()
        listing_cases = (
            (None, session, str(project), (1, 1, 0)),
            (
                "remote.example",
                remote_row.session,
                remote_row.project_dir,
                (remote_row.running, remote_row.queued, remote_row.escalated),
            ),
        )
        for width in (80, 60, 40):
            for host, identity, project_dir, counts in listing_cases:
                args = ["ls"] if host is None else ["ls", "--host", host]
                result = await asyncio.to_thread(
                    runner.invoke,
                    app,
                    args,
                    env={**env, "COLUMNS": str(width)},
                )
                assert result.exit_code == 0
                _assert_session_data(result.stdout, identity, project_dir, counts)
                if host is not None:
                    assert result.stdout.count(host) == 1
    finally:
        engine_lock.release()

    local_rows = json.loads(local_result.stdout)

    assert local_result.exit_code == 0
    assert all(
        local_rows[0][field] == value
        for field, value in (
            ("session", session),
            ("project_dir", str(project)),
            ("engine_state", "up"),
            ("running", 1),
            ("queued", 1),
        )
    )
    assert remote_result.exit_code == 0
    assert json.loads(remote_result.stdout) == [remote_row.model_dump(mode="json")]
    assert json.loads(remote_argv) == ["--", "remote.example", "shikiru", "ls", "--json"]
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
async def test_unnamed_attach_uses_nearest_or_explicit_project_before_reading_other_databases(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    project = tmp_path / "current project"
    project_config = project / ".shikiru" / "config.toml"
    _make_project(project, ())
    project_config.write_text('[project]\nname = "current"\n')
    (project / "packages" / ".shikiru").mkdir(parents=True)
    nested_directory = project / "packages" / "nested"
    nested_directory.mkdir(parents=True)
    broken_project = tmp_path / "broken foreign project"
    broken_state = broken_project / ".shikiru"
    broken_state.mkdir(parents=True)
    (broken_state / "state.db").write_text("not a SQLite database")
    session = f"{SESSION_PREFIX}current project"
    await _add_session(tmux_server, session, project)
    await _add_session(tmux_server, f"{SESSION_PREFIX}foreign", broken_project)
    bin_dir = tmp_path / "bin"
    _fake_tmux(bin_dir)
    capture = tmp_path / "tmux-argv.json"
    env = _test_env(bin_dir, capture, tmux_server)
    env.pop("TMUX", None)

    for arguments, cwd in (
        (["attach"], nested_directory),
        (["--project", str(project), "attach"], tmp_path),
    ):
        _, returncode = await _run_attach_cli(arguments, cwd=cwd, env=env)

        assert returncode == 0
        assert json.loads(capture.read_text()) == ["attach", "-t", f"={session}"]


@pytest.mark.asyncio
async def test_current_project_without_a_session_does_not_attach_an_unrelated_singleton(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    project = tmp_path / "current project"
    _make_project(project, ())
    (project / ".shikiru" / "config.toml").write_text('[project]\nname = "current"\n')
    unrelated = tmp_path / "unrelated project"
    _make_project(unrelated, ())
    await _add_session(tmux_server, f"{SESSION_PREFIX}unrelated", unrelated)
    bin_dir = tmp_path / "bin"
    _fake_tmux(bin_dir)
    capture = tmp_path / "tmux-argv.json"
    env = _test_env(bin_dir, capture, tmux_server)
    env.pop("TMUX", None)

    output, returncode = await _run_attach_cli(["attach"], cwd=project, env=env)

    assert returncode == EXIT_USAGE_ERROR
    assert "start --attach" in output
    assert not capture.exists()


@pytest.mark.asyncio
async def test_explicit_project_without_config_rejects_attach_without_selecting_a_session(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    incomplete_project = tmp_path / "incomplete project"
    unrelated_project = tmp_path / "unrelated project"
    _make_project(incomplete_project, ())
    _make_project(unrelated_project, ())
    await _add_session(tmux_server, f"{SESSION_PREFIX}unrelated", unrelated_project)
    bin_dir = tmp_path / "bin"
    _fake_tmux(bin_dir)
    capture = tmp_path / "tmux-argv.json"
    env = _test_env(bin_dir, capture, tmux_server)
    env.pop("TMUX", None)

    _, returncode = await _run_attach_cli(
        ["--project", str(incomplete_project), "attach"], cwd=tmp_path, env=env
    )

    assert returncode == EXIT_NOT_INITIALISED
    assert not capture.exists()


@pytest.mark.asyncio
async def test_explicit_attach_name_remains_authoritative_with_project_option(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    current_project = tmp_path / "current project"
    target_project = tmp_path / "named project"
    _make_project(current_project, ())
    _make_project(target_project, ())
    (current_project / ".shikiru" / "config.toml").write_text('[project]\nname = "current"\n')
    named_session = f"{SESSION_PREFIX}named project"
    await _add_session(tmux_server, f"{SESSION_PREFIX}current", current_project)
    await _add_session(tmux_server, named_session, target_project)
    bin_dir = tmp_path / "bin"
    _fake_tmux(bin_dir)
    capture = tmp_path / "tmux-argv.json"
    env = _test_env(bin_dir, capture, tmux_server)
    env.pop("TMUX", None)

    _, returncode = await _run_attach_cli(
        ["--project", str(current_project), "attach", "named project"],
        cwd=tmp_path,
        env=env,
    )

    assert returncode == 0
    assert json.loads(capture.read_text()) == ["attach", "-t", f"={named_session}"]


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_trust_file", [False, True])
async def test_ambiguous_attach_retries_invalid_numbers_and_uses_the_selected_exact_row(
    tmp_path: Path, tmux_server: TmuxServer, legacy_trust_file: bool
) -> None:
    first_project = tmp_path / "first project"
    second_project = tmp_path / "O'Brien [literal] project"
    outside_directory = tmp_path / "outside" / "nested"
    _make_project(first_project, ())
    _make_project(second_project, ())
    outside_directory.mkdir(parents=True)
    first_session = f"{SESSION_PREFIX}first project"
    second_session = f"{SESSION_PREFIX}O'Brien [literal] project"
    await _add_session(tmux_server, first_session, first_project)
    await _add_session(tmux_server, second_session, second_project)
    if legacy_trust_file:
        legacy_state = tmp_path / ".shikiru"
        legacy_state.mkdir()
        (legacy_state / "trusted.json").write_text("{}")
    expected_row = (await _lister(tmux_server).list_sessions())[1]
    bin_dir = tmp_path / "bin"
    _fake_tmux(bin_dir)
    capture = tmp_path / "tmux-argv.json"
    env = _test_env(bin_dir, capture, tmux_server)
    env.pop("TMUX", None)

    output, returncode = await _run_attach_cli(
        ["attach"],
        cwd=outside_directory,
        env=env,
        input_bytes=b"invalid\n0\n2\n",
    )

    assert returncode == 0
    assert second_session in output
    assert str(second_project) in output
    assert json.loads(capture.read_text()) == [
        "attach",
        "-t",
        f"={expected_row.session}",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("input_bytes", "interrupt"), [(b"q\n", False), (b"\x04", False), (None, True)]
)
async def test_ambiguous_attach_can_be_cancelled_by_quit_eof_or_interrupt(
    tmp_path: Path,
    tmux_server: TmuxServer,
    input_bytes: bytes | None,
    interrupt: bool,
) -> None:
    first_project = tmp_path / "first project"
    second_project = tmp_path / "second project"
    _make_project(first_project, ())
    _make_project(second_project, ())
    await _add_session(tmux_server, f"{SESSION_PREFIX}first", first_project)
    await _add_session(tmux_server, f"{SESSION_PREFIX}second", second_project)
    bin_dir = tmp_path / "bin"
    _fake_tmux(bin_dir)
    capture = tmp_path / "tmux-argv.json"
    env = _test_env(bin_dir, capture, tmux_server)
    env.pop("TMUX", None)

    output, returncode = await _run_attach_cli(
        ["attach"], cwd=tmp_path, env=env, input_bytes=input_bytes, interrupt=interrupt
    )

    assert returncode == EXIT_USAGE_ERROR, output
    assert not capture.exists()


@pytest.mark.asyncio
async def test_remote_attach_selection_and_ssh_quoting_use_process_boundary(
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

def pick(options, host):
    assert host == "remote.example"
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

    def prompt(rows: Sequence[SessionRow], host: str | None) -> SessionRow:
        assert host is None
        return rows[0]

    attacher = SessionAttacher(SubprocessRunner(), prompt)
    with pytest.raises(UsageError):
        await attacher.attach(
            [row],
            name="-t",
            host=None,
            inside_tmux=False,
        )
