"""The composition root. Assembles concrete components for each entry point (02 §4)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from shikiru.agents.registry import AgentRegistry
from shikiru.clock import Clock, SystemClock
from shikiru.config.loader import load_settings
from shikiru.engine.agents import AgentPool
from shikiru.engine.daemon import Engine
from shikiru.engine.dependency import DependencyEscalator
from shikiru.engine.escalations import EscalationRaiser
from shikiru.engine.flow import TaskFlow
from shikiru.engine.integration import IntegrationSummary, Integrator, LocalMerge
from shikiru.engine.layout_guard import LayoutGuard
from shikiru.engine.notifier import Notifier
from shikiru.engine.quota_poller import QuotaPoller
from shikiru.engine.scheduler import Scheduler
from shikiru.engine.sessions import SessionManager
from shikiru.engine.titles import TaskTitles
from shikiru.engine.turns import TurnRunner
from shikiru.process import SubprocessRunner
from shikiru.prompts.renderer import PromptRenderer
from shikiru.store.commands import CommandQueue
from shikiru.store.database import Database
from shikiru.store.escalations import EscalationStore
from shikiru.store.events import EventLog
from shikiru.store.messages import MessageStore
from shikiru.store.quotas import QuotaStore
from shikiru.store.runs import RunStore
from shikiru.store.tasks import TaskStore
from shikiru.telemetry.resources import ResourceSampler
from shikiru.tmux.client import Tmux, exact_target
from shikiru.tmux.decorator import PaneDecorator
from shikiru.tmux.pane import TmuxPane
from shikiru.tmux.session import session_name
from shikiru.workspace.facts import GitFacts
from shikiru.workspace.git import Git
from shikiru.workspace.roots import TaskRootBuilder

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.config.settings import Settings
    from shikiru.domain.tasks import Task


def build_clock() -> Clock:
    """Return the `Clock` implementation used by every real entry point."""
    return SystemClock()


def build_engine(
    project_root: Path,
    settings: Settings | None = None,
    *,
    clock: Clock | None = None,
    runner: SubprocessRunner | None = None,
    tmux: Tmux | None = None,
    registry: AgentRegistry | None = None,
) -> Engine:
    """Build the in-process engine from project boundaries and validated settings."""
    resolved_clock = clock if clock is not None else build_clock()
    resolved_settings = settings if settings is not None else load_settings(project_root)
    resolved_runner = runner if runner is not None else SubprocessRunner()
    resolved_tmux = tmux if tmux is not None else Tmux(resolved_runner)
    state_dir = project_root / ".shikiru"
    state_dir.mkdir(parents=True, exist_ok=True)
    connection = Database(state_dir / "state.db").connect()
    tasks = TaskStore(connection, resolved_clock)
    runs = RunStore(connection, resolved_clock)
    events = EventLog(connection, resolved_clock)
    quotas = QuotaStore(connection)
    messages = MessageStore(connection)
    escalations_store = EscalationStore(connection, resolved_clock)
    command_queue = CommandQueue(connection, resolved_clock)
    resolved_registry = registry if registry is not None else AgentRegistry()
    agent_pool = AgentPool(
        resolved_registry.create, resolved_runner, resolved_settings, resolved_clock
    )

    def pane_factory(pane_id: str) -> TmuxPane:
        return TmuxPane(resolved_tmux, pane_id, resolved_clock)

    decorator = PaneDecorator(resolved_tmux)
    sessions = SessionManager(
        agent_pool,
        pane_factory,
        tasks=tasks,
        runs=runs,
        project_root=project_root,
        settings=resolved_settings,
        clock=resolved_clock,
        decorator=decorator,
    )
    target = exact_target(session_name(resolved_settings.project.name))
    layout = LayoutGuard(
        tmux=resolved_tmux,
        session_target=target,
        tasks=tasks,
        settings=resolved_settings,
        project_root=project_root,
        events=events,
        clock=resolved_clock,
    )
    roots = TaskRootBuilder(Git(resolved_runner), project_root)
    escalation_raiser = EscalationRaiser(escalations_store, events, runs, project_root)
    titles = TaskTitles(decorator, runs, resolved_settings)
    git = Git(resolved_runner)
    integrator = _build_integrator(
        project_root=project_root,
        settings=resolved_settings,
        clock=resolved_clock,
        git=git,
        roots=roots,
        tasks=tasks,
        events=events,
        sessions=sessions,
        layout=layout,
    )
    flow = TaskFlow(
        tasks=tasks,
        events=events,
        sessions=sessions,
        turns=TurnRunner(PromptRenderer(project_root), resolved_clock),
        git_facts=GitFacts(git),
        escalations=escalation_raiser,
        titles=titles,
        settings=resolved_settings,
        agent_pool=agent_pool,
        pane_factory=pane_factory,
        project_root=project_root,
        quota=quotas,
        clock=resolved_clock,
        integrator=integrator,
    )
    sampler = ResourceSampler(resolved_clock, layout.process_ids)
    notifier = Notifier(
        resolved_tmux,
        resolved_runner,
        session_target=target,
        command=resolved_settings.notify.command,
        timeout_seconds=resolved_settings.orchestration.timeouts.tool,
    )
    poller = QuotaPoller(agent_pool, runs, quotas, resolved_clock)

    scheduler = Scheduler(
        tasks=tasks,
        settings=resolved_settings,
        quotas=quotas,
        resources=sampler,
        clock=resolved_clock,
        layout=layout,
        roots=roots,
        events=events,
        on_dependency_cancelled=DependencyEscalator(tasks, escalation_raiser),
    )
    return Engine(
        lock_path=state_dir / "engine.lock",
        command_queue=command_queue,
        tasks=tasks,
        runs=runs,
        escalations=escalations_store,
        events=events,
        quotas=quotas,
        messages=messages,
        scheduler=scheduler,
        sessions=sessions,
        flow=flow,
        roots=roots,
        layout=layout,
        quota_poller=poller,
        notifier=notifier,
        settings=resolved_settings,
        project_root=project_root,
        clock=resolved_clock,
        close_state=connection.close,
    )


def _build_integrator(
    *,
    project_root: Path,
    settings: Settings,
    clock: Clock,
    git: Git,
    roots: TaskRootBuilder,
    tasks: TaskStore,
    events: EventLog,
    sessions: SessionManager,
    layout: LayoutGuard,
) -> Integrator:

    async def finalizer(task: Task, _summary: IntegrationSummary) -> None:
        await sessions.exit_task(task)
        await roots.cleanup(task.id, task.repos)

    async def close_window(task: Task) -> None:
        await layout.close(task)

    return Integrator(
        git=git,
        strategy=LocalMerge(
            git,
            clock,
            integration_wait=settings.orchestration.timeouts.integration_wait,
        ),
        lock=asyncio.Lock(),
        project_root=project_root,
        tasks=tasks,
        events=events,
        finalizer=finalizer,
        close_window=close_window,
    )
