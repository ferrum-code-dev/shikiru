"""Real SQLite, git, tmux and fake CLI scaffolding for engine integration tests."""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import uuid
from typing import TYPE_CHECKING, Any

import pytest
from fake_agent.agent import create as create_fake_agent
from fake_agent.scripted import SCRIPT_ENV_VAR, role_script_env_var

from shikiru.agents.contracts import Rung
from shikiru.agents.registry import AgentRegistry
from shikiru.config.settings import AgentConfig, LayoutSplit, PaneLeaf, ProjectConfig
from shikiru.domain.commands import COMMAND_POLL_SECONDS, Submit
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import Role, Status, Step, Task
from shikiru.engine.agents import AgentPool
from shikiru.engine.escalations import EscalationRaiser
from shikiru.engine.evidence import EvidenceCollector
from shikiru.engine.fault_reporter import FaultReporter
from shikiru.engine.history import ReportHistory
from shikiru.engine.monitor import TurnMonitor
from shikiru.engine.quota_reconciler import QuotaReconciler
from shikiru.engine.quota_schedule import QuotaSchedule
from shikiru.engine.quota_waits import QuotaWaits
from shikiru.engine.sessions import SessionManager
from shikiru.engine.task_facts import TaskFacts
from shikiru.engine.titles import TaskTitles
from shikiru.engine.transitions import TransitionWriter
from shikiru.engine.turn_processes import TurnProcesses
from shikiru.engine.turns import TurnRunner
from shikiru.helper.helper import Helper, HelperEntry
from shikiru.process import SubprocessRunner
from shikiru.prompts.renderer import PromptRenderer
from shikiru.store.commands import CommandQueue, CommandStatus
from shikiru.store.database import transaction
from shikiru.store.escalations import EscalationStore
from shikiru.store.events import EventLog
from shikiru.store.quotas import QuotaStore
from shikiru.store.runs import RunStore
from shikiru.store.tasks import TaskStore
from shikiru.tmux.client import Tmux
from shikiru.tmux.decorator import PaneDecorator
from shikiru.tmux.session import SessionView
from shikiru.workspace.facts import GitFacts
from shikiru.workspace.git import Git
from shikiru.workspace.messages import OperatorMessages
from shikiru.workspace.roots import task_root
from tests.support.clock import FakeClock
from tests.support.factories import TASK_CREATED_AT, make_settings
from tests.support.git_repo import GIT_COMMAND_TIMEOUT
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.clock import Clock
    from shikiru.config.settings import Settings
    from shikiru.store.commands import CommandOutcome, QueuedCommand
    from tests.support.store import Connection
    from tests.support.tmux import TmuxServer

FAKE_AGENT_NAME = "fake"
FAKE_AGENT_ALIAS = "fake2"
FAKE_RUNG = Rung(agent=FAKE_AGENT_NAME, model="fake-1", effort=None)
_SESSION_NAME = "test"
_PANE_SUFFIX_LENGTH = 8
_COMMAND_WAIT_SECONDS = 10
ENGINE_LAYOUT = LayoutSplit(
    split="h",
    panes=[
        PaneLeaf(pane="leader"),
        PaneLeaf(pane="implementer"),
        PaneLeaf(pane="system"),
        PaneLeaf(pane="shell"),
    ],
)


@dataclasses.dataclass
class TurnSystem:
    sessions: SessionManager
    turns: TurnRunner
    events: EventLog
    quotas: QuotaStore
    titles: TaskTitles
    schedule: QuotaSchedule


def build_registry() -> AgentRegistry:
    """An `AgentRegistry` with the fake plugin registered under both test names."""
    registry = AgentRegistry()
    registry.register(FAKE_AGENT_NAME, create_fake_agent)
    registry.register(FAKE_AGENT_ALIAS, create_fake_agent)
    return registry


def fake_engine_settings() -> Settings:
    """Settings whose active role uses the fake plugin."""
    return make_settings(implementer_ladder=[FAKE_RUNG], agents={FAKE_AGENT_NAME: AgentConfig()})


