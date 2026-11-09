"""Scheduler gates and dispatch behaviour against real SQLite and tmux."""

from __future__ import annotations

import asyncio
import dataclasses
import errno
import json
import os
import shlex
import stat
import threading
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, cast

import pytest

from shikiru.agents.contracts import QuotaWindow, Rung
from shikiru.config.settings import AgentConfig, LayoutSplit, PaneLeaf, ProjectConfig
from shikiru.domain.commands import Submit
from shikiru.domain.events import EventKind
from shikiru.domain.ladder import pick_rung
from shikiru.domain.reports import NewTask
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import (
    EscalationReason,
    Hold,
    Role,
    Status,
    StatusReason,
    Step,
    TaskOrigin,
)
from shikiru.engine.agents import AgentPool
from shikiru.engine.dependency import DependencyEscalator
from shikiru.engine.escalations import DispatchFailureEscalator, EscalationRaiser
from shikiru.engine.layout_guard import LayoutGuard
from shikiru.engine.quota_probe_threads import THREAD_NAME_PREFIX
from shikiru.engine.quota_reconciler import QUOTA_REFRESH_SCOPE
from shikiru.engine.root_preparation import RootPreparation
from shikiru.engine.scheduler import FETCH_FAULT_SCOPE, MAX_DISPATCH_FAILURES, Scheduler
from shikiru.paths import run_dir, task_root
from shikiru.process import SubprocessRunner
from shikiru.store.commands import CommandQueue
from shikiru.store.database import transaction
from shikiru.store.escalations import EscalationStore, fetch_open_escalations
from shikiru.store.events import EventLog
from shikiru.store.quotas import QuotaStore
from shikiru.store.runs import RunStore
from shikiru.store.tasks import TaskStore
from shikiru.telemetry import resources as resources_module
from shikiru.telemetry.resources import ResourceSampler
from shikiru.tmux.client import exact_target
from shikiru.tmux.decorator import (
    PANE_GLYPH_OPTION,
    WINDOW_STATE_GLYPH_OPTION,
    PaneDecorator,
)
from shikiru.tmux.layout import LayoutBuilder
from shikiru.tmux.session import SessionView, session_name
from shikiru.ui.glyphs import GLYPH_QUOTA, QUOTA_MARKER
from shikiru.view_log import quota_status_path, read_quota_status
from shikiru.workspace.base_refs import FetchedBaseRefs, LocalBaseRefs
from shikiru.workspace.caches import CacheCloner
from shikiru.workspace.git import Git
from shikiru.workspace.roots import TaskRootBuilder
from tests.support.clock import LONG_AGO, FakeClock
from tests.support.engine import (
    build_decorator,
    build_evidence,
    build_fault_reporter,
    build_reconciler,
    build_registry,
    build_titles,
    build_tmux,
    build_writer,
)
from tests.support.factories import CPU_UNCAPPED_PCT, TASK_CREATED_AT, make_settings
from tests.support.flow import init_project_repo
from tests.support.git_repo import CACHE_COPY_TIMEOUT, GIT_COMMAND_TIMEOUT, commit_all
from tests.support.github import add_bare_remote
from tests.support.handshake import fifo_writer, release_fifo
from tests.support.permissions import EXECUTABLE_MODE
from tests.support.quota_waits import record_quota_wait
from tests.support.store import seed_task
from tests.support.tmux import attach_tmux_client
from tests.support.waiting import MEDIUM_WAIT_SECONDS, wait_until, wait_until_advancing
from tests.support.workspace import task_root_builder

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator
    from datetime import datetime
    from pathlib import Path

    from shikiru.clock import Clock
    from shikiru.config.settings import Settings
    from shikiru.domain.scheduling import ReadySchedule
    from shikiru.domain.tasks import Task
    from shikiru.engine.quota_reconciler import QuotaReconciler
    from shikiru.engine.titles import TaskTitles
    from tests.support.store import Connection
    from tests.support.tmux import TmuxServer


async def test_dispatch_and_guard_recovery_keep_an_attached_client_in_place(
    scheduler_env: SchedulerFixture,
    connection: Connection,
    tmux_server: TmuxServer,
) -> None:
    tmux = build_tmux(tmux_server)
    target = exact_target(session_name("test"))
    queued = _submit(connection, scheduler_env.clock, title="Keep Home focused")

    async with attach_tmux_client(tmux_server, target):
        before = await tmux.list_clients(target, "#{window_id}\t#{pane_id}")
        schedule = await scheduler_env.scheduler.run_once()
        dispatched = scheduler_env.tasks.get(queued.id)
        assert dispatched is not None
        assert schedule.dispatches
        assert dispatched.window_id is not None
        assert dispatched.panes
        home_window, _home_pane = before[0].split("\t", 1)
        assert await tmux.display(home_window, "#{window_name}") == "dashboard"
        assert await tmux.display(dispatched.window_id, "#{window_name}") == (
            f"{queued.id} Keep Home focused"
        )
        await scheduler_env.titles.refresh(dispatched)
        assert await tmux.list_clients(target, "#{window_id}\t#{pane_id}") == before
        dispatched_pane = next(iter(dispatched.panes.values()))
        await tmux_server.run("kill-pane", "-t", dispatched_pane)

        await scheduler_env.layout.ensure(dispatched)

        after = await tmux.list_clients(target, "#{window_id}\t#{pane_id}")

    assert before == after


_FAKE_RUNG = Rung(agent="fake", model="fake-1", effort=None)
_FALLBACK_RUNG = Rung(agent="fallback", model="fallback-1", effort=None)
_BLOCKED_RUNG = Rung(agent="blocked", model="blocked-1", effort=None)
_QUOTA_WAIT_SECONDS = 300
_SAMPLED_CPU_PERCENT = 50.0
_TRACKER = "TODO.md"
_IMPLEMENTATION = "impl.py"
_LAYOUT = LayoutSplit(
    split="h",
    panes=[
        PaneLeaf(pane="leader"),
        PaneLeaf(pane="implementer"),
    ],
)


@dataclass
class SchedulerFixture:
    scheduler: Scheduler
    settings: Settings
    tasks: TaskStore
    quotas: QuotaStore
    reconciler: QuotaReconciler
    events: EventLog
    layout: LayoutGuard
    roots: TaskRootBuilder
    titles: TaskTitles
    dependency_callback: Callable[[Task], Awaitable[None]]
    dispatch_failure_callback: Callable[[Task, Exception], Awaitable[None]]
    clock: Clock


def _settings(*, max_parallel: int = 2, max_cpu_pct: int = CPU_UNCAPPED_PCT) -> Settings:
    settings = make_settings(
        leader_ladder=[_FAKE_RUNG],
        implementer_ladder=[_FAKE_RUNG],
        agents={"fake": AgentConfig()},
    )
    orchestration = settings.orchestration.model_copy(
        update={
            "max_parallel": max_parallel,
            "resources": settings.orchestration.resources.model_copy(
                update={"max_cpu_pct": max_cpu_pct, "min_free_ram_gb": 0}
            ),
        }
    )
    dashboard = settings.dashboard.model_copy(
        update={
            "layout": _LAYOUT,
            "titles": settings.dashboard.titles.model_copy(
                update={"window": "{task_id} {title:.24}"}
            ),
        }
    )
    return settings.model_copy(
        update={
            "project": ProjectConfig(name="test", trackers=[_TRACKER]),
            "orchestration": orchestration,
            "dashboard": dashboard,
        }
    )


def _submit(
    connection: Connection,
    clock: Clock,
    *,
    title: str,
    priority: int = 0,
    depends_on: list[str] | None = None,
) -> Task:
    queued = CommandQueue(connection, clock).append(
        Submit(
            title=title,
            request=title,
            priority=priority,
            depends_on=depends_on or [],
        )
    )
    assert isinstance(queued.command, Submit)
    return TaskStore(connection, clock).insert_from_submit(queued.command)


def _scheduler(environment: SchedulerFixture, settings: Settings) -> Scheduler:
    return Scheduler(
        tasks=environment.tasks,
        settings=settings,
        quotas=environment.quotas,
        reconciler=environment.reconciler,
        resources=ResourceSampler(environment.clock, lambda: settings.orchestration.resources),
        clock=environment.clock,
        layout=environment.layout,
        roots=RootPreparation(
            environment.roots,
            environment.clock,
            budget_seconds=settings.orchestration.dispatch_budget_seconds,
        ),
        events=environment.events,
        on_dependency_cancelled=environment.dependency_callback,
        on_dispatch_failure=environment.dispatch_failure_callback,
        titles=environment.titles,
        faults=build_fault_reporter(environment.events, environment.clock, settings),
    )


