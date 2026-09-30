"""Engine restart recovery (03 §7): an engine killed at a step boundary resumes on restart."""

from __future__ import annotations

import asyncio
import fcntl
import gc
import os
import signal
import threading
import warnings
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import psutil
import pytest
from fake_agent.scripted import FAKE_CHANGE_FILENAME

from shikiru.agents.contracts import role_dir
from shikiru.bootstrap import build_engine
from shikiru.config.settings import AgentConfig, LayoutSplit, PaneLeaf, ProjectConfig
from shikiru.domain.commands import Cancel, Pause, Prioritize, Resume, Submit
from shikiru.domain.events import EventKind
from shikiru.domain.reports import CURRENT_TURN_LINK_NAME, REPORT_FILENAME
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import Hold, Role, Status, Step
from shikiru.engine.integration import INTEGRATION_RETRY
from shikiru.engine.turn_processes import TURN_PID_NAME, read_turn_pid
from shikiru.paths import (
    engine_lock_path,
    project_state_dir,
    run_dir,
    state_db_path,
    task_root,
    turns_dir,
)
from shikiru.process import SubprocessRunner
from shikiru.store.commands import CommandQueue, CommandStatus
from shikiru.store.database import Database
from shikiru.store.events import EventLog
from shikiru.store.quotas import fetch_quotas
from shikiru.store.runs import RunStore
from shikiru.store.tasks import TaskStore
from shikiru.tmux.session import SessionView
from shikiru.workspace.messages import OperatorMessages
from tests.support.clock import FakeClock
from tests.support.engine import FAKE_RUNG, build_registry, build_tmux, set_role_script
from tests.support.factories import CPU_UNCAPPED_PCT, TASK_CREATED_AT, make_settings
from tests.support.flow import init_project_repo
from tests.support.git_repo import commit_all
from tests.support.store import seed_task
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path

    from shikiru.config.settings import Settings
    from shikiru.engine.daemon import Engine
    from shikiru.process import ProcessIdentity
    from shikiru.tmux.client import Tmux
    from tests.support.tmux import TmuxServer

_PROJECT_NAME = "test"
_POLL_ADVANCE_SECONDS = 0.25
_WAIT_SECONDS = 30
_REQUEST = "restart me"
_STALL = {
    "kind": "stall",
    "quota_windows": [
        {"name": "stalled", "used_pct": 1, "observed_at": TASK_CREATED_AT.isoformat()}
    ],
}
_REPORT_THEN_STALL = {"kind": "accept", "stall_after_report": True}
_ACCEPT = {"kind": "accept"}
_NEEDS_DECISION = {"kind": "needs_decision"}
_CONFLICT_COMMITS = 2
_RUNNER = SubprocessRunner()
_POLL_SECONDS = 3600
_UNKNOWN_TASK_ID = "T-999"
_TERMINAL_EVENTS = {Status.DONE: EventKind.TASK_DONE, Status.CANCELLED: EventKind.TASK_CANCELLED}


@dataclass(frozen=True)
class _Kill:
    step: Step
    role: Role | None
    reported: bool = False
    terminal: Status | None = None


_REBASE_SETUP_KILL = _Kill(Step.IMPLEMENT, Role.IMPLEMENTER, reported=True)


@dataclass(frozen=True)
class _Boundary:
    kills: tuple[_Kill, ...]
    scripts: dict[Role, list[dict[str, Any]]] = field(default_factory=dict[Role, Any])
    step_turns: int = 0
    commits: int = 1
    blocked_merge: bool = False
    conflicting_base: bool = False
    status: Status = Status.DONE
    repo_less_leftover: bool = False