def build_tmux(tmux_server: TmuxServer) -> Tmux:
    """A `Tmux` client scoped to the fixture's private socket."""
    return Tmux(tmux_server.runner, socket=tmux_server.socket_name, env=tmux_server.env)


def build_evidence(project_root: Path) -> EvidenceCollector:
    """An `EvidenceCollector` reading the reports, git facts and turn tails under `project_root`."""
    return EvidenceCollector(
        history=ReportHistory(project_root),
        facts=TaskFacts(
            git_facts=GitFacts(Git(SubprocessRunner(), command_timeout=GIT_COMMAND_TIMEOUT)),
            project_root=project_root,
            settings=fake_engine_settings(),
        ),
        project_root=project_root,
    )


def build_raiser(
    connection: Connection, clock: Clock, project_root: Path, *, tmux: Tmux | None = None
) -> EscalationRaiser:
    """An `EscalationRaiser` over `connection`, refreshing titles through `tmux` when given."""
    runs = RunStore(connection, clock)
    decorator = PaneDecorator(tmux if tmux is not None else Tmux(SubprocessRunner()))
    return EscalationRaiser(
        EscalationStore(connection, clock),
        EventLog(connection, clock),
        runs,
        project_root,
        atomically=lambda: transaction(connection),
        titles=build_titles(
            connection=connection,
            clock=clock,
            decorator=decorator,
            settings=fake_engine_settings(),
            project_root=project_root,
        ),
        evidence=build_evidence(project_root),
        settings=fake_engine_settings(),
    )


def build_titles(
    *,
    connection: Connection,
    clock: Clock,
    decorator: PaneDecorator,
    settings: Settings,
    project_root: Path,
    schedule: QuotaSchedule | None = None,
) -> TaskTitles:
    """A `TaskTitles` over `connection`, sharing `schedule` with the reconciler when given."""
    return TaskTitles(
        decorator,
        RunStore(connection, clock),
        TaskStore(connection, clock),
        settings,
        waits=QuotaWaits(
            quotas=QuotaStore(connection),
            events=EventLog(connection, clock),
            schedule=schedule if schedule is not None else QuotaSchedule(),
            clock=clock,
            settings=settings,
        ),
        project_root=project_root,
    )


def build_writer(
    connection: Connection, clock: Clock, project_root: Path, *, tmux: Tmux | None = None
) -> TransitionWriter:
    """A `TransitionWriter` over `connection`, refreshing titles through `tmux` when given."""
    decorator = PaneDecorator(tmux if tmux is not None else Tmux(SubprocessRunner()))
    settings = fake_engine_settings()
    tasks = TaskStore(connection, clock)
    return TransitionWriter(
        tasks=tasks,
        events=EventLog(connection, clock),
        titles=build_titles(
            connection=connection,
            clock=clock,
            decorator=decorator,
            settings=settings,
            project_root=project_root,
        ),
        settings=settings,
    )


def build_decorator(tmux: Tmux) -> PaneDecorator:
    """Build task pane and window decorations over the isolated server."""
    return PaneDecorator(tmux)


async def new_agent_pane(tmux: Tmux, cwd: Path) -> str:
    """Create a pane in a task window on the fixture's private tmux server."""
    name = f"engine-test-{uuid.uuid4().hex[:_PANE_SUFFIX_LENGTH]}"
    return await SessionView(tmux).create_task_window(_SESSION_NAME, window_name=name, cwd=cwd)


def set_script(monkeypatch: pytest.MonkeyPatch, steps: list[dict[str, object]]) -> None:
    """Configure the fake CLI's process-wide behavior for one integration test."""
    monkeypatch.setenv(SCRIPT_ENV_VAR, json.dumps(steps))


async def set_role_script(
    _tmux_server: TmuxServer, role: Role, steps: list[dict[str, object]]
) -> None:
    """Configure one role's fake CLI behavior without changing another role's script."""
    os.environ[role_script_env_var(role.value)] = json.dumps(steps)


