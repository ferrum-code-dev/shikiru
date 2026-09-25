"""The composition root. Assembles concrete components for each entry point (02 §4)."""

from __future__ import annotations

import asyncio
import logging
from importlib import import_module
from typing import TYPE_CHECKING, cast

from shikiru.agents.registry import AgentRegistry
from shikiru.clock import Clock, SystemClock
from shikiru.config.loader import load_settings
from shikiru.engine.agents import AgentPool
from shikiru.engine.daemon import (
    ENGINE_LOGGER_NAME,
    Engine,
    engine_is_running,
)
from shikiru.engine.dependency import DependencyEscalator
from shikiru.engine.escalations import DispatchFailureEscalator, EscalationRaiser
from shikiru.engine.flow import TaskFlow
from shikiru.engine.handlers import (
    CancelHandler,
    HandBackHandler,
    HandlerContext,
    HandlerRegistry,
    PauseHandler,
    PrioritizeHandler,
    ResolveHandler,
    ResumeHandler,
    RetryHandler,
    SayHandler,
    SetRungHandler,
    StopHandler,
    SubmitHandler,
    TakeOverHandler,
)
from shikiru.engine.history import ReportHistory
from shikiru.engine.integration import Integrator, LocalMerge
from shikiru.engine.integration_turns import IntegrationTurns
from shikiru.engine.layout_guard import LayoutGuard
from shikiru.engine.lifecycle import TaskFinisher
from shikiru.engine.monitor import TurnMonitor
from shikiru.engine.notifier import NOTIFY_TIMEOUT_SECONDS, Notifier
from shikiru.engine.scheduler import Scheduler
from shikiru.engine.sessions import SessionManager
from shikiru.engine.supervisor import TaskSupervisor
from shikiru.engine.takeover import Takeover
from shikiru.engine.titles import TaskTitles
from shikiru.engine.turns import TurnRunner
from shikiru.errors import ConfigError
from shikiru.helper.helper import Helper
from shikiru.operator.commands import OperatorCommands
from shikiru.operator.detectors import ProjectDetectors
from shikiru.operator.doctor import Doctor
from shikiru.operator.events import EventFeed
from shikiru.operator.lifecycle import ProjectLifecycle
from shikiru.operator.plugin_checks import PluginChecks
from shikiru.operator.probe import AgentProbe
from shikiru.operator.queries import OperatorQueries
from shikiru.operator.services import OperatorServices
from shikiru.paths import engine_lock_path, engine_log_path, project_state_dir, state_db_path
from shikiru.process import SubprocessRunner
from shikiru.prompts.renderer import PromptRenderer
from shikiru.store.commands import CommandQueue
from shikiru.store.database import Database
from shikiru.store.escalations import EscalationStore
from shikiru.store.events import EventLog
from shikiru.store.quotas import QuotaStore
from shikiru.store.reader import StateReader
from shikiru.store.runs import RunStore
from shikiru.store.tasks import TaskStore
from shikiru.telemetry.resources import ResourceSampler
from shikiru.tmux.client import Tmux, exact_target
from shikiru.tmux.decorator import PaneDecorator
from shikiru.tmux.layout import LayoutBuilder
from shikiru.tmux.session import SessionView, session_name
from shikiru.workspace.caches import CacheCloner, default_cow_args
from shikiru.workspace.facts import GitFacts
from shikiru.workspace.git import Git
from shikiru.workspace.messages import OperatorMessages
from shikiru.workspace.roots import TaskRootBuilder

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from mcp.server.fastmcp import FastMCP

    from shikiru.config.settings import Settings
    from shikiru.domain.tasks import Task


def build_clock() -> Clock:
    """Return the `Clock` implementation used by every real entry point."""
    return SystemClock()


def build_project_detectors(project_root: Path) -> ProjectDetectors:
    """Build the detector with one injected process, clock and git boundary."""
    runner = SubprocessRunner()
    plugins = PluginChecks(AgentRegistry(), runner, build_clock())
    return ProjectDetectors(
        project_root,
        git=Git(runner),
        plugin_checks=plugins,
    )


def build_doctor(project_root: Path) -> Doctor:
    """Build environment diagnostics with one injected process, clock and registry."""
    runner = SubprocessRunner()
    clock = build_clock()
    registry = AgentRegistry()
    return Doctor(
        project_root,
        cow_args=default_cow_args(),
        git=Git(runner),
        runner=runner,
        clock=clock,
        plugin_checks=PluginChecks(registry, runner, clock),
        probe=AgentProbe(registry, runner, clock),
    )