_BOUNDARIES = {
    "triage turn running": _Boundary(
        (_Kill(Step.TRIAGE, Role.LEADER),), {Role.LEADER: [_STALL]}, step_turns=2
    ),
    "triage report submitted": _Boundary(
        (_Kill(Step.TRIAGE, Role.LEADER, reported=True),),
        {Role.LEADER: [_REPORT_THEN_STALL]},
        step_turns=1,
    ),
    "implement turn running": _Boundary(
        (_Kill(Step.IMPLEMENT, Role.IMPLEMENTER),), {Role.IMPLEMENTER: [_STALL]}, step_turns=2
    ),
    "implement report submitted": _Boundary(
        (_Kill(Step.IMPLEMENT, Role.IMPLEMENTER, reported=True),),
        {Role.IMPLEMENTER: [_REPORT_THEN_STALL]},
        step_turns=1,
    ),
    "verify turn running": _Boundary(
        (_Kill(Step.VERIFY, Role.LEADER),), {Role.LEADER: [_ACCEPT, _STALL]}, step_turns=2
    ),
    "verify report submitted": _Boundary(
        (_Kill(Step.VERIFY, Role.LEADER, reported=True),),
        {Role.LEADER: [_ACCEPT, _REPORT_THEN_STALL]},
        step_turns=1,
    ),
    "integration waiting": _Boundary((_Kill(Step.INTEGRATE, None),), blocked_merge=True),
    "done before cleanup": _Boundary(
        (_Kill(Step.INTEGRATE, None, terminal=Status.DONE),), blocked_merge=True
    ),
    "cancelled before cleanup": _Boundary(
        (_Kill(Step.IMPLEMENT, Role.IMPLEMENTER, terminal=Status.CANCELLED),),
        {Role.IMPLEMENTER: [_STALL]},
        step_turns=1,
        commits=0,
        status=Status.CANCELLED,
        repo_less_leftover=True,
    ),
    "conflicts turn running": _Boundary(
        (_REBASE_SETUP_KILL, _Kill(Step.INTEGRATE, Role.IMPLEMENTER)),
        {Role.IMPLEMENTER: [_REPORT_THEN_STALL, _STALL]},
        step_turns=3,
        commits=_CONFLICT_COMMITS,
        conflicting_base=True,
    ),
    "conflicts report submitted": _Boundary(
        (_REBASE_SETUP_KILL, _Kill(Step.INTEGRATE, Role.IMPLEMENTER, reported=True)),
        {Role.IMPLEMENTER: [_REPORT_THEN_STALL, _REPORT_THEN_STALL]},
        step_turns=2,
        commits=_CONFLICT_COMMITS,
        conflicting_base=True,
    ),
    "post-rebase verify running": _Boundary(
        (_REBASE_SETUP_KILL, _Kill(Step.INTEGRATE, Role.LEADER)),
        {Role.IMPLEMENTER: [_REPORT_THEN_STALL], Role.LEADER: [_ACCEPT, _ACCEPT, _STALL]},
        step_turns=3,
        commits=_CONFLICT_COMMITS,
        conflicting_base=True,
    ),
    "integration consult running": _Boundary(
        (_REBASE_SETUP_KILL, _Kill(Step.CONSULT, Role.LEADER)),
        {
            Role.IMPLEMENTER: [_REPORT_THEN_STALL, _NEEDS_DECISION],
            Role.LEADER: [_ACCEPT, _ACCEPT, _STALL],
        },
        step_turns=2,
        commits=_CONFLICT_COMMITS,
        conflicting_base=True,
    ),
}


class _KillableEngine:
    """An in-process engine on its own event loop thread, which can die without any cleanup."""

    def __init__(self, engine: Engine, clock: FakeClock) -> None:
        self._engine: Engine | None = engine
        self._clock = clock
        self._loop = asyncio.new_event_loop()
        self._reap = threading.Event()
        self._thread = threading.Thread(target=self._serve)

    def start(self) -> None:
        self._thread.start()

    def advance(self, seconds: float = _POLL_ADVANCE_SECONDS) -> None:
        self._loop.call_soon_threadsafe(self._clock.advance, seconds)

    async def kill(self, lock_path: Path) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)
        await wait_until(lambda: not self._loop.is_running(), timeout=_WAIT_SECONDS)
        _release_lock_held_by_dead_engine(lock_path)

    async def reap(self) -> None:
        if self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        await wait_until(lambda: not self._loop.is_running(), timeout=_WAIT_SECONDS)
        self._reap.set()
        await asyncio.to_thread(self._thread.join)

    def _serve(self) -> None:
        asyncio.set_event_loop(self._loop)
        assert self._engine is not None
        self._loop.create_task(self._engine.run())
        self._loop.run_forever()
        self._reap.wait()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ResourceWarning)
            self._reclaim_what_the_dead_engine_left_open()

    def _reclaim_what_the_dead_engine_left_open(self) -> None:
        pending = asyncio.all_tasks(self._loop)
        for task in pending:
            task.cancel()
        self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        self._loop.close()
        self._engine = None
        gc.collect()


