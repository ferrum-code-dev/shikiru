"""Scheduler gates and dispatch behaviour against real SQLite and tmux."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from shikiru.agents.contracts import Rung
from shikiru.config.settings import AgentConfig, LayoutSplit, PaneLeaf, ProjectConfig
from shikiru.domain.commands import Submit
from shikiru.domain.events import EventKind
from shikiru.domain.reports import NewTask
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import EscalationReason, Hold, Status, StatusReason, Step, TaskOrigin
from shikiru.engine.dependency import DependencyEscalator
from shikiru.engine.escalations import DispatchFailureEscalator, EscalationRaiser
from shikiru.engine.layout_guard import LayoutGuard
from shikiru.engine.scheduler import FETCH_FAULT_SCOPE, MAX_DISPATCH_FAILURES, Scheduler
from shikiru.engine.titles import TaskTitles
from shikiru.paths import task_root
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
from shikiru.tmux.decorator import PaneDecorator
from shikiru.tmux.layout import LayoutBuilder
from shikiru.tmux.session import SessionView, session_name
from shikiru.workspace.base_refs import FetchedBaseRefs
from shikiru.workspace.git import Git
from tests.support.clock import FakeClock
from tests.support.engine import (
    build_decorator,
    build_evidence,
    build_fault_reporter,
    build_tmux,
    build_writer,
)
from tests.support.factories import CPU_UNCAPPED_PCT, TASK_CREATED_AT, make_settings
from tests.support.flow import init_project_repo
from tests.support.git_repo import commit_all
from tests.support.github import add_bare_remote
from tests.support.workspace import task_root_builder

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

    from shikiru.clock import Clock
    from shikiru.config.settings import Settings
    from shikiru.domain.tasks import Task
    from shikiru.workspace.roots import TaskRootBuilder
    from tests.support.store import Connection
    from tests.support.tmux import TmuxServer

_FAKE_RUNG = Rung(agent="fake", model="fake-1", effort=None)
_SAMPLED_CPU_PERCENT = 50.0
_TRACKER = "TODO.md"
_IMPLEMENTATION = "impl.py"
_LAYOUT = LayoutSplit(
    split="h",
    panes=[
        PaneLeaf(pane="leader"),
        PaneLeaf(pane="implementer"),
        PaneLeaf(pane="system"),
        PaneLeaf(pane="shell"),
    ],
)


@dataclass
class SchedulerFixture:
    scheduler: Scheduler
    settings: Settings
    tasks: TaskStore
    quotas: QuotaStore
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
    dashboard = settings.dashboard.model_copy(update={"layout": _LAYOUT})
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
        resources=ResourceSampler(environment.clock, settings.orchestration.resources),
        clock=environment.clock,
        layout=environment.layout,
        roots=environment.roots,
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
) -> SchedulerFixture:
    def fixed_cpu_percent(interval: float | None = None) -> float:
        del interval
        return _SAMPLED_CPU_PERCENT

    monkeypatch.setattr(resources_module.psutil, "cpu_percent", fixed_cpu_percent)
    await init_project_repo(tmp_path)
    clock = FakeClock(TASK_CREATED_AT)
    settings = _settings()
    tasks = TaskStore(connection, clock)
    quotas = QuotaStore(connection)
    events = EventLog(connection, clock)
    tmux = build_tmux(tmux_server)
    layout = LayoutGuard(
        tmux=tmux,
        view=SessionView(tmux),
        builder=LayoutBuilder(tmux),
        decorator=PaneDecorator(tmux),
        session_target=exact_target(session_name("test")),
        tasks=tasks,
        settings=settings,
        project_root=tmp_path,
        events=events,
        runs=RunStore(connection, clock),
        commands=CommandQueue(connection, clock),
        faults=build_fault_reporter(events, clock, settings),
    )
    roots = task_root_builder(
        SubprocessRunner(), tmp_path, settings.repo, trackers=settings.project.trackers
    )
    await SessionView(tmux).ensure("test", tmp_path, home_command=("/bin/sh",))

    titles = TaskTitles(build_decorator(tmux), RunStore(connection, clock), settings)
    raiser = EscalationRaiser(
        EscalationStore(connection, clock),
        events,
        RunStore(connection, clock),
        tmp_path,
        atomically=lambda: transaction(connection),
        titles=titles,
        evidence=build_evidence(tmp_path),
    )
    writer = build_writer(connection, clock, tmux=tmux)
    dependency_callback = DependencyEscalator(writer, raiser)
    dispatch_failure_callback = DispatchFailureEscalator(writer, raiser)
    scheduler = Scheduler(
        tasks=tasks,
        settings=settings,
        quotas=quotas,
        resources=ResourceSampler(clock, settings.orchestration.resources),
        clock=clock,
        layout=layout,
        roots=roots,
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
    scheduler_env.quotas.block("fake", scheduler_env.clock.now() + timedelta(hours=1), "test quota")

    schedule = await scheduler_env.scheduler.run_once()

    assert schedule.dispatches == ()
    waiting = scheduler_env.tasks.get(task.id)
    assert waiting is not None
    assert waiting.wait_reason == "quota"


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
    await runner.run([*gone, str(remote_dir / "gone")], timeout=10, cwd=tmp_path, check=True)
    roots = task_root_builder(
        runner,
        tmp_path,
        settings.repo,
        FetchedBaseRefs(
            Git(runner),
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

    await runner.run([*gone, str(remote_dir)], timeout=10, cwd=tmp_path, check=True)
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
    await Git(SubprocessRunner()).merge_ff_only(tmp_path, landed.repo.branch)
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
    assert await Git(SubprocessRunner()).status_porcelain(root) == []
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
