"""Every running task keeps exactly one live flow, whatever ended its previous one."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from shikiru.bootstrap import build_engine
from shikiru.domain.commands import AnswerResolution, Prioritize, Resolve, Submit
from shikiru.domain.events import EventKind
from shikiru.domain.steps import CONSULT_LIMIT, Transition
from shikiru.domain.tasks import EscalationReason, Mode, RepoState, Role, Status, Step
from shikiru.engine.flow import TaskFlow
from shikiru.store.commands import CommandQueue
from shikiru.store.database import Database
from shikiru.store.escalations import fetch_open_escalations
from shikiru.store.events import EventLog
from shikiru.store.runs import RunStore
from shikiru.store.tasks import TaskStore
from shikiru.tmux.client import Tmux
from shikiru.tmux.session import SessionView
from shikiru.workspace.roots import task_root
from tests.support.clock import FakeClock
from tests.support.engine import build_registry, engine_settings, set_role_script, wait_for_command
from tests.support.factories import CPU_UNCAPPED_PCT, TASK_CREATED_AT
from tests.support.flow import init_project_repo
from tests.support.waiting import stop_engine, wait_until, wait_until_advancing

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable
    from pathlib import Path

    from shikiru.domain.commands import Command
    from shikiru.domain.tasks import Task
    from shikiru.store.commands import CommandOutcome
    from tests.support.store import Connection
    from tests.support.tmux import TmuxServer

_TICKS = 20
_POLL_ADVANCE_SECONDS = 0.25
_TIMEOUT_SECONDS = 30


class _FinishingFlow(TaskFlow):
    """Hold the supervisor's old flow after its real work and title refresh finish."""

    def __init__(self, flow: TaskFlow) -> None:
        self._flow = flow
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.armed = False
        self._spent = False

    async def run(self, task_id: str, *, resumed: bool = False) -> None:
        await self._flow.run(task_id, resumed=resumed)
        if self.armed and not self._spent:
            self._spent = True
            self.entered.set()
            await self.release.wait()


@dataclass
class _Project:
    clock: FakeClock
    flow: _FinishingFlow
    tasks: TaskStore
    runs: RunStore
    events: EventLog
    queue: CommandQueue
    connection: Connection

    async def send(self, command: Command) -> CommandOutcome:
        return await wait_for_command(self.queue.append(command), self.queue, self.clock)

    async def until(self, condition: Callable[[], bool]) -> None:
        async def reached() -> bool:
            return condition()

        await wait_until_advancing(
            self.clock, reached, _TIMEOUT_SECONDS, step_seconds=_POLL_ADVANCE_SECONDS
        )

    async def ticks(self, task_id: str) -> None:
        for priority in range(_TICKS):
            await self.send(Prioritize(task_id=task_id, priority=priority))

    def task(self, task_id: str) -> Task:
        task = self.tasks.get(task_id)
        assert task is not None
        return task

    def turns(self, task_id: str, step: Step) -> int:
        started = self.events.since(0, kinds=[EventKind.TURN_STARTED], task_id=task_id)
        return sum(event.data["step"] == step.value for event in started)


@asynccontextmanager
async def _engine(tmp_path: Path, tmux_server: TmuxServer) -> AsyncGenerator[_Project]:
    await init_project_repo(tmp_path)
    clock = FakeClock(TASK_CREATED_AT)
    tmux = Tmux(tmux_server.runner, socket=tmux_server.socket_name, env=tmux_server.env)
    await SessionView(tmux).ensure("test", tmp_path, home_command=("/bin/sh",))
    engine = build_engine(
        tmp_path,
        engine_settings(max_cpu_pct=CPU_UNCAPPED_PCT),
        clock=clock,
        tmux=tmux,
        registry=build_registry(),
    )
    task_supervisor = engine._task_supervisor  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    finishing_flow = _FinishingFlow(task_supervisor._flow)  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    task_supervisor._flow = finishing_flow  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    connection = Database(tmp_path / ".shikiru" / "state.db").connect()
    project = _Project(
        clock,
        finishing_flow,
        TaskStore(connection, clock),
        RunStore(connection, clock),
        EventLog(connection, clock),
        CommandQueue(connection, clock),
        connection,
    )
    engine_task = asyncio.create_task(engine.run())
    try:
        yield project
    finally:
        finishing_flow.release.set()
        await stop_engine(engine_task, clock)
        connection.close()


async def test_an_answer_resolved_while_the_old_flow_is_finishing_leaves_one_live_flow(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    steps: list[dict[str, object]] = [{"kind": "needs_decision"}] * (CONSULT_LIMIT + 1)
    await set_role_script(tmux_server, Role.IMPLEMENTER, [*steps, {"kind": "stall"}])
    async with _engine(tmp_path, tmux_server) as project:
        project.flow.armed = True
        outcome = await project.send(Submit(title="decide", request="decide", mode=Mode.IMPLEMENT))
        assert outcome.result is not None
        task_id = str(outcome.result["task_id"])
        await wait_until(project.flow.entered.wait, timeout=_TIMEOUT_SECONDS)
        [escalation] = fetch_open_escalations(project.connection)
        assert escalation.reason is EscalationReason.DECISION_REQUIRED

        await project.send(
            Resolve(escalation_id=escalation.id, resolution=AnswerResolution(text="go on"))
        )
        await project.send(Prioritize(task_id=task_id, priority=1))
        assert project.task(task_id).status is Status.RUNNING
        project.flow.release.set()

        await project.until(lambda: project.turns(task_id, Step.IMPLEMENT) == CONSULT_LIMIT + 2)
        await project.ticks(task_id)
        assert project.turns(task_id, Step.IMPLEMENT) == CONSULT_LIMIT + 2
        assert project.turns(task_id, Step.CONSULT) == CONSULT_LIMIT + 1


async def test_a_running_task_left_without_a_flow_gets_one_on_the_next_tick(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    async with _engine(tmp_path, tmux_server) as project:
        await project.send(Prioritize(task_id="T-0", priority=1))
        submitted = project.queue.append(Submit(title="orphaned", request="orphaned"))
        assert isinstance(submitted.command, Submit)
        task = project.tasks.insert_from_submit(submitted.command)
        project.queue.mark_done(submitted.id, {"task_id": task.id})
        project.tasks.apply_transition(
            task.id, Transition.stay(task, step=Step.VERIFY, status=Status.RUNNING)
        )
        project.tasks.set_slot(task.id, 0)
        project.tasks.set_repo(
            task.id,
            RepoState(base="missing", base_ref="missing", base_sha="0" * 40, branch="shikiru/lost"),
        )
        task_root(tmp_path, task.id).mkdir(parents=True)

        await project.until(lambda: bool(fetch_open_escalations(project.connection)))
        await project.ticks(task.id)

        [escalation] = fetch_open_escalations(project.connection)
        assert escalation.reason is EscalationReason.ENGINE_ERROR
        assert project.task(task.id).status is Status.ESCALATED
