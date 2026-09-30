"""Real SQLite, git, tmux and fake CLI scaffolding for engine integration tests."""

from __future__ import annotations

import dataclasses
import json
import os
import uuid
from typing import TYPE_CHECKING

import pytest
from fake_agent.agent import create as create_fake_agent
from fake_agent.scripted import SCRIPT_ENV_VAR, role_script_env_var

from shikiru.agents.contracts import Rung
from shikiru.agents.registry import AgentRegistry
from shikiru.config.settings import AgentConfig
from shikiru.domain.commands import Submit
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import Role, Status, Step, Task
from shikiru.engine.agents import AgentPool
from shikiru.engine.escalations import EscalationRaiser
from shikiru.engine.evidence import EvidenceCollector
from shikiru.engine.history import ReportHistory
from shikiru.engine.monitor import TurnMonitor
from shikiru.engine.sessions import SessionManager
from shikiru.engine.task_facts import TaskFacts
from shikiru.engine.titles import TaskTitles
from shikiru.engine.turn_processes import TurnProcesses
from shikiru.engine.turns import TurnRunner
from shikiru.helper.helper import Helper
from shikiru.process import SubprocessRunner
from shikiru.prompts.renderer import PromptRenderer
from shikiru.store.commands import CommandQueue
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

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.clock import Clock
    from shikiru.config.settings import Settings
    from tests.support.store import Connection
    from tests.support.tmux import TmuxServer

FAKE_AGENT_NAME = "fake"
FAKE_AGENT_ALIAS = "fake2"
FAKE_RUNG = Rung(agent=FAKE_AGENT_NAME, model="fake-1", effort=None)
_SESSION_NAME = "test"


@dataclasses.dataclass
class TurnSystem:
    sessions: SessionManager
    turns: TurnRunner
    events: EventLog
    quotas: QuotaStore
    titles: TaskTitles


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
            git_facts=GitFacts(Git(SubprocessRunner())),
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
        titles=TaskTitles(decorator, runs, fake_engine_settings()),
        evidence=build_evidence(project_root),
    )


def build_decorator(tmux: Tmux) -> PaneDecorator:
    """Build task pane and window decorations over the isolated server."""
    return PaneDecorator(tmux)


async def new_agent_pane(tmux: Tmux, cwd: Path) -> str:
    """Create a pane in a task window on the fixture's private tmux server."""
    name = f"engine-test-{uuid.uuid4().hex[:8]}"
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
    titles = TaskTitles(decorator, runs, settings)
    sessions = SessionManager(runs=runs, project_root=project_root, settings=settings)
    runner = SubprocessRunner()
    agents = AgentPool(
        (registry if registry is not None else build_registry()).create,
        runner,
        settings,
        clock,
    )
    helper = Helper(agents.get(settings.roles.helper.ladder[0].agent))

    async def refresh_titles(task: Task) -> None:
        await titles.refresh(task)

    monitor = TurnMonitor(
        sessions=sessions,
        runs=runs,
        quotas=quotas,
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
        project_root=project_root,
        settings=settings,
    )
    return TurnSystem(sessions=sessions, turns=turns, events=events, quotas=quotas, titles=titles)


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

    def build_turn_system(self, settings: Settings, *, clock: Clock | None = None) -> TurnSystem:
        """Build a second turn stack with settings specific to one flow test."""
        return build_turn_system(
            connection=self.connection,
            project_root=self.project_root,
            tmux=self.tmux,
            runs=self.runs,
            settings=settings,
            clock=clock if clock is not None else self.clock,
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
