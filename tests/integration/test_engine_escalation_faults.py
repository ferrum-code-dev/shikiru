"""A packet that cannot be written never kills or wedges the engine (workflow invariant)."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from shikiru.agents.contracts import role_dir
from shikiru.bootstrap import build_engine
from shikiru.config.settings import RepoConfig
from shikiru.domain.commands import Submit
from shikiru.domain.events import EventKind
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import EscalationReason, Role, Status, Step
from shikiru.engine.evidence import HISTORY_UNAVAILABLE
from shikiru.paths import project_state_dir, run_dir, task_root
from shikiru.process import ProcessResult, SubprocessRunner
from shikiru.store.commands import CommandQueue
from shikiru.store.database import Database
from shikiru.store.escalations import fetch_open_escalations
from shikiru.store.events import EventLog
from shikiru.store.tasks import TaskStore
from shikiru.tmux.session import SessionView
from tests.support.clock import FakeClock
from tests.support.engine import (
    build_raiser,
    build_registry,
    build_tmux,
    build_writer,
    engine_settings,
)
from tests.support.factories import CPU_UNCAPPED_PCT, TASK_CREATED_AT
from tests.support.flow import init_project_repo
from tests.support.waiting import wait_until, wait_until_advancing
from tests.support.workspace import task_root_builder

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable, Sequence
    from pathlib import Path

    from shikiru.domain.tasks import Task
    from tests.support.tmux import TmuxServer

_WAIT_SECONDS = 15
_TICKS_AFTER_FAILURE = 200
_EVIDENCE = "evidence that survives the failure"


class _World:
    def __init__(self, project: Path, clock: FakeClock) -> None:
        self.project = project
        self.clock = clock
        self.connection = Database(project / ".shikiru" / "state.db").connect()
        self.tasks = TaskStore(self.connection, clock)
        self.queue = CommandQueue(self.connection, clock)
        self.events = EventLog(self.connection, clock)

    def submit(self, title: str, *, depends_on: tuple[str, ...] = ()) -> Task:
        queued = self.queue.append(Submit(title=title, request=title, depends_on=list(depends_on)))
        assert isinstance(queued.command, Submit)
        task = self.tasks.insert_from_submit(queued.command)
        self.queue.mark_done(queued.id, {"task_id": task.id})
        return task

    def block_packets(self, task_id: str) -> Path:
        blocked = run_dir(self.project, task_id) / "escalations"
        blocked.parent.mkdir(parents=True, exist_ok=True)
        blocked.write_text("not a directory")
        return blocked

    def leave_turn_evidence(self, task_id: str, role: Role) -> None:
        view = role_dir(run_dir(self.project, task_id), role.value) / "view.jsonl"
        view.parent.mkdir(parents=True, exist_ok=True)
        view.write_text(f'{{"signal":"TurnEnded","last_message":"{_EVIDENCE}"}}\n')

    async def until(self, condition: Callable[[], object]) -> None:
        async def reached() -> bool:
            return bool(condition())

        await wait_until_advancing(self.clock, reached, _WAIT_SECONDS)

    def fault_events(self, task_id: str) -> int:
        return len(self.events.since(0, kinds=[EventKind.ENGINE_FAULT], task_id=task_id))

    def status(self, task_id: str) -> Status:
        task = self.tasks.get(task_id)
        assert task is not None
        return task.status


@asynccontextmanager
async def _world(project: Path, tmux_server: TmuxServer) -> AsyncGenerator[_World]:
    clock = FakeClock(TASK_CREATED_AT)
    await init_project_repo(project)
    await SessionView(build_tmux(tmux_server)).ensure("test", project, home_command=("/bin/sh",))
    project_state_dir(project).mkdir(parents=True, exist_ok=True)
    yield _World(project, clock)


class _WorktreeGitCalls(SubprocessRunner):
    def __init__(self, root: Path) -> None:
        self.root = root
        self.calls = 0

    async def run(self, argv: Sequence[str], **kwargs: Any) -> ProcessResult:
        if kwargs.get("cwd") == self.root:
            self.calls += 1
        return await super().run(argv, **kwargs)


@asynccontextmanager
async def _running(
    world: _World, tmux_server: TmuxServer, runner: SubprocessRunner | None = None
) -> AsyncGenerator[asyncio.Task[None]]:
    settings = engine_settings(max_cpu_pct=CPU_UNCAPPED_PCT)
    engine = build_engine(
        world.project,
        settings,
        clock=world.clock,
        runner=runner,
        tmux=build_tmux(tmux_server),
        registry=build_registry(),
    )
    served = asyncio.create_task(engine.run())
    try:
        yield served
    finally:
        if not served.done():
            engine.request_stop()
            await world.until(served.done)
        world.connection.close()


async def _settle(world: _World) -> None:
    ticks = 0

    async def ticked() -> bool:
        nonlocal ticks
        world.clock.advance(1)
        ticks += 1
        return ticks >= _TICKS_AFTER_FAILURE

    await wait_until(ticked, _WAIT_SECONDS)


async def _assert_repaired(world: _World, task_id: str, *, evidence: bool = True) -> None:
    (escalation,) = [e for e in fetch_open_escalations(world.connection) if e.task_id == task_id]
    packet = world.project / ".shikiru" / "runs" / task_id / "escalations" / f"{escalation.id}.md"
    await world.until(packet.exists)
    text = await asyncio.to_thread(packet.read_text)
    assert str(escalation.reason.value) in text
    assert (_EVIDENCE in text) is evidence
    assert len(fetch_open_escalations(world.connection)) == 1


async def test_a_flow_whose_packet_cannot_be_written_escalates_and_the_packet_follows_the_repair(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    async with _world(tmp_path, tmux_server) as world:
        task = world.submit("corrupt history")
        stale = run_dir(tmp_path, task.id) / "turns" / "00-stale"
        stale.mkdir(parents=True)
        (stale / "engine-verdict.json").write_text("{}")
        blocked = world.block_packets(task.id)
        world.leave_turn_evidence(task.id, Role.LEADER)
        async with _running(world, tmux_server) as served:
            await world.until(lambda: world.status(task.id) is Status.ESCALATED)
            await _settle(world)

            assert not served.done()
            assert len(fetch_open_escalations(world.connection)) == 1
            assert world.fault_events(task.id) == 1
            assert len(world.events.since(0, kinds=[EventKind.ESCALATION], task_id=task.id)) == 1

            blocked.unlink()
            await _assert_repaired(world, task.id)
            packet = next((blocked.parent / "escalations").iterdir())
            assert HISTORY_UNAVAILABLE.split("{")[0] in packet.read_text()


async def test_a_dependency_escalation_with_an_unwritable_packet_does_not_block_other_tasks(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    async with _world(tmp_path, tmux_server) as world:
        dependency = world.submit("cancelled dependency")
        dependent = world.submit("dependent", depends_on=(dependency.id,))
        bystander = world.submit("bystander")
        stored = world.tasks.get(dependency.id)
        assert stored is not None
        world.tasks.apply_transition(
            stored.id, Transition.stay(stored, step=Step.IMPLEMENT, status=Status.CANCELLED)
        )
        blocked = world.block_packets(dependent.id)
        async with _running(world, tmux_server) as served:
            await world.until(lambda: world.status(dependent.id) is Status.ESCALATED)
            await world.until(
                lambda: world.events.since(0, kinds=[EventKind.TASK_STARTED], task_id=bystander.id)
            )
            await _settle(world)

            assert not served.done()
            assert world.fault_events(dependent.id) == 1

            blocked.unlink()
            await _assert_repaired(world, dependent.id, evidence=False)


async def test_a_restart_with_an_unwritable_packet_directory_keeps_running_and_stops_collecting(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    async with _world(tmp_path, tmux_server) as world:
        task = world.submit("escalated before restart")
        repo = await task_root_builder(SubprocessRunner(), tmp_path, RepoConfig()).build(
            task.id, task.title
        )
        world.tasks.set_repo(task.id, repo)
        blocked = world.block_packets(task.id)
        world.leave_turn_evidence(task.id, Role.IMPLEMENTER)
        escalated = Transition.escalate_current(task, EscalationReason.AGENT_ERROR)
        writer = build_writer(world.connection, world.clock, tmp_path)
        await build_raiser(world.connection, world.clock, tmp_path).raise_escalation(
            task,
            step=Step.IMPLEMENT,
            reason=EscalationReason.AGENT_ERROR,
            question="The agent kept failing.",
            attempted="Retried.",
            reports=(),
            facts="",
            view_tail="",
            apply_state=lambda: writer.persist(task, escalated, None),
        )
        assert world.fault_events(task.id) == 1
        runner = _WorktreeGitCalls(task_root(tmp_path, task.id))
        async with _running(world, tmux_server, runner) as served:
            await _settle(world)
            collected = runner.calls
            await _settle(world)

            assert not served.done()
            assert world.status(task.id) is Status.ESCALATED
            assert world.fault_events(task.id) == 2
            assert runner.calls == collected

            blocked.unlink()
            await _assert_repaired(world, task.id)
