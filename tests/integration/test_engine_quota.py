"""Quota polling with the configurable fake agent and real quota rows."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from shikiru.agents.contracts import Rung
from shikiru.bootstrap import build_engine
from shikiru.config.settings import AgentConfig, LayoutSplit, PaneLeaf, ProjectConfig
from shikiru.domain.commands import Stop, Submit
from shikiru.domain.events import EventKind
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import Role, Status, Step
from shikiru.engine.agents import AgentPool
from shikiru.engine.quota_poller import QUOTA_POLL_SECONDS, QuotaPoller
from shikiru.process import SubprocessRunner
from shikiru.store.commands import CommandQueue, CommandStatus
from shikiru.store.database import Database
from shikiru.store.events import EventLog
from shikiru.store.quotas import QuotaStore
from shikiru.store.runs import RunStore
from shikiru.store.tasks import TaskStore
from tests.support.clock import FakeClock
from tests.support.engine import build_registry
from tests.support.factories import TASK_CREATED_AT, make_settings
from tests.support.store import seed_task
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.config.settings import Settings
    from shikiru.store.commands import QueuedCommand
    from tests.support.store import Connection


_FAKE_RUNG = Rung(agent="fake", model="fake-1", effort=None)
_LAYOUT = LayoutSplit(
    split="h",
    panes=[
        PaneLeaf(pane="leader"),
        PaneLeaf(pane="implementer"),
        PaneLeaf(pane="system"),
        PaneLeaf(pane="shell"),
    ],
)


def _settings(sequence: list[list[dict[str, Any]]]) -> Settings:
    settings = make_settings(
        leader_ladder=[_FAKE_RUNG],
        implementer_ladder=[_FAKE_RUNG],
        agents={"fake": AgentConfig(options={"quota_sequence": sequence})},
    )
    orchestration = settings.orchestration.model_copy(
        update={
            "max_parallel": 1,
            "resources": settings.orchestration.resources.model_copy(
                update={"max_cpu_pct": 100, "min_free_ram_gb": 0}
            ),
        }
    )
    return settings.model_copy(
        update={
            "project": ProjectConfig(name="test"),
            "orchestration": orchestration,
            "dashboard": settings.dashboard.model_copy(update={"layout": _LAYOUT}),
        }
    )


async def _wait_for_command(queued: QueuedCommand, queue: CommandQueue, clock: FakeClock) -> None:
    async def completed() -> bool:
        if clock.has_pending_sleep():
            clock.advance(0.25)
        outcome = queue.status(queued.id)
        return outcome is not None and outcome.status is not CommandStatus.PENDING

    await wait_until(completed, timeout=10)


async def test_quota_poller_persists_periodic_and_failure_triggered_snapshots(
    tmp_path: Path,
) -> None:
    sequence = [
        [
            {
                "name": "short",
                "used_pct": 10,
                "observed_at": TASK_CREATED_AT,
            }
        ],
        [
            {
                "name": "short",
                "used_pct": 20,
                "observed_at": TASK_CREATED_AT + timedelta(minutes=1),
            }
        ],
        [
            {
                "name": "short",
                "used_pct": 30,
                "observed_at": TASK_CREATED_AT + timedelta(minutes=2),
            }
        ],
    ]
    clock = FakeClock(TASK_CREATED_AT)
    settings = _settings(sequence)
    engine = build_engine(tmp_path, settings, clock=clock, registry=build_registry())
    connection = Database(tmp_path / ".shikiru" / "state.db").connect()
    tasks = TaskStore(connection, clock)
    queue = CommandQueue(connection, clock)
    submitted = queue.append(
        Submit(
            task_id="",
            title="quota task",
            request="quota task",
            leader=_FAKE_RUNG,
            implementer=_FAKE_RUNG,
        )
    )
    assert isinstance(submitted.command, Submit)
    task = tasks.insert_from_submit(submitted.command)
    tasks.apply_transition(
        task.id,
        Transition.stay(task, step=Step.IMPLEMENT, status=Status.DONE),
    )
    RunStore(connection, clock).start(task.id, Role.IMPLEMENTER, _FAKE_RUNG)
    quotas = QuotaStore(connection)
    engine_task = asyncio.create_task(engine.run())

    async def has_usage(expected: float) -> bool:
        if clock.has_pending_sleep():
            clock.advance(0.25)
        windows = quotas.quotas_for("fake")
        return bool(windows) and windows[0].used_pct == expected

    try:
        await wait_until(lambda: has_usage(10), timeout=10)
        EventLog(connection, clock).append(
            EventKind.QUOTA_BLOCKED,
            task_id=task.id,
            message="quota blocked",
        )
        await wait_until(lambda: has_usage(20), timeout=10)
        clock.advance(QUOTA_POLL_SECONDS)
        await wait_until(lambda: has_usage(30), timeout=10)

        stopped = queue.append(Stop())
        await _wait_for_command(stopped, queue, clock)
        await engine_task
    finally:
        if not engine_task.done():
            engine_task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await engine_task
        connection.close()


async def test_quota_poller_continues_after_one_agent_plugin_failure(
    connection: Connection,
) -> None:
    sequence = [[{"name": "short", "used_pct": 10, "observed_at": TASK_CREATED_AT}]]
    settings = _settings(sequence).model_copy(
        update={
            "agents": {
                "fake": AgentConfig(options={"quota_sequence": sequence}),
                "fake2": AgentConfig(options={"quota_failure": "probe failed"}),
            }
        }
    )
    clock = FakeClock(TASK_CREATED_AT)
    runs = RunStore(connection, clock)
    quotas = QuotaStore(connection)
    first = seed_task(connection, clock, title="healthy quota")
    second = seed_task(connection, clock, title="failing quota")
    runs.start(first.id, Role.IMPLEMENTER, Rung(agent="fake", model="fake-1"))
    runs.start(second.id, Role.IMPLEMENTER, Rung(agent="fake2", model="fake-1"))
    poller = QuotaPoller(
        AgentPool(build_registry().create, SubprocessRunner(), settings, clock),
        runs,
        quotas,
        clock,
    )

    await poller.poll_once()

    [window] = quotas.quotas_for("fake")
    assert window.used_pct == 10
    assert quotas.quotas_for("fake2") == []