@pytest.fixture
async def scheduler_env(
    connection: Connection,
    tmux_server: TmuxServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> SchedulerFixture:
    def fixed_cpu_percent(interval: float | None = None) -> float:
        del interval
        return _SAMPLED_CPU_PERCENT

    monkeypatch.setattr(resources_module.psutil, "cpu_percent", fixed_cpu_percent)
    await init_project_repo(tmp_path)
    clock = FakeClock(TASK_CREATED_AT)
    settings = cast("Settings", request.param) if hasattr(request, "param") else _settings()
    tasks = TaskStore(connection, clock)
    quotas = QuotaStore(connection)
    events = EventLog(connection, clock)
    tmux = build_tmux(tmux_server)
    decorator = PaneDecorator(tmux)
    runs = RunStore(connection, clock)
    layout = LayoutGuard(
        tmux=tmux,
        view=SessionView(tmux),
        builder=LayoutBuilder(tmux),
        decorator=decorator,
        session_target=exact_target(session_name("test")),
        tasks=tasks,
        settings=settings,
        project_root=tmp_path,
        events=events,
        runs=runs,
        commands=CommandQueue(connection, clock),
        faults=build_fault_reporter(events, clock, settings),
        refresh_titles=build_titles(
            connection=connection,
            clock=clock,
            decorator=decorator,
            settings=settings,
            project_root=tmp_path,
        ).refresh,
    )
    roots = task_root_builder(
        SubprocessRunner(), tmp_path, settings.repo, trackers=settings.project.trackers
    )
    await SessionView(tmux).ensure("test", tmp_path, home_command=("/bin/sh",))

    titles = build_titles(
        connection=connection,
        clock=clock,
        decorator=build_decorator(tmux),
        settings=settings,
        project_root=tmp_path,
    )
    raiser = EscalationRaiser(
        EscalationStore(connection, clock),
        events,
        RunStore(connection, clock),
        tmp_path,
        atomically=lambda: transaction(connection),
        titles=titles,
        evidence=build_evidence(tmp_path),
        settings=settings,
    )
    writer = build_writer(connection, clock, tmp_path, tmux=tmux)
    dependency_callback = DependencyEscalator(writer, raiser)
    dispatch_failure_callback = DispatchFailureEscalator(writer, raiser)
    reconciler = build_reconciler(
        quotas=quotas,
        agents=AgentPool(build_registry().create, SubprocessRunner(), settings, clock),
        events=events,
        clock=clock,
        settings=settings,
    )
    scheduler = Scheduler(
        tasks=tasks,
        settings=settings,
        quotas=quotas,
        reconciler=reconciler,
        resources=ResourceSampler(clock, lambda: settings.orchestration.resources),
        clock=clock,
        layout=layout,
        roots=RootPreparation(
            roots, clock, budget_seconds=settings.orchestration.dispatch_budget_seconds
        ),
        events=events,
        on_dependency_cancelled=dependency_callback,
        on_dispatch_failure=dispatch_failure_callback,
        titles=titles,
        faults=build_fault_reporter(events, clock, settings),
    )
    return SchedulerFixture(
        scheduler,
        settings,
        tasks,
        quotas,
        reconciler,
        events,
        layout,
        roots,
        titles,
        dependency_callback,
        dispatch_failure_callback,
        clock,
    )


async def test_priority_order_prefers_previous_slot(
    scheduler_env: SchedulerFixture, connection: Connection
) -> None:
    low = _submit(connection, scheduler_env.clock, title="low", priority=1)
    high = _submit(connection, scheduler_env.clock, title="high", priority=10)
    scheduler_env.tasks.set_slot(low.id, 1)
    scheduler_env.tasks.set_slot(high.id, 0)

    schedule = await scheduler_env.scheduler.run_once()

    assert [(dispatch.task_id, dispatch.slot) for dispatch in schedule.dispatches] == [
        (high.id, 0),
        (low.id, 1),
    ]
    for task_id, slot in ((high.id, 0), (low.id, 1)):
        current = scheduler_env.tasks.get(task_id)
        assert current is not None
        assert current.status is Status.RUNNING
        assert current.slot == slot
        assert current.window_id is not None
    started = scheduler_env.events.since(0, kinds=[EventKind.TASK_STARTED])
    assert [event.task_id for event in started] == [high.id, low.id]


async def test_parallelism_persists_slot_wait_reason(
    scheduler_env: SchedulerFixture, connection: Connection
) -> None:
    first = _submit(connection, scheduler_env.clock, title="first")
    second = _submit(connection, scheduler_env.clock, title="second")
    scheduler = _scheduler(scheduler_env, _settings(max_parallel=1))

    schedule = await scheduler.run_once()

    assert [dispatch.task_id for dispatch in schedule.dispatches] == [first.id]
    waiting = scheduler_env.tasks.get(second.id)
    assert waiting is not None
    assert waiting.wait_reason == "slot"


async def test_dependency_waits_then_dispatches_after_completion(
    scheduler_env: SchedulerFixture, connection: Connection
) -> None:
    dependency = _submit(connection, scheduler_env.clock, title="dependency")
    dependent = _submit(
        connection, scheduler_env.clock, title="dependent", depends_on=[dependency.id]
    )

    first_schedule = await scheduler_env.scheduler.run_once()

    assert [dispatch.task_id for dispatch in first_schedule.dispatches] == [dependency.id]
    waiting = scheduler_env.tasks.get(dependent.id)
    assert waiting is not None
    assert waiting.wait_reason == "dependency"

    current = scheduler_env.tasks.get(dependency.id)
    assert current is not None
    scheduler_env.tasks.apply_transition(
        current.id, Transition.stay(current, step=Step.INTEGRATE, status=Status.DONE)
    )
    second_schedule = await scheduler_env.scheduler.run_once()

    assert [dispatch.task_id for dispatch in second_schedule.dispatches] == [dependent.id]


async def test_integrate_dispatch_enters_integrating_status(
    scheduler_env: SchedulerFixture, connection: Connection
) -> None:
    task = _submit(connection, scheduler_env.clock, title="integrate")
    scheduler_env.tasks.apply_transition(
        task.id,
        Transition.stay(task, step=Step.INTEGRATE, status=Status.QUEUED),
    )

    schedule = await scheduler_env.scheduler.run_once()

    assert [dispatch.task_id for dispatch in schedule.dispatches] == [task.id]
    current = scheduler_env.tasks.get(task.id)
    assert current is not None
    assert current.status is Status.INTEGRATING


async def test_dispatch_emits_task_started_before_step_started(
    scheduler_env: SchedulerFixture, connection: Connection
) -> None:
    task = _submit(connection, scheduler_env.clock, title="ordered events")
    scheduler_env.tasks.apply_transition(
        task.id,
        Transition.stay(task, step=Step.IMPLEMENT, status=Status.QUEUED),
    )

    await scheduler_env.scheduler.run_once()

    events = scheduler_env.events.since(0, task_id=task.id)
    assert [
        event.kind
        for event in events
        if event.kind in (EventKind.TASK_STARTED, EventKind.STEP_STARTED)
    ] == [
        EventKind.TASK_STARTED,
        EventKind.STEP_STARTED,
    ]


async def test_cancelled_dependency_escalates_with_packet(
    scheduler_env: SchedulerFixture, connection: Connection
) -> None:
    dependency = _submit(connection, scheduler_env.clock, title="cancelled")
    dependent = _submit(
        connection, scheduler_env.clock, title="dependent", depends_on=[dependency.id]
    )
    current = scheduler_env.tasks.get(dependency.id)
    assert current is not None
    scheduler_env.tasks.apply_transition(
        current.id, Transition.stay(current, step=Step.IMPLEMENT, status=Status.CANCELLED)
    )

    schedule = await scheduler_env.scheduler.run_once()

    assert schedule.dependency_cancelled == (dependent.id,)
    updated = scheduler_env.tasks.get(dependent.id)
    assert updated is not None
    assert updated.status is Status.ESCALATED
    assert updated.status_reason is None
    assert scheduler_env.events.since(0, task_id=dependent.id)[-1].kind is EventKind.ESCALATION
    escalation = fetch_open_escalations(connection)[0]
    assert escalation.reason is EscalationReason.DEPENDENCY_CANCELLED


async def test_resource_gate_blocks_at_zero_and_dispatches_at_hundred(
    scheduler_env: SchedulerFixture,
    connection: Connection,
) -> None:
    task = _submit(connection, scheduler_env.clock, title="resources")
    blocked_scheduler = _scheduler(scheduler_env, _settings(max_cpu_pct=0))

    blocked = await blocked_scheduler.run_once()

    assert blocked.dispatches == ()
    waiting = scheduler_env.tasks.get(task.id)
    assert waiting is not None
    assert waiting.wait_reason == "resources"

    passing = await _scheduler(scheduler_env, _settings(max_cpu_pct=CPU_UNCAPPED_PCT)).run_once()

    assert [dispatch.task_id for dispatch in passing.dispatches] == [task.id]


async def test_a_queued_task_under_intervention_takes_no_slot(
    scheduler_env: SchedulerFixture, connection: Connection
) -> None:
    taken_over = _submit(connection, scheduler_env.clock, title="taken over")
    scheduler_env.tasks.set_holds(taken_over.id, frozenset({Hold.INTERVENTION}))

    schedule = await scheduler_env.scheduler.run_once()

    assert schedule.dispatches == ()
    waiting = scheduler_env.tasks.get(taken_over.id)
    assert waiting is not None
    assert waiting.status is Status.QUEUED
    assert waiting.slot is None

    scheduler_env.tasks.set_holds(taken_over.id, frozenset())
    resumed = await scheduler_env.scheduler.run_once()

    assert [dispatch.task_id for dispatch in resumed.dispatches] == [taken_over.id]


async def test_quota_gate_persists_wait_reason(
    scheduler_env: SchedulerFixture, connection: Connection
) -> None:
    task = _submit(connection, scheduler_env.clock, title="quota")
    scheduler_env.quotas.block(
        "fake", scheduler_env.clock.now() + timedelta(hours=1), "test quota", LONG_AGO
    )

    schedule = await scheduler_env.scheduler.run_once()

    assert schedule.dispatches == ()
    waiting = scheduler_env.tasks.get(task.id)
    assert waiting is not None
    assert waiting.wait_reason == "quota"


async def test_scheduler_publishes_and_clears_queued_quota_marks_for_an_existing_window(
    scheduler_env: SchedulerFixture,
    connection: Connection,
    tmux_server: TmuxServer,
    tmp_path: Path,
) -> None:
    task = _submit(connection, scheduler_env.clock, title="quota in an existing window")
    await scheduler_env.layout.ensure(task)
    current = scheduler_env.tasks.require(task.id)
    assert current.window_id is not None
    scheduler_env.quotas.block(
        "fake", scheduler_env.clock.now() + timedelta(hours=1), "quota", LONG_AGO
    )

    waiting = await scheduler_env.scheduler.run_once()

    assert waiting.dispatches == ()
    assert scheduler_env.tasks.require(task.id).wait_reason == "quota"
    assert (
        await tmux_server.run(
            "display-message", "-p", "-t", current.window_id, f"#{{{WINDOW_STATE_GLYPH_OPTION}}}"
        )
    ).stdout.strip() == QUOTA_MARKER
    status_dir = run_dir(tmp_path, task.id)
    implementer_status = quota_status_path(status_dir, Role.IMPLEMENTER.value)
    assert read_quota_status(implementer_status) is not None

    scheduler_env.quotas.clear_block("fake")
    resumed = await scheduler_env.scheduler.run_once()

    assert [dispatch.task_id for dispatch in resumed.dispatches] == [task.id]
    assert (
        await tmux_server.run(
            "display-message", "-p", "-t", current.window_id, f"#{{{WINDOW_STATE_GLYPH_OPTION}}}"
        )
    ).stdout.strip() != QUOTA_MARKER
    assert read_quota_status(implementer_status) is None


async def test_scheduler_publishes_a_running_mid_turn_quota_wait_without_agent_output(
    scheduler_env: SchedulerFixture,
    connection: Connection,
    tmux_server: TmuxServer,
    tmp_path: Path,
) -> None:
    assert isinstance(scheduler_env.clock, FakeClock)
    clock = scheduler_env.clock
    task = _submit(connection, scheduler_env.clock, title="quota during a turn")
    await scheduler_env.layout.ensure(task)
    queued = scheduler_env.tasks.require(task.id)
    scheduler_env.tasks.apply_transition(
        task.id,
        dataclasses.replace(
            Transition.stay(queued, step=Step.IMPLEMENT, status=Status.RUNNING), iteration=1
        ),
    )
    running = scheduler_env.tasks.require(task.id)
    until = clock.now() + timedelta(hours=1)
    record_quota_wait(
        connection,
        clock,
        running,
        role=Role.IMPLEMENTER,
        rung=_FAKE_RUNG,
        until=until,
    )

    await scheduler_env.scheduler.run_once()

    assert running.window_id is not None
    assert (
        await tmux_server.run(
            "display-message",
            "-p",
            "-t",
            running.window_id,
            f"#{{{WINDOW_STATE_GLYPH_OPTION}}}",
        )
    ).stdout.strip() == QUOTA_MARKER
    implementer_pane = running.panes[Role.IMPLEMENTER]
    leader_pane = running.panes[Role.LEADER]
    assert (
        await tmux_server.run(
            "display-message", "-p", "-t", implementer_pane, f"#{{{PANE_GLYPH_OPTION}}}"
        )
    ).stdout.strip() == GLYPH_QUOTA
    assert (
        await tmux_server.run(
            "display-message", "-p", "-t", leader_pane, f"#{{{PANE_GLYPH_OPTION}}}"
        )
    ).stdout.strip() != GLYPH_QUOTA
    status_dir = run_dir(tmp_path, task.id)
    assert read_quota_status(quota_status_path(status_dir, Role.IMPLEMENTER.value)) is not None
    assert read_quota_status(quota_status_path(status_dir, Role.LEADER.value)) is None

    scheduler_env.quotas.clear_block("fake")
    await scheduler_env.scheduler.run_once()

    assert (
        await tmux_server.run(
            "display-message",
            "-p",
            "-t",
            running.window_id,
            f"#{{{WINDOW_STATE_GLYPH_OPTION}}}",
        )
    ).stdout.strip() != QUOTA_MARKER
    assert read_quota_status(quota_status_path(status_dir, Role.IMPLEMENTER.value)) is None


def _quota_settings(role: Role, ladder: list[Rung], *, both_roles: bool = False) -> Settings:
    settings = _settings()
    role_configs = {
        selected.value: getattr(settings.roles, selected.value).model_copy(
            update={"ladder": ladder if selected is role or both_roles else [_FALLBACK_RUNG]}
        )
        for selected in (Role.LEADER, Role.IMPLEMENTER)
    }
    return settings.model_copy(
        update={
            "roles": settings.roles.model_copy(update=role_configs),
            "agents": {
                **settings.agents,
                _FALLBACK_RUNG.agent: AgentConfig(),
                _BLOCKED_RUNG.agent: AgentConfig(),
            },
            "orchestration": settings.orchestration.model_copy(
                update={
                    "timeouts": settings.orchestration.timeouts.model_copy(
                        update={"max_quota_wait": _QUOTA_WAIT_SECONDS}
                    )
                }
            ),
        }
    )


@pytest.mark.parametrize(
    ("scheduler_env", "role"),
    [
        (_quota_settings(role, [_FAKE_RUNG, _FALLBACK_RUNG]), role)
        for role in (Role.LEADER, Role.IMPLEMENTER)
    ],
    indirect=["scheduler_env"],
    ids=["leader", "implementer"],
)
async def test_persisted_quota_block_falls_back_for_consecutive_queued_tasks(
    scheduler_env: SchedulerFixture, connection: Connection, role: Role
) -> None:
    settings = scheduler_env.settings
    until = scheduler_env.clock.now() + timedelta(hours=5)
    scheduler_env.quotas.block(_FAKE_RUNG.agent, until, "quota", LONG_AGO)
    scheduler = _scheduler(scheduler_env, settings)

    for title in ("first task after restart", "next task"):
        queued = _submit(connection, scheduler_env.clock, title=title)

        schedule = await scheduler.run_once()

        assert [dispatch.task_id for dispatch in schedule.dispatches] == [queued.id]
        running = scheduler_env.tasks.require(queued.id)
        assert running.status is Status.RUNNING
        assert running.wait_reason is None
        assert pick_rung(running, role, settings) == _FALLBACK_RUNG
        changes = scheduler_env.events.since(0, task_id=queued.id, kinds=[EventKind.RUNG_CHANGED])
        assert len(changes) == 1
        assert changes[0].data["role"] == role.value
        assert changes[0].data["reason"] == "quota"

    block = scheduler_env.quotas.block_for(_FAKE_RUNG.agent)
    assert block is not None
    assert block.until == until


@pytest.mark.parametrize(
    ("remaining_seconds", "expected_index"),
    [(0, 0), (_QUOTA_WAIT_SECONDS, 0), (_QUOTA_WAIT_SECONDS + 1, 1)],
    ids=["expired", "within-wait-budget", "beyond-wait-budget"],
)
@pytest.mark.parametrize(
    "scheduler_env",
    [_quota_settings(Role.IMPLEMENTER, [_FAKE_RUNG, _FALLBACK_RUNG])],
    indirect=True,
)
async def test_queued_quota_fallback_respects_remaining_wait_budget(
    scheduler_env: SchedulerFixture,
    connection: Connection,
    remaining_seconds: int,
    expected_index: int,
) -> None:
    role = Role.IMPLEMENTER
    settings = scheduler_env.settings
    queued = _submit(connection, scheduler_env.clock, title="quota boundary")
    scheduler_env.quotas.block(
        _FAKE_RUNG.agent,
        scheduler_env.clock.now() + timedelta(seconds=remaining_seconds),
        "quota",
        LONG_AGO,
    )

    schedule = await _scheduler(scheduler_env, settings).run_once()

    updated = scheduler_env.tasks.require(queued.id)
    assert updated.rung_index.get(role, 0) == expected_index
    if remaining_seconds == _QUOTA_WAIT_SECONDS:
        assert schedule.dispatches == ()
        assert updated.wait_reason == "quota"
    else:
        assert [dispatch.task_id for dispatch in schedule.dispatches] == [queued.id]
        assert updated.wait_reason is None


@pytest.mark.parametrize("all_blocked", [False, True])
@pytest.mark.parametrize(
    "scheduler_env",
    [_quota_settings(Role.IMPLEMENTER, [_FAKE_RUNG, _BLOCKED_RUNG, _FALLBACK_RUNG])],
    indirect=True,
)
async def test_queued_quota_fallback_skips_other_blocked_agents(
    scheduler_env: SchedulerFixture, connection: Connection, *, all_blocked: bool
) -> None:
    role = Role.IMPLEMENTER
    ladder = [_FAKE_RUNG, _BLOCKED_RUNG, _FALLBACK_RUNG]
    settings = scheduler_env.settings
    queued = _submit(connection, scheduler_env.clock, title="multiple quota blocks")
    for rung in ladder if all_blocked else ladder[:-1]:
        scheduler_env.quotas.block(
            rung.agent, scheduler_env.clock.now() + timedelta(hours=5), "quota", LONG_AGO
        )

    schedule = await _scheduler(scheduler_env, settings).run_once()

    updated = scheduler_env.tasks.require(queued.id)
    if all_blocked:
        assert schedule.dispatches == ()
        assert updated.rung_index.get(role, 0) == 0
        assert updated.wait_reason == "quota"
    else:
        assert [dispatch.task_id for dispatch in schedule.dispatches] == [queued.id]
        assert pick_rung(updated, role, settings) == _FALLBACK_RUNG
        assert updated.rung_index[role] == 2


@pytest.mark.parametrize("first_reset", [_FAKE_RUNG, _FALLBACK_RUNG], ids=["original", "later"])
@pytest.mark.parametrize(
    "scheduler_env",
    [
        _quota_settings(
            Role.IMPLEMENTER, [_FAKE_RUNG, _BLOCKED_RUNG, _FALLBACK_RUNG], both_roles=True
        )
    ],
    indirect=True,
)
async def test_all_agents_on_quota_wait_without_moving_and_resume_when_one_resets(
    scheduler_env: SchedulerFixture, connection: Connection, first_reset: Rung
) -> None:
    clock = scheduler_env.clock
    assert isinstance(clock, FakeClock)
    settings = scheduler_env.settings
    queued = _submit(connection, clock, title="all agents exhausted")
    reset_after = _QUOTA_WAIT_SECONDS * 2
    for rung in (_FAKE_RUNG, _BLOCKED_RUNG, _FALLBACK_RUNG):
        delay = timedelta(seconds=reset_after) if rung == first_reset else timedelta(hours=5)
        scheduler_env.quotas.block(rung.agent, clock.now() + delay, "quota", LONG_AGO)
    scheduler = _scheduler(scheduler_env, settings)

    for advance in (0, _QUOTA_WAIT_SECONDS + 1):
        clock.advance(advance)

        schedule = await scheduler.run_once()

        assert schedule.dispatches == ()
        waiting = scheduler_env.tasks.require(queued.id)
        assert waiting.status is Status.QUEUED
        assert waiting.wait_reason == "quota"
        assert waiting.slot is None
        assert waiting.rung_index == {}
        assert not scheduler_env.events.since(0, task_id=queued.id, kinds=[EventKind.RUNG_CHANGED])
        assert len(scheduler_env.quotas.active_blocks(clock.now())) == 3

    clock.advance(reset_after - (_QUOTA_WAIT_SECONDS + 1))

    resumed = await _scheduler(scheduler_env, settings).run_once()

    assert [dispatch.task_id for dispatch in resumed.dispatches] == [queued.id]
    running = scheduler_env.tasks.require(queued.id)
    assert running.status is Status.RUNNING
    assert running.wait_reason is None
    for role in (Role.LEADER, Role.IMPLEMENTER):
        assert pick_rung(running, role, settings) == first_reset
    assert len(scheduler_env.quotas.active_blocks(clock.now())) == 2


async def test_dispatch_failures_are_reported_and_eventually_escalated(
    scheduler_env: SchedulerFixture,
    connection: Connection,
    tmux_server: TmuxServer,
) -> None:
    task = _submit(connection, scheduler_env.clock, title="dispatch failure")
    await tmux_server.run("kill-session", "-t", exact_target(session_name("test")))

    schedule = await scheduler_env.scheduler.run_once()

    assert schedule.dispatches == ()
    current = scheduler_env.tasks.get(task.id)
    assert current is not None
    assert current.status is Status.QUEUED
    assert current.slot is None
    assert current.window_id is None
    dispatch_failures = scheduler_env.events.since(
        0, kinds=[EventKind.DISPATCH_FAILED], task_id=task.id
    )
    assert len(dispatch_failures) == 1
    assert dispatch_failures[0].data["attempt"] == 1
    assert not scheduler_env.events.since(
        0, kinds=[EventKind.TASK_STARTED, EventKind.STEP_STARTED], task_id=task.id
    )

    await scheduler_env.scheduler.run_once()
    await scheduler_env.scheduler.run_once()

    escalated = scheduler_env.tasks.get(task.id)
    assert escalated is not None
    assert escalated.status is Status.ESCALATED
    assert fetch_open_escalations(connection)[0].reason is (EscalationReason.ENGINE_ERROR)


async def test_a_fetch_outage_delays_the_dispatch_without_escalating_and_ends_with_it(
    scheduler_env: SchedulerFixture,
    connection: Connection,
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    settings = scheduler_env.settings
    runner = SubprocessRunner()
    remote_dir = tmp_path_factory.mktemp("remote") / "origin.git"
    await add_bare_remote(tmp_path, remote_dir, settings.repo.remote)
    gone = ["git", "remote", "set-url", settings.repo.remote]
    await runner.run(
        [*gone, str(remote_dir / "gone")], timeout=MEDIUM_WAIT_SECONDS, cwd=tmp_path, check=True
    )
    roots = task_root_builder(
        runner,
        tmp_path,
        settings.repo,
        FetchedBaseRefs(
            Git(runner, command_timeout=GIT_COMMAND_TIMEOUT),
            tmp_path,
            settings.repo.remote,
            fetch_timeout=settings.orchestration.network.fetch_timeout_seconds,
        ),
    )
    scheduler = _scheduler(dataclasses.replace(scheduler_env, roots=roots), settings)
    task = _submit(connection, scheduler_env.clock, title="offline dispatch")
    backoff_max = settings.orchestration.faults.backoff_max_seconds
    clock = scheduler_env.clock
    assert isinstance(clock, FakeClock)

    for _ in range(MAX_DISPATCH_FAILURES + 2):
        clock.advance(backoff_max)
        assert (await scheduler.run_once()).dispatches == ()

    waiting = scheduler_env.tasks.get(task.id)
    assert waiting is not None
    assert waiting.status is Status.QUEUED
    assert fetch_open_escalations(connection) == []
    assert not scheduler_env.events.since(0, kinds=[EventKind.DISPATCH_FAILED])
    [fault] = scheduler_env.events.since(0, kinds=[EventKind.ENGINE_FAULT], task_id=task.id)
    assert fault.data["scope"] == FETCH_FAULT_SCOPE

    await runner.run(
        [*gone, str(remote_dir)], timeout=MEDIUM_WAIT_SECONDS, cwd=tmp_path, check=True
    )
    assert (await scheduler.run_once()).dispatches == ()
    clock.advance(backoff_max)
    schedule = await scheduler.run_once()

    assert [dispatch.task_id for dispatch in schedule.dispatches] == [task.id]
    assert scheduler_env.events.since(0, kinds=[EventKind.ENGINE_RECOVERED], task_id=task.id)


async def _parent_with_committed_work(
    environment: SchedulerFixture, connection: Connection, project_root: Path, files: dict[str, str]
) -> Task:
    parent = _submit(connection, environment.clock, title="decomposed parent")
    repo = await environment.roots.build(parent.id, parent.title)
    environment.tasks.set_repo(parent.id, repo)
    root = task_root(project_root, parent.id)
    for name, content in files.items():
        (root / name).write_text(content)
    await commit_all(SubprocessRunner(), root, "parent work")
    return parent


def _decompose(environment: SchedulerFixture, parent: Task, transition: Transition) -> Task:
    [child] = environment.tasks.apply_scope_change(
        parent.id,
        transition,
        [NewTask(title="child", request="child")],
        origin=TaskOrigin.LEADER,
    )
    return child


async def test_plan_children_start_only_after_the_parent_landed_its_tracker_changes(
    scheduler_env: SchedulerFixture, connection: Connection, tmp_path: Path
) -> None:
    parent = await _parent_with_committed_work(
        scheduler_env, connection, tmp_path, {_TRACKER: "- [ ] child\n"}
    )
    integrating = Transition.stay(parent, step=Step.INTEGRATE, status=Status.INTEGRATING)
    child = _decompose(scheduler_env, parent, integrating)

    blocked = await scheduler_env.scheduler.run_once()

    assert blocked.dispatches == ()
    waiting = scheduler_env.tasks.get(child.id)
    assert waiting is not None
    assert waiting.wait_reason == "dependency"
    assert not task_root(tmp_path, child.id).exists()

    landed = scheduler_env.tasks.get(parent.id)
    assert landed is not None
    assert landed.repo is not None
    await Git(SubprocessRunner(), command_timeout=GIT_COMMAND_TIMEOUT).merge_ff_only(
        tmp_path, landed.repo.branch
    )
    scheduler_env.tasks.apply_transition(
        parent.id, Transition.stay(landed, step=Step.INTEGRATE, status=Status.DONE)
    )
    released = await scheduler_env.scheduler.run_once()

    assert [dispatch.task_id for dispatch in released.dispatches] == [child.id]
    assert (task_root(tmp_path, child.id) / _TRACKER).read_text() == "- [ ] child\n"


async def test_superseded_replacements_receive_only_the_tracker_changes_of_the_rejected_task(
    scheduler_env: SchedulerFixture, connection: Connection, tmp_path: Path
) -> None:
    parent = await _parent_with_committed_work(
        scheduler_env,
        connection,
        tmp_path,
        {_TRACKER: "- [ ] child\n", _IMPLEMENTATION: "rejected\n"},
    )
    superseded = dataclasses.replace(
        Transition.stay(parent, step=None, status=Status.CANCELLED),
        status_reason=StatusReason.SUPERSEDED,
        repoint_dependents=True,
        enqueue_new_tasks=True,
    )
    child = _decompose(scheduler_env, parent, superseded)
    cancelled = scheduler_env.tasks.get(parent.id)
    assert cancelled is not None
    await scheduler_env.roots.cleanup(parent.id, cancelled.repo)

    schedule = await scheduler_env.scheduler.run_once()

    assert schedule.dependency_cancelled == ()
    assert [dispatch.task_id for dispatch in schedule.dispatches] == [child.id]
    root = task_root(tmp_path, child.id)
    assert (root / _TRACKER).read_text() == "- [ ] child\n"
    assert not (root / _IMPLEMENTATION).exists()
    assert (
        await Git(SubprocessRunner(), command_timeout=GIT_COMMAND_TIMEOUT).status_porcelain(root)
        == []
    )
    assert not (tmp_path / _IMPLEMENTATION).exists()
    assert not (tmp_path / _TRACKER).exists()


def _block_packet_directory(project_root: Path, task_id: str) -> None:
    blocked = project_root / ".shikiru" / "runs" / task_id / "escalations"
    blocked.parent.mkdir(parents=True)
    blocked.write_text("not a directory")


def _assert_escalated_despite_packet_failure(
    scheduler_env: SchedulerFixture, connection: Connection, task_id: str
) -> None:
    stored = scheduler_env.tasks.get(task_id)
    assert stored is not None
    assert stored.status is Status.ESCALATED
    (escalation,) = fetch_open_escalations(connection)
    assert escalation.task_id == task_id
    faults = EventLog(connection, scheduler_env.clock).since(0, kinds=[EventKind.ENGINE_FAULT])
    assert [fault.task_id for fault in faults] == [task_id]


async def test_a_dispatch_escalation_whose_packet_cannot_be_written_still_escalates_the_task(
    scheduler_env: SchedulerFixture,
    connection: Connection,
    tmux_server: TmuxServer,
    tmp_path: Path,
) -> None:
    task = _submit(connection, scheduler_env.clock, title="dispatch failure")
    await tmux_server.run("kill-session", "-t", exact_target(session_name("test")))
    _block_packet_directory(tmp_path, task.id)
    await scheduler_env.scheduler.run_once()
    await scheduler_env.scheduler.run_once()

    await scheduler_env.scheduler.run_once()

    _assert_escalated_despite_packet_failure(scheduler_env, connection, task.id)


_HUNG_UPLOAD_PACK = """#!/bin/sh
echo started >> '{calls}'
cat {release} > /dev/null
exec git upload-pack "$@"
"""
_HANDS_OFF_TASKS = 3
_REAL_SECONDS = 10.0


def _status(environment: SchedulerFixture, task: Task) -> Status:
    current = environment.tasks.get(task.id)
    assert current is not None
    return current.status


async def _land_every_dispatch(scheduler: Scheduler, expected: int) -> list[str]:
    landed: list[str] = []

    async def progressed() -> bool:
        landed.extend(dispatch.task_id for dispatch in (await scheduler.run_once()).dispatches)
        return len(landed) >= expected

    await wait_until(progressed, _REAL_SECONDS)
    return landed


@dataclass
class _HungRemote:
    settings: Settings
    roots: TaskRootBuilder
    calls: Path
    release: Path

    def fetches(self) -> int:
        return self.calls.read_text().count("started") if self.calls.exists() else 0


async def _hung_remote(
    scheduler_env: SchedulerFixture, tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> _HungRemote:
    base = scheduler_env.settings
    settings = base.model_copy(
        update={
            "orchestration": base.orchestration.model_copy(
                update={"max_parallel": _HANDS_OFF_TASKS}
            )
        }
    )
    runner = SubprocessRunner()
    remote_dir = tmp_path_factory.mktemp("remote") / "origin.git"
    await add_bare_remote(tmp_path, remote_dir, settings.repo.remote)
    hang_dir = tmp_path_factory.mktemp("hang")
    calls, release, hang = hang_dir / "calls", hang_dir / "go", hang_dir / "upload-pack"
    os.mkfifo(release)
    hang.write_text(_HUNG_UPLOAD_PACK.format(calls=calls, release=shlex.quote(str(release))))
    hang.chmod(EXECUTABLE_MODE)
    uploadpack = ["git", "config", f"remote.{settings.repo.remote}.uploadpack", str(hang)]
    await runner.run(uploadpack, timeout=MEDIUM_WAIT_SECONDS, cwd=tmp_path, check=True)
    roots = task_root_builder(
        runner,
        tmp_path,
        settings.repo,
        FetchedBaseRefs(
            Git(runner, command_timeout=GIT_COMMAND_TIMEOUT),
            tmp_path,
            settings.repo.remote,
            fetch_timeout=_REAL_SECONDS * 3,
        ),
    )
    return _HungRemote(settings, roots, calls, release)


async def _tick(scheduler: Scheduler, clock: FakeClock, settings: Settings) -> ReadySchedule:
    tick = asyncio.create_task(scheduler.run_once())

    def finished() -> bool:
        return tick.done()

    await wait_until_advancing(
        clock, finished, _REAL_SECONDS, step_seconds=settings.orchestration.dispatch_budget_seconds
    )
    return tick.result()


async def test_a_hung_remote_cannot_hold_the_tick_for_every_ready_task(
    scheduler_env: SchedulerFixture,
    connection: Connection,
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    remote = await _hung_remote(scheduler_env, tmp_path, tmp_path_factory)
    scheduler = _scheduler(dataclasses.replace(scheduler_env, roots=remote.roots), remote.settings)
    clock = scheduler_env.clock
    assert isinstance(clock, FakeClock)
    tasks = [
        _submit(connection, clock, title=f"ready {number}") for number in range(_HANDS_OFF_TASKS)
    ]
    writer: int | None = None
    try:
        first = asyncio.create_task(scheduler.run_once())
        writer = await fifo_writer(remote.release, _REAL_SECONDS)
        await wait_until(clock.has_pending_sleep, _REAL_SECONDS)
        clock.advance(remote.settings.orchestration.dispatch_budget_seconds)
        assert (await asyncio.wait_for(first, _REAL_SECONDS)).dispatches == ()
        assert (await asyncio.wait_for(scheduler.run_once(), _REAL_SECONDS)).dispatches == ()
        await wait_until(remote.calls.exists, _REAL_SECONDS)
        assert remote.fetches() == 1
        assert [_status(scheduler_env, task) for task in tasks] == [Status.QUEUED] * len(tasks)

        release_fifo(writer, remote.release)
        writer = None

        landed = await _land_every_dispatch(scheduler, _HANDS_OFF_TASKS)
        assert sorted(landed) == sorted(task.id for task in tasks)
    finally:
        if writer is not None:
            release_fifo(writer, remote.release)
        await scheduler.shutdown()


async def test_the_build_of_a_task_cancelled_meanwhile_does_not_hold_up_the_next_one(
    scheduler_env: SchedulerFixture,
    connection: Connection,
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    remote = await _hung_remote(scheduler_env, tmp_path, tmp_path_factory)
    scheduler = _scheduler(dataclasses.replace(scheduler_env, roots=remote.roots), remote.settings)
    clock = scheduler_env.clock
    assert isinstance(clock, FakeClock)
    first, _second = (_submit(connection, clock, title=f"ready {n}") for n in range(2))
    writer: int | None = None
    try:
        initial_tick = asyncio.create_task(scheduler.run_once())
        writer = await fifo_writer(remote.release, _REAL_SECONDS)
        await wait_until(clock.has_pending_sleep, _REAL_SECONDS)
        clock.advance(remote.settings.orchestration.dispatch_budget_seconds)
        await asyncio.wait_for(initial_tick, _REAL_SECONDS)
        await wait_until(lambda: remote.fetches() == 1, _REAL_SECONDS)
        cancelled = scheduler_env.tasks.require(first.id)
        scheduler_env.tasks.apply_transition(
            first.id, Transition.stay(cancelled, step=None, status=Status.CANCELLED)
        )

        async def next_build_started() -> bool:
            await _tick(scheduler, clock, remote.settings)
            return remote.fetches() == 2

        await wait_until(next_build_started, _REAL_SECONDS)
    finally:
        if writer is not None:
            release_fifo(writer, remote.release)
        await scheduler.shutdown()


_SLOW_COPY = """#!/bin/sh
case "$1" in
*.shikiru-cow-probe-*) exec cp "$1" "$2";;
esac
cat {release} > /dev/null
exec cp -R "$1" "$2"
"""


async def test_a_slow_cache_copy_runs_beside_the_tick_and_completes_the_dispatch(
    scheduler_env: SchedulerFixture,
    connection: Connection,
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    runner = SubprocessRunner()
    (tmp_path / ".gitignore").write_text("node_modules/\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "pkg.js").write_text("cached\n")
    await commit_all(runner, tmp_path, "ignore the cache")
    release = tmp_path_factory.mktemp("copy") / "go"
    os.mkfifo(release)
    slow = release.parent / "slow-cp"
    slow.write_text(_SLOW_COPY.format(release=shlex.quote(str(release))))
    slow.chmod(EXECUTABLE_MODE)
    settings = scheduler_env.settings
    repo = settings.repo.model_copy(update={"caches": ["node_modules"]})
    roots = TaskRootBuilder(
        Git(runner, command_timeout=GIT_COMMAND_TIMEOUT),
        tmp_path,
        repo,
        CacheCloner(runner, cow_args=(str(slow),), copy_timeout=CACHE_COPY_TIMEOUT),
        LocalBaseRefs(),
        trackers=(),
    )
    scheduler = _scheduler(dataclasses.replace(scheduler_env, roots=roots), settings)
    clock = scheduler_env.clock
    assert isinstance(clock, FakeClock)
    task = _submit(connection, clock, title="warm cache")
    writer: int | None = None
    try:
        first = asyncio.create_task(scheduler.run_once())
        writer = await fifo_writer(release, _REAL_SECONDS)
        await wait_until(clock.has_pending_sleep, _REAL_SECONDS)
        clock.advance(settings.orchestration.dispatch_budget_seconds)
        assert (await asyncio.wait_for(first, _REAL_SECONDS)).dispatches == ()
        assert _status(scheduler_env, task) is Status.QUEUED

        release_fifo(writer, release)
        writer = None

        assert await _land_every_dispatch(scheduler, 1) == [task.id]
        assert (task_root(tmp_path, task.id) / "node_modules" / "pkg.js").read_text() == "cached\n"
    finally:
        if writer is not None:
            release_fifo(writer, release)
        await scheduler.shutdown()


_EXHAUSTED_WEEKLY_RESET = timedelta(days=3)
_AVAILABLE_AFTER_BLOCK = timedelta(minutes=10)
_RESTART_AGENT = "fake"
_INDEPENDENT_AGENT = "fake2"


@dataclass
class QuotaProbeFixture:
    env: SchedulerFixture
    source: Path
    settings: Settings
    blocked_at: datetime
    reconciler: QuotaReconciler | None = None

    def _reading(self, *, five_hour: float, weekly: float, observed_at: datetime) -> str:
        return json.dumps(
            {
                "observed_at": observed_at.isoformat(),
                "windows": [
                    {"name": "5h", "used_pct": five_hour, "resets_at": None},
                    {
                        "name": "7d",
                        "used_pct": weekly,
                        "resets_at": (self.blocked_at + _EXHAUSTED_WEEKLY_RESET).isoformat(),
                    },
                ],
            }
        )

    def publish(self, *, five_hour: float, weekly: float, observed_at: datetime) -> None:
        reading = self._reading(five_hour=five_hour, weekly=weekly, observed_at=observed_at)
        self.source.write_text(reading, encoding="utf-8")

    def hold_source(self) -> None:
        os.mkfifo(self.source)

    async def reader_waiting(self) -> int:
        """Return a writer on the held source once a probe is blocked reading it."""
        opened: list[int] = []

        def connected() -> bool:
            try:
                opened.append(os.open(self.source, os.O_WRONLY | os.O_NONBLOCK))
            except OSError as error:
                if error.errno != errno.ENXIO:
                    raise
                return False
            return True

        assert isinstance(self.env.clock, FakeClock)
        await wait_until(self.env.clock.has_pending_sleep, _REAL_SECONDS)
        await wait_until(connected, _REAL_SECONDS)
        return opened[0]

    def answer(
        self, writer: int, *, five_hour: float, weekly: float, observed_at: datetime
    ) -> None:
        reading = self._reading(five_hour=five_hour, weekly=weekly, observed_at=observed_at)
        os.write(writer, reading.encode())
        os.close(writer)

    @staticmethod
    def reading_threads() -> int:
        return sum(
            thread.name.startswith(THREAD_NAME_PREFIX) and thread.is_alive()
            for thread in threading.enumerate()
        )

    async def source_drained(self) -> None:
        await wait_until(lambda: self.reading_threads() == 0, _REAL_SECONDS)

    def free_blocked_readers(self) -> None:
        while True:
            try:
                os.close(os.open(self.source, os.O_WRONLY | os.O_NONBLOCK))
            except OSError as error:
                if error.errno != errno.ENXIO:
                    raise
                return

    def advance(self, seconds: float) -> None:
        assert isinstance(self.env.clock, FakeClock)
        self.env.clock.advance(seconds)

    def restart(self) -> Scheduler:
        reconciler = build_reconciler(
            quotas=self.env.quotas,
            agents=AgentPool(
                build_registry().create, SubprocessRunner(), self.settings, self.env.clock
            ),
            events=self.env.events,
            clock=self.env.clock,
            settings=self.settings,
        )
        self.reconciler = reconciler
        return _scheduler(dataclasses.replace(self.env, reconciler=reconciler), self.settings)

    async def settle(self) -> None:
        assert self.reconciler is not None
        reconciler = self.reconciler
        await wait_until(lambda: not reconciler.probing, _REAL_SECONDS)

    @property
    def refresh_interval(self) -> float:
        return self.settings.orchestration.quota_refresh.interval_seconds


@pytest.fixture
def quota_probe(scheduler_env: SchedulerFixture, tmp_path: Path) -> Iterator[QuotaProbeFixture]:
    source = tmp_path / "quota-source.json"
    settings = scheduler_env.settings.model_copy(
        update={
            "agents": {
                **scheduler_env.settings.agents,
                "fake": AgentConfig(options={"quota_file": str(source)}),
                _INDEPENDENT_AGENT: AgentConfig(),
            }
        }
    )
    clock = scheduler_env.clock
    blocked_at = clock.now()
    scheduler_env.quotas.record(
        _RESTART_AGENT, QuotaWindow("5h", 11.0, blocked_at + timedelta(hours=4), blocked_at)
    )
    scheduler_env.quotas.record(
        _RESTART_AGENT,
        QuotaWindow("7d", 100.0, blocked_at + _EXHAUSTED_WEEKLY_RESET, blocked_at),
    )
    scheduler_env.quotas.block(
        _RESTART_AGENT, blocked_at + _EXHAUSTED_WEEKLY_RESET, "quota", blocked_at
    )
    probe = QuotaProbeFixture(scheduler_env, source, settings, blocked_at)
    yield probe
    if source.exists() and stat.S_ISFIFO(source.stat().st_mode):
        probe.free_blocked_readers()


async def test_a_newer_available_reading_dispatches_a_task_queued_behind_a_future_block(
    quota_probe: QuotaProbeFixture, connection: Connection
) -> None:
    env = quota_probe.env
    task = _submit(connection, env.clock, title="blocked behind a stale deadline")
    scheduler = quota_probe.restart()
    assert (await scheduler.run_once()).dispatches == ()
    await quota_probe.settle()
    assert env.tasks.require(task.id).wait_reason == "quota"

    quota_probe.advance(quota_probe.refresh_interval + 1)
    quota_probe.publish(five_hour=2, weekly=0, observed_at=env.clock.now())
    assert (await scheduler.run_once()).dispatches == ()
    await quota_probe.settle()
    schedule = await scheduler.run_once()

    assert [dispatch.task_id for dispatch in schedule.dispatches] == [task.id]
    running = env.tasks.require(task.id)
    assert running.status is Status.RUNNING
    assert running.wait_reason is None
    assert env.quotas.active_blocks(env.clock.now()) == []
    [cleared] = env.events.since(0, kinds=[EventKind.QUOTA_CLEARED])
    assert cleared.data["agent"] == _RESTART_AGENT
    assert {window.name: window.used_pct for window in env.quotas.windows_for("fake")} == {
        "5h": 2.0,
        "7d": 0.0,
    }


@pytest.mark.parametrize(
    "scheduler_env",
    [_quota_settings(Role.IMPLEMENTER, [_FAKE_RUNG, _FALLBACK_RUNG])],
    indirect=True,
)
@pytest.mark.parametrize(
    ("five_hour", "weekly", "expected_rung"),
    [
        pytest.param(2, 0, _FAKE_RUNG, id="block-released"),
        pytest.param(100, 100, _FALLBACK_RUNG, id="block-confirmed"),
    ],
)
async def test_a_requeued_task_keeps_its_rung_until_the_demanded_probe_finishes(
    quota_probe: QuotaProbeFixture,
    connection: Connection,
    five_hour: float,
    weekly: float,
    expected_rung: Rung,
) -> None:
    env = quota_probe.env
    settings = quota_probe.settings
    task = _submit(connection, env.clock, title="requeued behind a stale block")
    env.tasks.set_requeued(task.id)
    quota_probe.advance(1)
    quota_probe.publish(five_hour=five_hour, weekly=weekly, observed_at=env.clock.now())
    scheduler = quota_probe.restart()

    held = await scheduler.run_once()

    assert held.dispatches == ()
    assert pick_rung(env.tasks.require(task.id), Role.IMPLEMENTER, settings) == _FAKE_RUNG
    await quota_probe.settle()

    schedule = await scheduler.run_once()

    assert [dispatch.task_id for dispatch in schedule.dispatches] == [task.id]
    assert pick_rung(env.tasks.require(task.id), Role.IMPLEMENTER, settings) == expected_rung


@pytest.mark.parametrize(
    "scheduler_env",
    [_quota_settings(Role.IMPLEMENTER, [_FAKE_RUNG, _FALLBACK_RUNG])],
    indirect=True,
)
async def test_routine_refreshes_do_not_hold_back_fallback_after_a_probe_confirms_exhaustion(
    quota_probe: QuotaProbeFixture, connection: Connection
) -> None:
    env = quota_probe.env
    settings = quota_probe.settings
    task = _submit(connection, env.clock, title="exhausted, then fallback")
    env.tasks.set_requeued(task.id)
    quota_probe.advance(1)
    quota_probe.publish(five_hour=100, weekly=100, observed_at=env.clock.now())
    scheduler = quota_probe.restart()
    assert (await scheduler.run_once()).dispatches == ()
    await quota_probe.settle()
    assert pick_rung(env.tasks.require(task.id), Role.IMPLEMENTER, settings) == _FAKE_RUNG

    quota_probe.advance(quota_probe.refresh_interval)
    quota_probe.publish(five_hour=100, weekly=100, observed_at=env.clock.now())
    schedule = await scheduler.run_once()
    await quota_probe.settle()

    assert [dispatch.task_id for dispatch in schedule.dispatches] == [task.id]
    assert pick_rung(env.tasks.require(task.id), Role.IMPLEMENTER, settings) == _FALLBACK_RUNG


@pytest.mark.parametrize(
    "scheduler_env",
    [_quota_settings(Role.IMPLEMENTER, [_FAKE_RUNG, _FALLBACK_RUNG])],
    indirect=True,
)
async def test_a_requeue_during_a_routine_probe_waits_for_a_probe_started_after_the_requeue(
    quota_probe: QuotaProbeFixture, connection: Connection
) -> None:
    env = quota_probe.env
    settings = quota_probe.settings
    task = _submit(connection, env.clock, title="requeued while a routine probe runs")
    quota_probe.advance(1)
    quota_probe.publish(five_hour=100, weekly=100, observed_at=env.clock.now())
    scheduler = quota_probe.restart()
    assert quota_probe.reconciler is not None
    quota_probe.reconciler.request_refresh(_RESTART_AGENT)
    quota_probe.advance(1)
    env.tasks.set_requeued(task.id)

    held = await scheduler.run_once()

    assert held.dispatches == ()
    assert pick_rung(env.tasks.require(task.id), Role.IMPLEMENTER, settings) == _FAKE_RUNG
    await quota_probe.settle()

    still_held = await scheduler.run_once()

    assert still_held.dispatches == ()
    await quota_probe.settle()

    schedule = await scheduler.run_once()

    assert [dispatch.task_id for dispatch in schedule.dispatches] == [task.id]
    assert pick_rung(env.tasks.require(task.id), Role.IMPLEMENTER, settings) == _FALLBACK_RUNG


async def test_a_block_persisted_before_a_restart_is_revalidated_on_the_first_tick(
    quota_probe: QuotaProbeFixture, connection: Connection
) -> None:
    env = quota_probe.env
    task = _submit(connection, env.clock, title="queued across a restart")
    quota_probe.advance(_AVAILABLE_AFTER_BLOCK.total_seconds())
    quota_probe.publish(five_hour=3, weekly=0, observed_at=env.clock.now())

    scheduler = quota_probe.restart()
    await scheduler.run_once()
    await quota_probe.settle()
    schedule = await scheduler.run_once()

    assert [dispatch.task_id for dispatch in schedule.dispatches] == [task.id]
    assert env.quotas.block_for("fake") is None


@pytest.mark.parametrize(
    ("five_hour", "weekly", "age"),
    [
        pytest.param(2, 0, -timedelta(minutes=1), id="sample-older-than-the-block"),
        pytest.param(2, 100, _AVAILABLE_AFTER_BLOCK, id="weekly-still-exhausted"),
        pytest.param(100, 0, _AVAILABLE_AFTER_BLOCK, id="five-hour-exhausted"),
    ],
)
async def test_readings_that_do_not_prove_availability_keep_the_block(
    quota_probe: QuotaProbeFixture,
    connection: Connection,
    five_hour: float,
    weekly: float,
    age: timedelta,
) -> None:
    env = quota_probe.env
    task = _submit(connection, env.clock, title="still blocked")
    scheduler = quota_probe.restart()
    quota_probe.publish(
        five_hour=five_hour, weekly=weekly, observed_at=quota_probe.blocked_at + age
    )
    for _ in range(3):
        quota_probe.advance(quota_probe.refresh_interval + 1)

        schedule = await scheduler.run_once()
        await quota_probe.settle()

        assert schedule.dispatches == ()
    assert env.tasks.require(task.id).wait_reason == "quota"
    assert env.quotas.block_for("fake") is not None
    assert not env.events.since(0, kinds=[EventKind.QUOTA_CLEARED])


async def test_the_passage_of_the_refresh_interval_alone_never_lifts_a_block(
    quota_probe: QuotaProbeFixture, connection: Connection
) -> None:
    env = quota_probe.env
    _submit(connection, env.clock, title="no new evidence")
    scheduler = quota_probe.restart()

    for _ in range(5):
        quota_probe.advance(quota_probe.refresh_interval * 2)
        assert (await scheduler.run_once()).dispatches == ()
        await quota_probe.settle()

    assert env.quotas.block_for("fake") is not None


async def test_an_unreadable_quota_source_is_reported_once_per_interval_and_keeps_the_block(
    quota_probe: QuotaProbeFixture, connection: Connection
) -> None:
    env = quota_probe.env
    task = _submit(connection, env.clock, title="probe cannot read")
    scheduler = quota_probe.restart()

    for _ in range(3):
        assert (await scheduler.run_once()).dispatches == ()
        await quota_probe.settle()

    [fault] = env.events.since(0, kinds=[EventKind.ENGINE_FAULT])
    assert fault.data["scope"] == f"{QUOTA_REFRESH_SCOPE} fake"
    assert env.quotas.block_for("fake") is not None
    assert env.tasks.require(task.id).wait_reason == "quota"

    quota_probe.advance(quota_probe.refresh_interval + 1)
    quota_probe.publish(five_hour=1, weekly=0, observed_at=env.clock.now())

    await scheduler.run_once()
    await quota_probe.settle()
    schedule = await scheduler.run_once()

    assert [dispatch.task_id for dispatch in schedule.dispatches] == [task.id]
    assert len(env.events.since(0, kinds=[EventKind.ENGINE_RECOVERED])) == 1


async def test_an_operator_retry_revalidates_inside_the_interval_without_a_second_dispatch(
    quota_probe: QuotaProbeFixture, connection: Connection
) -> None:
    env = quota_probe.env
    task = _submit(connection, env.clock, title="retried while blocked")
    scheduler = quota_probe.restart()
    quota_probe.publish(five_hour=2, weekly=100, observed_at=quota_probe.blocked_at)
    assert (await scheduler.run_once()).dispatches == ()
    await quota_probe.settle()

    quota_probe.advance(1)
    quota_probe.publish(five_hour=2, weekly=0, observed_at=env.clock.now())
    assert (await scheduler.run_once()).dispatches == ()
    await quota_probe.settle()

    quota_probe.advance(1)
    env.tasks.set_requeued(task.id)
    await scheduler.run_once()
    await quota_probe.settle()
    retried = await scheduler.run_once()
    again = await scheduler.run_once()

    assert [dispatch.task_id for dispatch in retried.dispatches] == [task.id]
    assert again.dispatches == ()
    assert len(env.events.since(0, task_id=task.id, kinds=[EventKind.TASK_STARTED])) == 1
    assert env.quotas.block_for("fake") is None


def _independent_task(connection: Connection, clock: Clock) -> Task:
    free = Rung(agent=_INDEPENDENT_AGENT, model="fake-1", effort=None)
    return seed_task(connection, clock, title="independent", leader=free, implementer=free)


async def test_a_pending_probe_holds_back_neither_the_tick_nor_an_independent_dispatch(
    quota_probe: QuotaProbeFixture, connection: Connection
) -> None:
    env = quota_probe.env
    blocked = _submit(connection, env.clock, title="behind the block")
    independent = _independent_task(connection, env.clock)
    quota_probe.hold_source()
    scheduler = quota_probe.restart()
    writer = None
    try:
        schedule = await asyncio.wait_for(scheduler.run_once(), _REAL_SECONDS)
        writer = await quota_probe.reader_waiting()

        assert [dispatch.task_id for dispatch in schedule.dispatches] == [independent.id]
        assert env.tasks.require(blocked.id).wait_reason == "quota"

        quota_probe.advance(1)
        quota_probe.answer(writer, five_hour=2, weekly=0, observed_at=env.clock.now())
        writer = None
        await quota_probe.settle()
        released = await scheduler.run_once()

        assert [dispatch.task_id for dispatch in released.dispatches] == [blocked.id]
        assert env.quotas.block_for("fake") is None
    finally:
        if writer is not None:
            os.close(writer)
        await scheduler.shutdown()


async def test_a_forced_retry_never_starts_a_second_probe_of_the_same_block(
    quota_probe: QuotaProbeFixture, connection: Connection
) -> None:
    env = quota_probe.env
    task = _submit(connection, env.clock, title="retried while probing")
    quota_probe.hold_source()
    scheduler = quota_probe.restart()
    writer = None
    try:
        await scheduler.run_once()
        writer = await quota_probe.reader_waiting()
        quota_probe.advance(1)
        env.tasks.set_requeued(task.id)

        await scheduler.run_once()
        quota_probe.answer(writer, five_hour=2, weekly=0, observed_at=env.clock.now())
        writer = None
        await quota_probe.settle()

        assert env.quotas.block_for("fake") is None
    finally:
        if writer is not None:
            os.close(writer)
        await scheduler.shutdown()


async def test_a_probe_that_never_answers_is_reported_at_its_timeout_and_the_next_one_runs(
    quota_probe: QuotaProbeFixture, connection: Connection
) -> None:
    env = quota_probe.env
    task = _submit(connection, env.clock, title="probe hangs")
    quota_probe.hold_source()
    scheduler = quota_probe.restart()
    hung = None
    writer = None
    try:
        await scheduler.run_once()
        hung = await quota_probe.reader_waiting()
        quota_probe.advance(quota_probe.settings.orchestration.quota_refresh.timeout_seconds)
        await quota_probe.settle()

        [fault] = env.events.since(0, kinds=[EventKind.ENGINE_FAULT])
        assert fault.data["scope"] == f"{QUOTA_REFRESH_SCOPE} fake"
        assert env.quotas.block_for("fake") is not None
        os.close(hung)
        hung = None
        await quota_probe.source_drained()

        quota_probe.advance(quota_probe.refresh_interval)
        await scheduler.run_once()
        writer = await quota_probe.reader_waiting()
        quota_probe.answer(writer, five_hour=2, weekly=0, observed_at=env.clock.now())
        writer = None
        await quota_probe.settle()

        assert [dispatch.task_id for dispatch in (await scheduler.run_once()).dispatches] == [
            task.id
        ]
        assert len(env.events.since(0, kinds=[EventKind.ENGINE_RECOVERED])) == 1
    finally:
        for descriptor in (hung, writer):
            if descriptor is not None:
                os.close(descriptor)
        await scheduler.shutdown()


async def test_a_source_that_stays_pending_never_gains_a_second_reader_and_shutdown_leaves_it(
    quota_probe: QuotaProbeFixture, connection: Connection
) -> None:
    env = quota_probe.env
    _submit(connection, env.clock, title="source never answers")
    quota_probe.hold_source()
    scheduler = quota_probe.restart()
    refresh = quota_probe.settings.orchestration.quota_refresh
    held = None
    try:
        await scheduler.run_once()
        held = await quota_probe.reader_waiting()
        for _ in range(3):
            quota_probe.advance(refresh.timeout_seconds)
            await quota_probe.settle()
            quota_probe.advance(refresh.interval_seconds)
            await scheduler.run_once()

        assert quota_probe.reading_threads() == 1
        assert env.events.since(0, kinds=[EventKind.ENGINE_FAULT])

        await asyncio.wait_for(scheduler.shutdown(), _REAL_SECONDS)

        assert quota_probe.reading_threads() == 1
        assert env.quotas.block_for("fake") is not None
    finally:
        if held is not None:
            os.close(held)
        quota_probe.free_blocked_readers()
        await quota_probe.source_drained()
        await scheduler.shutdown()


async def test_a_probe_of_a_block_since_replaced_cannot_release_the_newer_block(
    quota_probe: QuotaProbeFixture, connection: Connection
) -> None:
    env = quota_probe.env
    _submit(connection, env.clock, title="blocked again")
    quota_probe.hold_source()
    scheduler = quota_probe.restart()
    writer = None
    try:
        await scheduler.run_once()
        writer = await quota_probe.reader_waiting()
        available_at = env.clock.now() + timedelta(seconds=1)
        quota_probe.advance(2)
        newer = env.clock.now()
        env.quotas.block("fake", newer + _EXHAUSTED_WEEKLY_RESET, "quota", newer)

        await scheduler.run_once()
        await quota_probe.settle()
        quota_probe.answer(writer, five_hour=2, weekly=0, observed_at=available_at)
        writer = None
        await scheduler.run_once()

        block = env.quotas.block_for("fake")
        assert block is not None
        assert block.blocked_at == newer
    finally:
        if writer is not None:
            os.close(writer)
        await scheduler.shutdown()


async def test_shutdown_ends_a_probe_that_is_still_waiting(
    quota_probe: QuotaProbeFixture, connection: Connection
) -> None:
    env = quota_probe.env
    _submit(connection, env.clock, title="engine stops while probing")
    quota_probe.hold_source()
    scheduler = quota_probe.restart()
    writer = None
    try:
        await scheduler.run_once()
        writer = await quota_probe.reader_waiting()

        await asyncio.wait_for(scheduler.shutdown(), _REAL_SECONDS)

        assert quota_probe.reconciler is not None
        assert not quota_probe.reconciler.probing
        assert env.quotas.block_for("fake") is not None
    finally:
        if writer is not None:
            os.close(writer)