async def create_running_task(
    connection: Connection,
    clock: Clock,
    *,
    title: str,
    pane_id: str,
    role: Role = Role.IMPLEMENTER,
    slot: int = 1,
) -> Task:
    """Insert a task at the running implement step with its real pane id recorded."""
    tasks = TaskStore(connection, clock)
    queue = CommandQueue(connection, clock)
    queued = queue.append(Submit(title=title, request=title))
    assert isinstance(queued.command, Submit)
    submitted = tasks.insert_from_submit(queued.command)
    transition = dataclasses.replace(
        Transition.stay(submitted, step=Step.IMPLEMENT, status=Status.RUNNING), iteration=1
    )
    tasks.apply_transition(submitted.id, transition)
    tasks.set_slot(submitted.id, slot)
    tasks.set_panes(submitted.id, {role: pane_id})
    task = tasks.get(submitted.id)
    assert task is not None
    return task


def build_turn_system(
    *,
    connection: Connection,
    project_root: Path,
    tmux: Tmux,
    runs: RunStore,
    settings: Settings,
    clock: Clock,
    registry: AgentRegistry | None = None,
) -> TurnSystem:
    """Build the real headless turn stack used by integration tests."""
    events = EventLog(connection, clock)
    quotas = QuotaStore(connection)
    decorator = build_decorator(tmux)
    tasks = TaskStore(connection, clock)
    schedule = QuotaSchedule()
    titles = build_titles(
        connection=connection,
        clock=clock,
        decorator=decorator,
        settings=settings,
        project_root=project_root,
        schedule=schedule,
    )
    runner = SubprocessRunner()
    agents = AgentPool(
        (registry if registry is not None else build_registry()).create,
        runner,
        settings,
        clock,
    )
    sessions = SessionManager(
        runs=runs,
        agents=agents,
        project_root=project_root,
        settings=settings,
    )
    helper_entries = tuple(
        HelperEntry(rung, agents.get(rung.agent)) for rung in settings.roles.helper.ladder
    )
    helper = Helper(helper_entries, clock)

    async def refresh_titles(task: Task) -> None:
        await titles.refresh(task)

    reconciler = build_reconciler(
        quotas=quotas,
        agents=agents,
        events=events,
        clock=clock,
        settings=settings,
        schedule=schedule,
    )
    monitor = TurnMonitor(
        sessions=sessions,
        runs=runs,
        quotas=reconciler,
        events=events,
        clock=clock,
        settings=settings,
        decorator=decorator,
        refresh_titles=refresh_titles,
        processes=TurnProcesses(runner=runner, clock=clock, project_root=project_root),
    )
    turns = TurnRunner(
        agents=agents,
        sessions=sessions,
        monitor=monitor,
        helper=helper,
        renderer=PromptRenderer(project_root),
        messages=OperatorMessages(project_root),
        events=events,
        runs=runs,
        tasks=tasks,
        project_root=project_root,
        settings=settings,
    )
    return TurnSystem(
        sessions=sessions,
        turns=turns,
        events=events,
        quotas=quotas,
        titles=titles,
        schedule=schedule,
    )


@dataclasses.dataclass
class EngineEnv:
    """Stores and private tmux fixture shared by turn and flow integrations."""

    tmux: Tmux
    tasks: TaskStore
    runs: RunStore
    connection: Connection
    project_root: Path
    clock: Clock
    turn_system: TurnSystem

    def build_turn_system(
        self,
        settings: Settings,
        *,
        clock: Clock | None = None,
        registry: AgentRegistry | None = None,
    ) -> TurnSystem:
        """Build a second turn stack with settings specific to one flow test."""
        return build_turn_system(
            connection=self.connection,
            project_root=self.project_root,
            tmux=self.tmux,
            runs=self.runs,
            settings=settings,
            clock=clock if clock is not None else self.clock,
            registry=registry,
        )

    async def new_pane(self) -> str:
        """Create a pane in a task window rooted at the project checkout."""
        return await new_agent_pane(self.tmux, self.project_root)

    async def build_task(
        self, *, title: str = "t", role: Role = Role.IMPLEMENTER, slot: int = 1
    ) -> Task:
        """Insert a running task with a pane and a task-root directory."""
        pane_id = await self.new_pane()
        task = await create_running_task(
            self.connection,
            self.clock,
            title=title,
            pane_id=pane_id,
            role=role,
            slot=slot,
        )
        task_root(self.project_root, task.id).mkdir(parents=True)
        return task