def _release_lock_held_by_dead_engine(lock_path: Path) -> None:
    for opened in psutil.Process().open_files():
        if lock_path.samefile(opened.path):
            fcntl.flock(opened.fd, fcntl.LOCK_UN)


def _settings() -> Settings:
    settings = make_settings(
        leader_ladder=[FAKE_RUNG],
        implementer_ladder=[FAKE_RUNG],
        helper_ladder=[FAKE_RUNG],
        agents={FAKE_RUNG.agent: AgentConfig()},
    )
    orchestration = settings.orchestration.model_copy(
        update={
            "poll_seconds": _POLL_SECONDS,
            "resources": settings.orchestration.resources.model_copy(
                update={"max_cpu_pct": CPU_UNCAPPED_PCT, "min_free_ram_gb": 0}
            ),
        }
    )
    layout = LayoutSplit(
        split="h",
        panes=[
            PaneLeaf(pane="leader"),
            PaneLeaf(pane="implementer"),
            PaneLeaf(pane="system"),
            PaneLeaf(pane="shell"),
        ],
    )
    return settings.model_copy(
        update={
            "project": ProjectConfig(name=_PROJECT_NAME, trackers=[]),
            "orchestration": orchestration,
            "dashboard": settings.dashboard.model_copy(update={"layout": layout}),
        }
    )


async def _commit_subjects(project_root: Path, since: str) -> list[str]:
    result = await _RUNNER.run(
        ["git", "log", "--format=%s", f"{since}..HEAD"], timeout=10, cwd=project_root, check=True
    )
    return result.stdout.splitlines()


async def _head(project_root: Path) -> str:
    result = await _RUNNER.run(
        ["git", "rev-parse", "HEAD"], timeout=10, cwd=project_root, check=True
    )
    return result.stdout.strip()


@dataclass(frozen=True)
class _Interrupted:
    orphan: ProcessIdentity | None
    sessions: dict[Role, str | None]


class _Project:
    """The state one restart scenario observes, read over its own SQLite connection."""

    def __init__(self, root: Path, clock: FakeClock) -> None:
        self.root = root
        project_state_dir(root).mkdir(parents=True, exist_ok=True)
        self.connection = Database(state_db_path(root)).connect()
        self.tasks = TaskStore(self.connection, clock)
        self.runs = RunStore(self.connection, clock)
        self.events = EventLog(self.connection, clock)
        self.queue = CommandQueue(self.connection, clock)

    def close(self) -> None:
        self.connection.close()

    def reached(self, task_id: str, boundary: _Kill) -> bool:
        if boundary.role is None:
            return bool(
                self.events.since(0, kinds=[EventKind.INTEGRATION_STARTED], task_id=task_id)
            )
        started = [
            event
            for event in self.events.since(0, kinds=[EventKind.TURN_STARTED], task_id=task_id)
            if event.data["step"] == boundary.step.value
        ]
        role_path = role_dir(run_dir(self.root, task_id), boundary.role.value)
        run = self.runs.get_live(task_id, boundary.role)
        stalled = (
            (role_path / CURRENT_TURN_LINK_NAME / REPORT_FILENAME).is_file()
            if boundary.reported
            else bool(fetch_quotas(self.connection, FAKE_RUNG.agent))
        )
        return (
            bool(started)
            and (role_path / TURN_PID_NAME).is_file()
            and run is not None
            and run.session_id is not None
            and stalled
        )

    def interrupted(self, task_id: str, boundary: _Kill) -> _Interrupted:
        orphan = None
        if boundary.role is not None:
            pid_path = role_dir(run_dir(self.root, task_id), boundary.role.value) / TURN_PID_NAME
            orphan = read_turn_pid(pid_path) if pid_path.is_file() else None
        sessions = {run.role: run.session_id for run in self.runs.for_task(task_id)}
        return _Interrupted(orphan, sessions)


async def _submit(project: _Project, engine: _KillableEngine) -> str:
    queued = project.queue.append(Submit(title=_REQUEST, request=_REQUEST))

    def acknowledged() -> bool:
        engine.advance()
        outcome = project.queue.status(queued.id)
        return outcome is not None and outcome.result is not None

    await wait_until(acknowledged, timeout=_WAIT_SECONDS)
    outcome = project.queue.status(queued.id)
    assert outcome is not None
    assert outcome.result is not None
    return str(outcome.result["task_id"])