def build_lifecycle() -> ProjectLifecycle:
    """Build the detached engine lifecycle service."""
    clock = build_clock()
    runner = SubprocessRunner()
    tmux = Tmux(runner)
    session = SessionView(tmux)
    registry = AgentRegistry()
    plugin_checks = PluginChecks(registry, runner, clock)

    def operator_factory(project_root: Path, settings: Settings) -> OperatorServices:
        return build_operator(project_root, settings, clock=clock)

    async def plugin_validator(settings: Settings) -> None:
        results = [
            await plugin_checks.check(name, config.options)
            for name, config in sorted(settings.agents.items())
        ]
        failures = [result for result in results if not result.ok]
        if failures:
            detail = "; ".join(f"{result.name}: {result.detail}" for result in failures)
            message = f"agent plugin checks failed: {detail}"
            raise ConfigError(message)

    def engine_up(project_root: Path) -> bool:
        return engine_is_running(engine_lock_path(project_root))

    return ProjectLifecycle(
        runner=runner,
        tmux=tmux,
        session=session,
        clock=clock,
        operator_factory=operator_factory,
        plugin_validator=plugin_validator,
        engine_up=engine_up,
    )


async def run_dashboard(
    project_root: Path,
    overrides: Sequence[str],
    focus_task: str | None,
) -> None:
    """Run the configured dashboard for one project entry point."""
    dashboard_app_module = import_module("shikiru.dashboard.app")
    dashboard_services_module = import_module("shikiru.dashboard.services")
    dashboard_views_module = import_module("shikiru.dashboard.views")
    clock = build_clock()
    git = Git(SubprocessRunner())
    settings = load_settings(project_root, overrides)
    services = build_operator(project_root, settings, clock=clock)
    try:
        dashboard_services = dashboard_services_module.DashboardServices(
            dashboard_services_module.GitRepoStatusService(git, settings.repo, project_root, clock),
            lambda: engine_is_running(engine_lock_path(project_root)),
        )
        views = dashboard_views_module.StateDashboardViews(
            services.reader,
            focus_task=focus_task,
            settings=settings,
            sources=dashboard_services,
        )
        app_instance = dashboard_app_module.DashboardApp(
            focus_task=focus_task,
            views=views,
            on_change=services.reader.watch,
            widget_names=settings.dashboard.widgets,
        )
        await app_instance.run_async()
    finally:
        services.close()


def build_operator(
    project_root: Path,
    settings: Settings | None = None,
    *,
    clock: Clock | None = None,
) -> OperatorServices:
    """Build read, command and event services for one initialized project."""
    resolved_clock = clock if clock is not None else build_clock()
    resolved_settings = settings if settings is not None else load_settings(project_root)
    database = Database(state_db_path(project_root))
    connection = database.connect()
    reader = StateReader(database, resolved_clock)
    command_queue = CommandQueue(connection, resolved_clock)
    engine_lock = engine_lock_path(project_root)

    def engine_state() -> bool:
        return engine_is_running(engine_lock)

    queries = OperatorQueries(
        reader,
        project=resolved_settings.project.name,
        project_root=project_root,
        engine_up=engine_state,
        max_parallel=resolved_settings.orchestration.max_parallel,
    )
    commands = OperatorCommands(command_queue, resolved_clock, engine_up=engine_state)
    events = EventFeed(reader, resolved_clock)

    def close() -> None:
        reader.close()
        connection.close()

    return OperatorServices(reader, queries, commands, events, close)