@pytest.fixture
async def engine_env(tmux_server: TmuxServer, connection: Connection, tmp_path: Path) -> EngineEnv:
    """Provide a fake-clock task store, real SQLite and isolated tmux, plus the headless stack."""
    tmux = build_tmux(tmux_server)
    clock = FakeClock(TASK_CREATED_AT)
    tasks = TaskStore(connection, clock)
    runs = RunStore(connection, clock)
    turn_system = build_turn_system(
        connection=connection,
        project_root=tmp_path,
        tmux=tmux,
        runs=runs,
        settings=fake_engine_settings(),
        clock=clock,
    )
    return EngineEnv(
        tmux=tmux,
        tasks=tasks,
        runs=runs,
        connection=connection,
        project_root=tmp_path,
        clock=clock,
        turn_system=turn_system,
    )


def build_reconciler(
    *,
    quotas: QuotaStore,
    agents: AgentPool,
    events: EventLog,
    clock: Clock,
    settings: Settings,
    schedule: QuotaSchedule | None = None,
) -> QuotaReconciler:
    """A `QuotaReconciler` probing `agents` with the quota refresh settings of `settings`."""
    return QuotaReconciler(
        quotas=quotas,
        agents=agents,
        events=events,
        clock=clock,
        settings=settings.orchestration.quota_refresh,
        faults=build_fault_reporter(events, clock, settings),
        schedule=schedule if schedule is not None else QuotaSchedule(),
    )


def build_fault_reporter(events: EventLog, clock: Clock, settings: Settings) -> FaultReporter:
    """A `FaultReporter` over `events` with the retry settings of `settings`."""
    return FaultReporter(
        events=events,
        clock=clock,
        settings=settings.orchestration.faults,
        logger=logging.getLogger("fault-reporter-test"),
    )


def pr_poll_seconds() -> float:
    return engine_settings().orchestration.network.pr_poll_seconds


UNREACHABLE_DEADLINE_SECONDS = 10**9


def engine_settings(*, max_cpu_pct: int = 0, max_parallel: int = 1) -> Settings:
    settings = make_settings(
        leader_ladder=[FAKE_RUNG],
        implementer_ladder=[FAKE_RUNG],
        helper_ladder=[FAKE_RUNG],
        agents={"fake": AgentConfig()},
    )
    orchestration = settings.orchestration.model_copy(
        update={
            "max_parallel": max_parallel,
            "timeouts": settings.orchestration.timeouts.model_copy(
                update={"stall": UNREACHABLE_DEADLINE_SECONDS, "tool": UNREACHABLE_DEADLINE_SECONDS}
            ),
            "quota_refresh": settings.orchestration.quota_refresh.model_copy(
                update={"timeout_seconds": UNREACHABLE_DEADLINE_SECONDS}
            ),
            "resources": settings.orchestration.resources.model_copy(
                update={"max_cpu_pct": max_cpu_pct, "min_free_ram_gb": 0}
            ),
        }
    )
    update: dict[str, Any] = {
        "project": ProjectConfig(name="test", trackers=[]),
        "orchestration": orchestration,
        "dashboard": settings.dashboard.model_copy(update={"layout": ENGINE_LAYOUT}),
    }
    return settings.model_copy(update=update)


async def wait_for_command(
    queued: QueuedCommand, queue: CommandQueue, clock: FakeClock
) -> CommandOutcome:
    async def completed() -> bool:
        outcome = queue.status(queued.id)
        if outcome is not None and outcome.status is not CommandStatus.PENDING:
            return True
        if clock.has_pending_sleep():
            clock.advance(COMMAND_POLL_SECONDS)
        return False

    await wait_until(completed, timeout=_COMMAND_WAIT_SECONDS)
    outcome = queue.status(queued.id)
    assert outcome is not None
    return outcome