async def _run_until_finished(
    project: _Project, task_id: str, status: Status, engine: Engine, clock: FakeClock
) -> None:
    def finished() -> bool:
        if clock.has_pending_sleep():
            clock.advance(_POLL_ADVANCE_SECONDS)
        task = project.tasks.get(task_id)
        return (
            task is not None
            and task.status is status
            and not task_root(project.root, task_id).exists()
        )

    running = asyncio.create_task(engine.run())
    try:
        await wait_until(finished, timeout=_WAIT_SECONDS)
    finally:
        running.cancel()
        with suppress(asyncio.CancelledError):
            await running


async def _assert_resumed(
    project: _Project,
    task_id: str,
    boundary: _Boundary,
    interrupted: list[_Interrupted],
    initial: str,
    *,
    tmux: Tmux,
) -> None:
    terminal_events = project.events.since(
        0, kinds=[_TERMINAL_EVENTS[boundary.status]], task_id=task_id
    )
    assert len(terminal_events) == 1
    task = project.tasks.get(task_id)
    assert task is not None
    assert task.window_id is not None
    assert not await tmux.target_exists(task.window_id)
    for kill in interrupted:
        if kill.orphan is not None:
            assert not _RUNNER.is_running(kill.orphan)
    runs = project.runs.for_task(task_id)
    assert len(runs) == len({run.role for run in runs})
    sessions = {run.role: run.session_id for run in runs}
    for kill in interrupted:
        assert {role: sessions[role] for role in kill.sessions} == kill.sessions
    assert len(await _commit_subjects(project.root, initial)) == boundary.commits
    last_step = boundary.kills[-1].step.value
    step_turns = list(turns_dir(project.root, task_id).glob(f"*-{last_step}-i*"))
    assert len(step_turns) == boundary.step_turns


def _seed_repo_less_leftover(project: _Project, clock: FakeClock) -> str:
    task = seed_task(project.connection, clock, title="never dispatched")
    [pending] = project.queue.pending()
    project.queue.mark_done(pending.id, {"task_id": task.id})
    project.tasks.finish(
        task.id,
        Transition.stay(task, step=None, status=Status.CANCELLED),
        EventKind.TASK_CANCELLED,
        message=f"{task.id} cancelled",
        data={},
    )
    root = task_root(project.root, task.id)
    root.mkdir(parents=True)
    (root / "project").symlink_to(project.root, target_is_directory=True)
    return task.id


async def _restart_once(project: _Project, engines: _Engines, leftover_id: str) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    probe = project.queue.append(Pause(task_id=leftover_id))

    def probed() -> bool:
        if clock.has_pending_sleep():
            clock.advance(_POLL_ADVANCE_SECONDS)
        outcome = project.queue.status(probe.id)
        return outcome is not None and outcome.status is not CommandStatus.PENDING

    running = asyncio.create_task(engines.build(clock).run())
    try:
        await wait_until(probed, timeout=_WAIT_SECONDS)
    finally:
        running.cancel()
        with suppress(asyncio.CancelledError):
            await running


async def _assert_leftover_reclaimed_once(
    project: _Project, engines: _Engines, leftover_id: str
) -> None:
    assert not task_root(project.root, leftover_id).exists()
    assert (project.root / "README.md").is_file()
    before = project.events.since(0, task_id=leftover_id)
    await _restart_once(project, engines, leftover_id)
    assert project.events.since(0, task_id=leftover_id) == before
    assert not task_root(project.root, leftover_id).exists()


async def _while_first_engine_down(project_root: Path, boundary: _Boundary) -> None:
    (project_root / FAKE_CHANGE_FILENAME).unlink(missing_ok=True)
    if boundary.conflicting_base:
        (project_root / FAKE_CHANGE_FILENAME).write_text("base\n")
        await commit_all(_RUNNER, project_root, "conflicting base change")


class _Engines:
    """The engines one scenario starts in turn, each on its own fake clock."""

    def __init__(self, project_root: Path, tmux: Tmux, tmux_server: TmuxServer) -> None:
        self._project_root = project_root
        self._tmux = tmux
        self.tmux_server = tmux_server
        self._killable: list[_KillableEngine] = []

    def build(self, clock: FakeClock) -> Engine:
        return build_engine(
            self._project_root, _settings(), clock=clock, tmux=self._tmux, registry=build_registry()
        )

    def start_killable(self) -> _KillableEngine:
        clock = FakeClock(TASK_CREATED_AT)
        engine = _KillableEngine(self.build(clock), clock)
        self._killable.append(engine)
        engine.start()
        return engine

    async def reap(self) -> None:
        for engine in reversed(self._killable):
            await engine.reap()


