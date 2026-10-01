"""Every running task keeps exactly one live flow, whatever ended its previous one (03 §7, §10)."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING

from shikiru.bootstrap import build_engine
from shikiru.domain.commands import AnswerResolution, Prioritize, Resolve, Submit
from shikiru.domain.events import EventKind
from shikiru.domain.steps import CONSULT_LIMIT, Transition
from shikiru.domain.tasks import EscalationReason, Mode, RepoState, Role, Status, Step
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
from tests.support.waiting import wait_until

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


class _GatedTmux(Tmux):
    """A tmux client whose first decoration call while `armed` holds until released."""

    def __init__(self, tmux_server: TmuxServer) -> None:
        super().__init__(tmux_server.runner, socket=tmux_server.socket_name, env=tmux_server.env)
        self.armed: Callable[[], bool] = lambda: False
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self._spent = False

    async def _gate(self) -> None:
        if not self._spent and self.armed():
            self._spent = True
            self.entered.set()
            await self.release.wait()

    async def set_window_option(self, target: str, name: str, value: str) -> None:
        await self._gate()
        await super().set_window_option(target, name, value)

    async def set_pane_option(self, target: str, name: str, value: str) -> None:
        await self._gate()
        await super().set_pane_option(target, name, value)

    async def set_pane_title(self, target: str, title: str) -> None:
        await self._gate()
        await super().set_pane_title(target, title)

    async def rename_window(self, target: str, name: str) -> None:
        await self._gate()
        await super().rename_window(target, name)


@dataclass
class _Project:
    clock: FakeClock
    tmux: _GatedTmux
    tasks: TaskStore
    runs: RunStore
    events: EventLog
    queue: CommandQueue
    connection: Connection

    async def send(self, command: Command) -> CommandOutcome:
        return await wait_for_command(self.queue.append(command), self.queue, self.clock)

    async def until(self, condition: Callable[[], bool]) -> None:
        async def reached() -> bool:
            if self.clock.has_pending_sleep():
                self.clock.advance(_POLL_ADVANCE_SECONDS)
            return condition()

        await wait_until(reached, timeout=_TIMEOUT_SECONDS)

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
    tmux = _GatedTmux(tmux_server)
    await SessionView(tmux).ensure("test", tmp_path, home_command=("/bin/sh",))
    engine = build_engine(
        tmp_path,
        engine_settings(max_cpu_pct=CPU_UNCAPPED_PCT),
        clock=clock,
        tmux=tmux,
        registry=build_registry(),
    )
    connection = Database(tmp_path / ".shikiru" / "state.db").connect()
    project = _Project(
        clock,
        tmux,
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
        tmux.release.set()
        if not engine_task.done():
            engine_task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await engine_task
        connection.close()


async def test_an_answer_resolved_while_the_old_flow_is_finishing_leaves_one_live_flow(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    steps: list[dict[str, object]] = [{"kind": "needs_decision"}] * (CONSULT_LIMIT + 1)
    await set_role_script(tmux_server, Role.IMPLEMENTER, [*steps, {"kind": "stall"}])
    async with _engine(tmp_path, tmux_server) as project:
        project.tmux.armed = lambda: any(
            task.status is Status.ESCALATED for task in project.tasks.list_active()
        )
        outcome = await project.send(Submit(title="decide", request="decide", mode=Mode.IMPLEMENT))
        assert outcome.result is not None
        task_id = str(outcome.result["task_id"])
        await wait_until(project.tmux.entered.wait, timeout=_TIMEOUT_SECONDS)
        [escalation] = fetch_open_escalations(project.connection)
        assert escalation.reason is EscalationReason.DECISION_REQUIRED

        await project.send(
            Resolve(escalation_id=escalation.id, resolution=AnswerResolution(text="go on"))
        )
        await project.send(Prioritize(task_id=task_id, priority=1))
        assert project.task(task_id).status is Status.RUNNING
        project.tmux.release.set()

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
