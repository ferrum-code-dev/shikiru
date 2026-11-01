"""Scaffolding for TaskFlow integration tests over real git, SQLite, tmux and fake CLI processes."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from shikiru.config.settings import AgentConfig, RepoConfig
from shikiru.domain.commands import SetRung, Submit
from shikiru.domain.tasks import Role, Status
from shikiru.engine.acceptance import AcceptanceGuard
from shikiru.engine.agents import AgentPool
from shikiru.engine.escalation_filing import EscalationFiler
from shikiru.engine.escalations import EscalationRaiser
from shikiru.engine.evidence import EvidenceCollector
from shikiru.engine.flow import TaskFlow
from shikiru.engine.handlers.base import HandlerContext
from shikiru.engine.handlers.tasks import SetRungHandler
from shikiru.engine.history import ReportHistory
from shikiru.engine.integration import (
    ConflictRecovery,
    IntegrationMetadata,
    IntegrationStrategy,
    IntegrationSummary,
    Integrator,
    LocalMerge,
)
from shikiru.engine.integration_turns import IntegrationTurns
from shikiru.engine.recovery import TurnRecovery
from shikiru.engine.step_runner import StepRunner
from shikiru.engine.task_facts import TaskFacts
from shikiru.engine.transitions import TransitionWriter
from shikiru.engine.turn_context import TurnContextPreparer
from shikiru.engine.turn_loop import TurnLoop
from shikiru.process import SubprocessRunner
from shikiru.store.commands import CommandQueue
from shikiru.store.database import transaction
from shikiru.store.escalations import EscalationStore
from shikiru.store.quotas import QuotaStore
from shikiru.tmux.session import SessionView
from shikiru.workspace.base_refs import LocalBaseRefs
from shikiru.workspace.facts import GitFacts
from shikiru.workspace.git import Git
from tests.support.engine import (
    FAKE_AGENT_NAME,
    FAKE_RUNG,
    EngineEnv,
    build_reconciler,
    build_registry,
)
from tests.support.factories import ESCALATE_AFTER, MAX_ITERATIONS, make_settings
from tests.support.git_repo import GIT_COMMAND_TIMEOUT, commit_all, init_repo
from tests.support.workspace import task_root_builder

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from shikiru.agents.registry import AgentRegistry
    from shikiru.clock import Clock
    from shikiru.config.settings import ComplexityName, Settings
    from shikiru.domain.tasks import Mode, Task
    from shikiru.engine.titles import TaskTitles
    from shikiru.store.events import EventLog
    from shikiru.workspace.base_refs import BaseRefs


class _NoOpIntegrationCoordinator:
    async def run(
        self,
        task: Task,
        resolve_conflicts: object,
        metadata: IntegrationMetadata,
        conflict_recovery: Callable[[Task], ConflictRecovery] | None = None,
    ) -> IntegrationSummary:
        del task, resolve_conflicts, conflict_recovery
        return IntegrationSummary(summary=metadata.summary, residual_risks=metadata.residual_risks)

    async def continue_rebase(self, task: Task) -> None:
        del task


def flow_settings(
    *, max_iterations: int = MAX_ITERATIONS, escalate_after: int = ESCALATE_AFTER
) -> Settings:
    """Settings whose leader, implementer and helper all use the fake CLI."""
    return make_settings(
        max_iterations=max_iterations,
        escalate_after=escalate_after,
        leader_ladder=[FAKE_RUNG],
        implementer_ladder=[FAKE_RUNG],
        helper_ladder=[FAKE_RUNG],
        agents={FAKE_AGENT_NAME: AgentConfig()},
    )


async def init_project_repo(project_root: Path) -> None:
    """Create the source repository used by task worktree integration tests."""
    runner = SubprocessRunner()
    await init_repo(runner, project_root)
    (project_root / "README.md").write_text("shikiru flow test project\n")
    await commit_all(runner, project_root, "initial commit")


async def build_flow_task(
    env: EngineEnv,
    *,
    title: str = "Add retries",
    mode: Mode | None = None,
    complexity: ComplexityName | None = None,
    base_refs: BaseRefs | None = None,
) -> Task:
    """Insert a queued task with real leader/implementer panes and a git worktree."""
    tasks = env.tasks
    queue = CommandQueue(env.connection, env.clock)
    queued = queue.append(Submit(title=title, request=title, mode=mode, complexity=complexity))
    assert isinstance(queued.command, Submit)
    submitted = tasks.insert_from_submit(queued.command)
    leader_pane = await env.new_pane()
    implementer_pane = await env.new_pane()
    tasks.set_slot(submitted.id, 1)
    tasks.set_panes(submitted.id, {Role.LEADER: leader_pane, Role.IMPLEMENTER: implementer_pane})
    tasks.set_window(submitted.id, leader_pane)
    builder = task_root_builder(SubprocessRunner(), env.project_root, RepoConfig(), base_refs)
    repo = await builder.build(submitted.id, submitted.title)
    tasks.set_repo(submitted.id, repo)
    task = tasks.get(submitted.id)
    assert task is not None
    return task


@dataclass
class FlowEnv:
    flow: TaskFlow
    env: EngineEnv
    events: EventLog
    escalations: EscalationStore
    titles: TaskTitles


class CountingLock(asyncio.Lock):
    def __init__(self) -> None:
        super().__init__()
        self.acquisitions = 0

    async def acquire(self) -> Literal[True]:
        self.acquisitions += 1
        return await super().acquire()


def build_flow(
    env: EngineEnv,
    settings: Settings,
    *,
    clock: Clock | None = None,
    turn_clock: Clock | None = None,
    local_integration: bool = False,
    strategy: IntegrationStrategy | None = None,
    base_refs: BaseRefs | None = None,
    lock: asyncio.Lock | None = None,
    registry: AgentRegistry | None = None,
) -> FlowEnv:
    """Build one flow and its production turn collaborators with fake CLI processes.

    `local_integration` integrates through `LocalMerge`, `strategy` through the one given.
    """
    flow_clock = clock if clock is not None else env.clock
    resolved_turn_clock = turn_clock if turn_clock is not None else flow_clock
    system = env.build_turn_system(settings, clock=resolved_turn_clock, registry=registry)
    escalations_store = EscalationStore(env.connection, flow_clock)
    history = ReportHistory(env.project_root)
    facts = TaskFacts(
        git_facts=GitFacts(Git(SubprocessRunner(), command_timeout=GIT_COMMAND_TIMEOUT)),
        project_root=env.project_root,
        settings=settings,
    )
    evidence = EvidenceCollector(history=history, facts=facts, project_root=env.project_root)
    escalations = EscalationRaiser(
        escalations_store,
        system.events,
        env.runs,
        env.project_root,
        atomically=lambda: transaction(env.connection),
        titles=system.titles,
        evidence=evidence,
        settings=settings,
    )
    coordinator = _NoOpIntegrationCoordinator()
    if local_integration or strategy is not None:
        builder = task_root_builder(SubprocessRunner(), env.project_root, settings.repo)

        async def finish_task(task: Task) -> None:
            system.sessions.close_task(task.id)
            await builder.cleanup(task.id, task.repo, integrated=task.status is Status.DONE)
            if task.window_id is None:
                raise AssertionError
            await SessionView(env.tmux).close_task_window(task.window_id)

        git = Git(SubprocessRunner(), command_timeout=GIT_COMMAND_TIMEOUT)
        coordinator = Integrator(
            git=git,
            strategy=strategy
            or LocalMerge(
                git,
                flow_clock,
                events=system.events,
                project_root=env.project_root,
                integration_wait=settings.orchestration.timeouts.integration_wait,
            ),
            base_refs=base_refs if base_refs is not None else LocalBaseRefs(),
            lock=lock if lock is not None else asyncio.Lock(),
            project_root=env.project_root,
            tasks=env.tasks,
            events=system.events,
            finish_task=finish_task,
        )
    contexts = TurnContextPreparer(
        history=history,
        escalation_store=escalations_store,
        facts=facts,
        settings=settings,
        project_root=env.project_root,
    )
    writer = TransitionWriter(
        tasks=env.tasks, events=system.events, titles=system.titles, settings=settings
    )
    filer = EscalationFiler(raiser=escalations, evidence=evidence, writer=writer, tasks=env.tasks)
    turn_loop = TurnLoop(
        tasks=env.tasks,
        events=system.events,
        sessions=system.sessions,
        turns=system.turns,
        contexts=contexts,
        recovery=TurnRecovery(
            tasks=env.tasks,
            events=system.events,
            quota=QuotaStore(env.connection),
            reconciler=build_reconciler(
                quotas=system.quotas,
                agents=AgentPool(
                    (registry if registry is not None else build_registry()).create,
                    SubprocessRunner(),
                    settings,
                    flow_clock,
                ),
                events=system.events,
                clock=flow_clock,
                settings=settings,
                schedule=system.schedule,
            ),
            clock=flow_clock,
            writer=writer,
            settings=settings,
        ),
        escalations=filer,
        runs=env.runs,
    )
    steps = StepRunner(
        loop=turn_loop,
        acceptance=AcceptanceGuard(events=system.events, facts=facts),
        escalations=filer,
        writer=writer,
        events=system.events,
        settings=settings,
    )
    flow = TaskFlow(
        tasks=env.tasks,
        writer=writer,
        steps=steps,
        turn_loop=turn_loop,
        escalations=filer,
        integration_turns=IntegrationTurns(
            coordinator=coordinator,
            events=system.events,
            history=history,
            project_root=env.project_root,
            tasks=env.tasks,
            steps=steps,
            escalations=filer,
        ),
    )
    return FlowEnv(
        flow=flow,
        env=env,
        events=system.events,
        escalations=escalations_store,
        titles=system.titles,
    )


async def apply_set_rung(env: EngineEnv, flow_env: FlowEnv, command: SetRung) -> None:
    """Run the operator's `set_rung` handler against the same stores a flow is using."""

    async def unused(_task_id: str) -> None:
        raise AssertionError

    async def unused_task(_task: Task) -> None:
        raise AssertionError

    context = HandlerContext(
        tasks=env.tasks,
        escalations=flow_env.escalations,
        events=flow_env.events,
        runs=env.runs,
        project_root=env.project_root,
        cancel_task=unused,
        start_task=unused,
        finish_task=unused_task,
        stop_engine=lambda: None,
        refresh_titles=flow_env.titles.refresh,
        atomically=lambda: transaction(env.connection),
    )
    await SetRungHandler(context).handle(command)