async def _kill_at(
    project: _Project, engines: _Engines, kill: _Kill, task_id: str | None
) -> tuple[str, _Interrupted]:
    engine = engines.start_killable()
    resolved_id = task_id if task_id is not None else await _submit(project, engine)

    def reached() -> bool:
        engine.advance()
        return project.reached(resolved_id, kill)

    await wait_until(reached, timeout=_WAIT_SECONDS)
    if kill.terminal is None:
        await engine.kill(engine_lock_path(project.root))
    else:
        await _settle(project, engine)
        async with _frozen(engines.tmux_server):
            await _reach_terminal_status(project, engine, resolved_id, kill.terminal)
            await engine.kill(engine_lock_path(project.root))
    return resolved_id, project.interrupted(resolved_id, kill)


async def _settle(project: _Project, engine: _KillableEngine) -> None:
    probe = project.queue.append(Prioritize(task_id=_UNKNOWN_TASK_ID, priority=0))

    def processed() -> bool:
        engine.advance()
        outcome = project.queue.status(probe.id)
        return outcome is not None and outcome.status is not CommandStatus.PENDING

    await wait_until(processed, timeout=_WAIT_SECONDS)


@asynccontextmanager
async def _frozen(tmux_server: TmuxServer) -> AsyncGenerator[None]:
    result = await tmux_server.run("display-message", "-p", "#{pid}")
    pid = int(result.stdout.strip())
    os.kill(pid, signal.SIGSTOP)
    try:
        yield
    finally:
        os.kill(pid, signal.SIGCONT)


async def _reach_terminal_status(
    project: _Project, engine: _KillableEngine, task_id: str, status: Status
) -> None:
    if status is Status.CANCELLED:
        project.queue.append(Cancel(task_id=task_id))
    (project.root / FAKE_CHANGE_FILENAME).unlink(missing_ok=True)

    step = INTEGRATION_RETRY.total_seconds() if status is Status.DONE else _POLL_ADVANCE_SECONDS

    def terminal() -> bool:
        engine.advance(step)
        task = project.tasks.get(task_id)
        return task is not None and task.status is status

    await wait_until(terminal, timeout=_WAIT_SECONDS)


@pytest.mark.parametrize("boundary", _BOUNDARIES.values(), ids=_BOUNDARIES.keys())
async def test_a_restarted_engine_completes_the_task_it_lost_at_a_step_boundary(
    boundary: _Boundary, tmp_path: Path, tmux_server: TmuxServer
) -> None:
    await init_project_repo(tmp_path)
    initial = await _head(tmp_path)
    if boundary.blocked_merge:
        (tmp_path / FAKE_CHANGE_FILENAME).write_text("uncommitted\n")
    for role, steps in boundary.scripts.items():
        await set_role_script(tmux_server, role, steps)
    tmux = build_tmux(tmux_server)
    await SessionView(tmux).ensure(_PROJECT_NAME, tmp_path, home_command=("/bin/sh",))
    project_clock = FakeClock(TASK_CREATED_AT)
    project = _Project(tmp_path, project_clock)
    engines = _Engines(tmp_path, tmux, tmux_server)
    leftover_id = (
        _seed_repo_less_leftover(project, project_clock) if boundary.repo_less_leftover else None
    )
    try:
        task_id: str | None = None
        interrupted: list[_Interrupted] = []
        for index, kill in enumerate(boundary.kills):
            task_id, lost = await _kill_at(project, engines, kill, task_id)
            interrupted.append(lost)
            if index == 0:
                await _while_first_engine_down(tmp_path, boundary)
        assert task_id is not None
        clock = FakeClock(TASK_CREATED_AT)
        await _run_until_finished(project, task_id, boundary.status, engines.build(clock), clock)

        await _assert_resumed(project, task_id, boundary, interrupted, initial, tmux=tmux)
        if leftover_id is not None:
            await _assert_leftover_reclaimed_once(project, engines, leftover_id)
    finally:
        await engines.reap()
        project.close()