def build_mcp_server(
    project_root: Path,
    overrides: Sequence[str] = (),
) -> tuple[FastMCP, OperatorServices]:
    """Build the stdio MCP server and the operator services it owns."""
    settings = load_settings(project_root, overrides)
    services = build_operator(project_root, settings)
    module = import_module("shikiru.mcp.server")
    factory = cast("Callable[[OperatorServices], FastMCP]", module.build_server)
    return factory(services), services


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
    resolved_registry = registry if registry is not None else AgentRegistry()
    agent_pool = AgentPool(
        resolved_registry.create, resolved_runner, resolved_settings, resolved_clock
    )
    helper_rung = resolved_settings.roles.helper.ladder[0]
    helper = Helper(agent_pool.get(helper_rung.agent))
    project_state_dir(project_root).mkdir(parents=True, exist_ok=True)
    connection = Database(state_db_path(project_root)).connect()
    tasks = TaskStore(connection, resolved_clock)
    runs = RunStore(connection, resolved_clock)
    events = EventLog(connection, resolved_clock)
    quotas = QuotaStore(connection)
    escalations_store = EscalationStore(connection, resolved_clock)
    command_queue = CommandQueue(connection, resolved_clock)

    decorator = PaneDecorator(resolved_tmux)
    titles = TaskTitles(decorator, runs, resolved_settings)

    async def refresh_titles(task: Task) -> None:
        await titles.refresh(task)

    sessions = SessionManager(runs=runs, project_root=project_root, settings=resolved_settings)
    monitor = TurnMonitor(
        sessions=sessions,
        runs=runs,
        quotas=quotas,
        events=events,
        clock=resolved_clock,
        settings=resolved_settings,
        decorator=decorator,
        refresh_titles=refresh_titles,
    )

    target = exact_target(session_name(resolved_settings.project.name))
    layout = LayoutGuard(
        tmux=resolved_tmux,
        view=SessionView(resolved_tmux),
        builder=LayoutBuilder(resolved_tmux),
        decorator=decorator,
        session_target=target,
        tasks=tasks,
        settings=resolved_settings,
        project_root=project_root,
        events=events,
        runs=runs,
        commands=command_queue,
    )
    roots = TaskRootBuilder(
        Git(resolved_runner),
        project_root,
        resolved_settings.repo,
        CacheCloner(resolved_runner, cow_args=default_cow_args()),
    )
    task_finisher = TaskFinisher(sessions, layout, roots)
    escalation_raiser = EscalationRaiser(escalations_store, events, runs, project_root)
    git = Git(resolved_runner)
    integrator = _build_integrator(
        project_root=project_root,
        settings=resolved_settings,
        clock=resolved_clock,
        git=git,
        tasks=tasks,
        events=events,
        task_finisher=task_finisher,
    )
    history = ReportHistory(project_root)
    messages = OperatorMessages(project_root)
    flow = TaskFlow(
        tasks=tasks,
        events=events,
        sessions=sessions,
        turns=TurnRunner(
            agents=agent_pool,
            sessions=sessions,
            monitor=monitor,
            helper=helper,
            renderer=PromptRenderer(project_root),
            messages=messages,
            events=events,
            runs=runs,
            project_root=project_root,
            settings=resolved_settings,
        ),
        git_facts=GitFacts(git),
        escalations=escalation_raiser,
        escalation_store=escalations_store,
        titles=titles,
        settings=resolved_settings,
        project_root=project_root,
        quota=quotas,
        clock=resolved_clock,
        integration_turns=IntegrationTurns(
            coordinator=integrator,
            history=history,
            settings=resolved_settings,
            tasks=tasks,
        ),
        history=history,
        runs=runs,
    )
    task_supervisor = TaskSupervisor(tasks, flow, task_finisher)
    handler_context = HandlerContext(
        tasks=tasks,
        escalations=escalations_store,
        events=events,
        project_root=project_root,
        cancel_task=task_supervisor.cancel_task,
        start_task=task_supervisor.start_task,
        finish_task=task_finisher.finish_task,
        stop_engine=task_supervisor.request_stop,
        refresh_titles=titles.refresh,
    )
    takeover = Takeover(
        tmux=resolved_tmux,
        layout=layout,
        agents=agent_pool,
        tasks=tasks,
        runs=runs,
        messages=messages,
        events=events,
        project_root=project_root,
        project_name=resolved_settings.project.name,
        cancel_task=task_supervisor.cancel_task,
        start_task=task_supervisor.start_task,
        refresh_titles=refresh_titles,
    )
    handlers = HandlerRegistry(
        [
            SubmitHandler(handler_context),
            PrioritizeHandler(handler_context),
            PauseHandler(handler_context),
            ResumeHandler(handler_context),
            CancelHandler(handler_context),
            RetryHandler(handler_context),
            SetRungHandler(handler_context),
            SayHandler(handler_context, messages),
            TakeOverHandler(handler_context, takeover),
            HandBackHandler(handler_context, takeover),
            ResolveHandler(handler_context),
            StopHandler(handler_context),
        ]
    )
    sampler = ResourceSampler(resolved_clock)
    notifier = Notifier(
        resolved_tmux,
        resolved_runner,
        session_target=target,
        command=resolved_settings.notify.command,
        timeout_seconds=NOTIFY_TIMEOUT_SECONDS,
    )
    scheduler = Scheduler(
        tasks=tasks,
        settings=resolved_settings,
        quotas=quotas,
        resources=sampler,
        clock=resolved_clock,
        layout=layout,
        roots=roots,
        events=events,
        on_dependency_cancelled=DependencyEscalator(tasks, escalation_raiser, titles),
        on_dispatch_failure=DispatchFailureEscalator(tasks, escalation_raiser, titles),
        titles=titles,
    )
    lock_path = engine_lock_path(project_root)
    logger, log_handler = _configure_engine_logging(project_root)
    return Engine(
        lock_path=lock_path,
        command_queue=command_queue,
        tasks=tasks,
        events=events,
        scheduler=scheduler,
        task_supervisor=task_supervisor,
        handlers=handlers,
        layout=layout,
        notifier=notifier,
        settings=resolved_settings,
        clock=resolved_clock,
        close_state=connection.close,
        logger=logger,
        log_handler=log_handler,
    )


def _build_integrator(
    *,
    project_root: Path,
    settings: Settings,
    clock: Clock,
    git: Git,
    tasks: TaskStore,
    events: EventLog,
    task_finisher: TaskFinisher,
) -> Integrator:
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
        finish_task=task_finisher.finish_task,
    )


def _configure_engine_logging(project_root: Path) -> tuple[logging.Logger, logging.FileHandler]:
    log_path = engine_log_path(project_root)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(ENGINE_LOGGER_NAME)
    handler = logging.FileHandler(log_path)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger, handler