async def test_a_restarted_engine_leaves_a_paused_integrating_task_alone_until_it_is_resumed(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    await init_project_repo(tmp_path)
    initial = await _head(tmp_path)
    (tmp_path / FAKE_CHANGE_FILENAME).write_text("uncommitted\n")
    tmux = build_tmux(tmux_server)
    await SessionView(tmux).ensure(_PROJECT_NAME, tmp_path, home_command=("/bin/sh",))
    project = _Project(tmp_path, FakeClock(TASK_CREATED_AT))
    engines = _Engines(tmp_path, tmux, tmux_server)
    try:
        task_id, _ = await _kill_at(project, engines, _Kill(Step.INTEGRATE, None), None)
        project.tasks.set_holds(task_id, frozenset({Hold.PAUSED}))
        await _while_first_engine_down(tmp_path, _Boundary(kills=()))
        events_before = project.events.since(0, task_id=task_id)
        clock = FakeClock(TASK_CREATED_AT)
        running = asyncio.create_task(engines.build(clock).run())
        try:
            await _process(project, clock, Prioritize(task_id=_UNKNOWN_TASK_ID, priority=0))
            held = project.tasks.get(task_id)
            assert held is not None
            assert held.status is Status.INTEGRATING
            assert project.events.since(0, task_id=task_id) == events_before
            assert await _head(tmp_path) == initial

            await _process(project, clock, Resume(task_id=task_id))
            await wait_until(lambda: _finished(project, task_id, clock), timeout=_WAIT_SECONDS)
        finally:
            running.cancel()
            with suppress(asyncio.CancelledError):
                await running
        assert len(await _commit_subjects(tmp_path, initial)) == 1
    finally:
        await engines.reap()
        project.close()


async def _process(
    project: _Project, clock: FakeClock, command: Pause | Prioritize | Resume
) -> None:
    queued = project.queue.append(command)

    def processed() -> bool:
        if clock.has_pending_sleep():
            clock.advance(_POLL_ADVANCE_SECONDS)
        outcome = project.queue.status(queued.id)
        return outcome is not None and outcome.status is not CommandStatus.PENDING

    await wait_until(processed, timeout=_WAIT_SECONDS)


def _finished(project: _Project, task_id: str, clock: FakeClock) -> bool:
    if clock.has_pending_sleep():
        clock.advance(_POLL_ADVANCE_SECONDS)
    task = project.tasks.get(task_id)
    return (
        task is not None
        and task.status is Status.DONE
        and not task_root(project.root, task_id).exists()
    )


async def test_a_restarted_engine_hands_the_messages_of_the_turn_it_lost_to_the_replacement(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    await init_project_repo(tmp_path)
    await set_role_script(tmux_server, Role.IMPLEMENTER, [dict(_STALL)])
    tmux = build_tmux(tmux_server)
    await SessionView(tmux).ensure(_PROJECT_NAME, tmp_path, home_command=("/bin/sh",))
    project = _Project(tmp_path, FakeClock(TASK_CREATED_AT))
    engines = _Engines(tmp_path, tmux, tmux_server)
    messages = OperatorMessages(tmp_path)
    try:
        engine = engines.start_killable()
        task_id = await _submit(project, engine)
        messages.queue(task_id, Role.IMPLEMENTER, "first guidance", "c1")
        lost = _Kill(Step.IMPLEMENT, Role.IMPLEMENTER)

        def reached() -> bool:
            engine.advance()
            return project.reached(task_id, lost)

        await wait_until(reached, timeout=_WAIT_SECONDS)
        await engine.kill(engine_lock_path(tmp_path))
        pid_path = role_dir(run_dir(tmp_path, task_id), Role.IMPLEMENTER.value) / TURN_PID_NAME
        lost_turn = read_turn_pid(pid_path)
        os.kill(lost_turn.pid, signal.SIGKILL)
        await wait_until(lambda: not _RUNNER.is_running(lost_turn), timeout=_WAIT_SECONDS)
        messages.queue(task_id, Role.IMPLEMENTER, "newer guidance", "c2")
        clock = FakeClock(TASK_CREATED_AT)
        await _run_until_finished(project, task_id, Status.DONE, engines.build(clock), clock)

        *_, replacement = sorted(turns_dir(tmp_path, task_id).glob("*-implement-i*"))
        delivered = sorted((replacement / "messages").iterdir())
        assert [path.read_text() for path in delivered] == ["first guidance", "newer guidance"]
        prompt = (replacement / "prompt.md").read_text()
        assert prompt.index(str(delivered[0])) < prompt.index(str(delivered[1]))
    finally:
        await engines.reap()
        project.close()
