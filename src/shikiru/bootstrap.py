"""The composition root. Assembles concrete components for each entry point."""

from __future__ import annotations

import asyncio
import logging
import os
from importlib import import_module
from typing import TYPE_CHECKING, Protocol, cast

from shikiru.agents.registry import AgentRegistry
from shikiru.cli.session_prompt import prompt_for_session
from shikiru.clock import Clock, SystemClock
from shikiru.config.loader import load_default_orchestration, load_session_settings, load_settings
from shikiru.config.settings import DOCKER_SCOPE_ALL, PULL_REQUEST_INTEGRATION
from shikiru.engine.acceptance import AcceptanceGuard
from shikiru.engine.agents import AgentPool
from shikiru.engine.cosmetic_decorator import CosmeticDecorator
from shikiru.engine.daemon import (
    ENGINE_LOGGER_NAME,
    Engine,
    EngineLock,
    engine_is_running,
)
from shikiru.engine.dependency import DependencyEscalator
from shikiru.engine.escalation_filing import EscalationFiler
from shikiru.engine.escalations import DispatchFailureEscalator, EscalationRaiser
from shikiru.engine.evidence import EvidenceCollector
from shikiru.engine.fault_reporter import FaultReporter
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
from shikiru.engine.integration import (
    IntegrationStrategy,
    Integrator,
    LocalMerge,
    PullRequest,
    PullRequestCloser,
)
from shikiru.engine.integration_turns import IntegrationTurns
from shikiru.engine.layout_guard import LayoutGuard
from shikiru.engine.lifecycle import TaskFinisher
from shikiru.engine.monitor import TurnMonitor
from shikiru.engine.notification_delivery import NotificationDelivery
from shikiru.engine.notification_feed import NotificationFeed
from shikiru.engine.notifier import Notifier
from shikiru.engine.pull_request_closing import PullRequestClosing
from shikiru.engine.reconcile import Reconciler
from shikiru.engine.recovery import TurnRecovery
from shikiru.engine.retention import Retention
from shikiru.engine.root_preparation import RootPreparation
from shikiru.engine.scheduler import Scheduler
from shikiru.engine.session_guard import SessionGuard
from shikiru.engine.sessions import SessionManager
from shikiru.engine.signalled import SignalledEngine
from shikiru.engine.step_runner import StepRunner
from shikiru.engine.supervisor import TaskSupervisor
from shikiru.engine.takeover import Takeover
from shikiru.engine.task_facts import TaskFacts
from shikiru.engine.tick_guard import TickGuard
from shikiru.engine.titles import TaskTitles
from shikiru.engine.transitions import TransitionWriter
from shikiru.engine.turn_context import TurnContextPreparer
from shikiru.engine.turn_loop import TurnLoop
from shikiru.engine.turn_processes import TurnProcesses
from shikiru.engine.turns import TurnRunner
from shikiru.errors import ConfigError
from shikiru.helper.helper import Helper, HelperEntry
from shikiru.operator.commands import OperatorCommands
from shikiru.operator.detectors import InitWizardResult, ProjectDetectors
from shikiru.operator.doctor import Doctor
from shikiru.operator.events import EventFeed
from shikiru.operator.evidence_export import TaskEvidenceExporter
from shikiru.operator.evidence_writer import LocalEvidenceWriter
from shikiru.operator.lifecycle import ProjectLifecycle, StartResult, home_command
from shikiru.operator.plugin_checks import PluginChecks
from shikiru.operator.probe import AgentProbe
from shikiru.operator.queries import OperatorQueries
from shikiru.operator.services import OperatorServices
from shikiru.operator.sessions import SessionAttacher, SessionLister
from shikiru.paths import (
    engine_lock_path,
    engine_log_path,
    project_config_path,
    project_state_dir,
    state_db_path,
)
from shikiru.private import PrivateRotatingFileHandler, make_private_dir, tighten_state_dir
from shikiru.process import SubprocessRunner
from shikiru.prompts.renderer import PromptRenderer
from shikiru.store.commands import CommandQueue
from shikiru.store.database import Database, transaction
from shikiru.store.escalations import EscalationStore
from shikiru.store.events import EventLog
from shikiru.store.notification_cursor import NotificationCursor
from shikiru.store.quotas import QuotaStore
from shikiru.store.reader import StateReader
from shikiru.store.retention import StateRetention
from shikiru.store.runs import RunStore
from shikiru.store.tasks import TaskStore
from shikiru.telemetry.disk import DiskSampler
from shikiru.telemetry.docker import DockerSampler
from shikiru.telemetry.resources import ResourceSampler
from shikiru.tmux.client import FORMAT_SESSION_NAME, Tmux, exact_target, tmux_key_label
from shikiru.tmux.layout import LayoutBuilder
from shikiru.tmux.session import PROJECT_DIR_ENV_VAR, SessionView, session_name
from shikiru.workspace.base_refs import BaseRefs, FetchedBaseRefs, LocalBaseRefs
from shikiru.workspace.caches import CacheCloner, default_cow_args
from shikiru.workspace.facts import GitFacts, TaskWorkObserver
from shikiru.workspace.git import Git
from shikiru.workspace.github import GitHub
from shikiru.workspace.messages import OperatorMessages
from shikiru.workspace.roots import TaskRootBuilder

_TMUX_PANE_ENVIRONMENT_VARIABLE = "TMUX_PANE"

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence
    from pathlib import Path

    from mcp.server.fastmcp import FastMCP
    from rich.console import RenderableType
    from textual.screen import ModalScreen

    from shikiru.config.settings import LoggingConfig, Settings
    from shikiru.engine.integration import IntegrationCoordinator

    StartEngine = Callable[[], Awaitable[StartResult]]


DASHBOARD_RESOURCES_WIDGET = "resources"


class _PaletteContext(Protocol):
    action: str | None
    task_id: str | None
    role: str | None
    drafts: tuple[object, ...]


async def dashboard_tmux_binding(
    tmux: Tmux,
    pane_target: str | None,
    project_root: Path,
    expected_session: str,
) -> tuple[str | None, Callable[[], Awaitable[bool]] | None]:
    """Return the session prefix and a revalidated action for this project pane."""
    if pane_target is None or not await tmux.pane_exists(pane_target):
        return None, None
    current_session = await tmux.display(pane_target, FORMAT_SESSION_NAME)
    if current_session != expected_session:
        return None, None
    session_target = exact_target(current_session)
    project_path = str(project_root)
    if await tmux.show_session_environment(session_target, PROJECT_DIR_ENV_VAR) != project_path:
        return None, None
    hint = tmux_key_label(await tmux.current_prefix(session_target))

    async def detach_project_client() -> bool:
        if not await tmux.pane_exists(pane_target):
            return False
        active_session = await tmux.display(pane_target, FORMAT_SESSION_NAME)
        if active_session != expected_session:
            return False
        active_session_target = exact_target(active_session)
        if (
            await tmux.show_session_environment(active_session_target, PROJECT_DIR_ENV_VAR)
            != project_path
        ):
            return False
        return await tmux.detach_client_for_pane(active_session_target, pane_target)

    return hint, detach_project_client


def build_clock() -> Clock:
    """Return the `Clock` implementation used by every real entry point."""
    return SystemClock()


def build_project_detectors(project_root: Path) -> ProjectDetectors:
    """Build the detector with one injected process, clock and git boundary."""
    runner = SubprocessRunner()
    plugins = PluginChecks(AgentRegistry(), runner, build_clock())
    timeouts = load_default_orchestration().timeouts
    return ProjectDetectors(
        project_root,
        git=Git(runner, command_timeout=timeouts.git_command),
        plugin_checks=plugins,
        cache_cloner=CacheCloner(
            runner, cow_args=default_cow_args(), copy_timeout=timeouts.cache_copy
        ),
    )


async def run_init_wizard(project_root: Path) -> InitWizardResult | None:
    """Run setup after the detector validates the repository prerequisites."""
    doctor_renderer = cast(
        "Callable[..., RenderableType]",
        import_module("shikiru.cli.render").__dict__["doctor_renderable"],
    )
    detectors = build_project_detectors(project_root)
    project = await detectors.detect()
    config_path = project_config_path(project_root)
    if await asyncio.to_thread(config_path.is_file):
        message = f"{config_path} already exists; refusing to overwrite it"
        raise ConfigError(message)
    wizard_type = import_module("shikiru.dashboard.wizard").__dict__["InitWizardApp"]
    wizard = wizard_type(
        project_root=project_root,
        project=project,
        detectors=detectors,
        doctor=build_doctor(project_root),
        doctor_renderer=doctor_renderer,
        orchestration_defaults=load_default_orchestration(),
    )
    return cast("InitWizardResult | None", await wizard.run_async(inline=False))


def build_doctor(project_root: Path) -> Doctor:
    """Build environment diagnostics with one injected process, clock and registry."""
    runner = SubprocessRunner()
    clock = build_clock()
    registry = AgentRegistry()
    timeouts = load_default_orchestration().timeouts
    return Doctor(
        project_root,
        cache_cloner=CacheCloner(
            runner, cow_args=default_cow_args(), copy_timeout=timeouts.cache_copy
        ),
        git=Git(runner, command_timeout=timeouts.git_command),
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
            await plugin_checks.check(name, config.options, settings.ladder_models(name))
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


def build_session_tools(
    overrides: Sequence[str] = (),
) -> tuple[SessionLister, SessionAttacher]:
    """Build the read-only session lister and process-replacing attach adapter."""
    runner = SubprocessRunner()
    clock = build_clock()
    tmux = Tmux(runner)
    session_settings = load_session_settings(overrides)

    def engine_up(project_root: Path) -> bool:
        return engine_is_running(engine_lock_path(project_root))

    def reader_for(project_root: Path) -> StateReader:
        return StateReader(Database(state_db_path(project_root)), clock)

    return (
        SessionLister(
            tmux=tmux,
            runner=runner,
            engine_up=engine_up,
            reader_factory=reader_for,
            remote_timeout_seconds=session_settings.ssh_timeout_seconds,
        ),
        SessionAttacher(runner, tmux, prompt_for_session),
    )


def build_project_starter(project_root: Path, overrides: Sequence[str]) -> StartEngine:
    """Build a lifecycle callback that starts this project without attaching a client."""
    lifecycle = build_lifecycle()

    async def start_engine() -> StartResult:
        return await lifecycle.start(project_root, overrides, attach=False)

    return start_engine


def _open_migrated_state_reader(project_root: Path, clock: Clock) -> StateReader:
    database = Database(state_db_path(project_root))
    database.connect().close()
    return StateReader(database, clock)


async def run_dashboard(
    project_root: Path,
    overrides: Sequence[str],
    focus_task: str | None,
) -> None:
    """Run the configured dashboard for one project entry point."""
    dashboard_app_module = import_module("shikiru.dashboard.app")
    dashboard_palette_module = import_module("shikiru.dashboard.palette")
    dashboard_actions_module = import_module("shikiru.dashboard.actions_list")
    dashboard_contract_module = import_module("shikiru.dashboard.contract")
    dashboard_resolution_module = import_module("shikiru.dashboard.resolution")
    dashboard_services_module = import_module("shikiru.dashboard.services")
    dashboard_views_module = import_module("shikiru.dashboard.views")
    dashboard_motion_module = import_module("shikiru.dashboard.motion")
    dashboard_activity_module = import_module("shikiru.dashboard.activity")
    clock = build_clock()
    runner = SubprocessRunner()
    state = _open_migrated_state_reader(project_root, clock)
    live_settings = import_module("shikiru.dashboard.live_settings").LiveSettings(
        state,
        lambda: load_settings(project_root, overrides),
    )
    settings = live_settings.current()
    git = Git(runner, command_timeout=settings.orchestration.timeouts.git_command)
    services = build_operator(project_root, settings, clock=clock)
    try:
        evidence_exporter = TaskEvidenceExporter(
            services.queries,
            LocalEvidenceWriter(project_root),
            clock,
        )
        lock_path = engine_lock_path(project_root)
        session_target = exact_target(session_name(settings.project.name))
        tmux = Tmux(runner)
        tmux_detach_hint, detach_tmux_client = await dashboard_tmux_binding(
            tmux,
            os.environ.get(_TMUX_PANE_ENVIRONMENT_VARIABLE),
            project_root,
            session_name(settings.project.name),
        )
        dashboard_services = dashboard_services_module.DashboardServices(
            dashboard_services_module.GitRepoStatusService(
                git,
                settings.repo,
                project_root,
                clock,
                cache_ttl_seconds=settings.dashboard.repo_status_cache_seconds,
            ),
            clock=clock,
            engine_up=lambda: engine_is_running(lock_path),
            engine_identity=lambda: EngineLock.identity_if_running(lock_path),
            project_root=project_root,
            state=services.reader,
            tmux=tmux,
            session_target=session_target,
            resources=ResourceSampler(clock, settings.orchestration.resources),
            docker=DockerSampler(
                runner,
                clock,
                refresh_seconds=settings.dashboard.docker_refresh_seconds,
                timeout_seconds=settings.dashboard.docker_timeout_seconds,
                project_roots=(
                    None if settings.dashboard.docker_scope == DOCKER_SCOPE_ALL else (project_root,)
                ),
            ),
            disk=DiskSampler(
                clock,
                project_root,
                refresh_seconds=settings.dashboard.disk_refresh_seconds,
                size_refresh_seconds=settings.dashboard.disk_size_refresh_seconds,
            ),
        )
        views = dashboard_views_module.StateDashboardViews(
            services.reader,
            focus_task=focus_task,
            settings=live_settings,
            sources=dashboard_services,
            queries=services.queries,
            clock=clock,
            motion=dashboard_motion_module.DashboardMotion(
                services.reader,
                project_root,
                live_settings,
                dashboard_activity_module.RoleViewTail(settings.dashboard.last_words_chars),
            ),
            work=dashboard_services_module.TaskWorkService(
                TaskWorkObserver(git),
                clock,
                cache_ttl_seconds=settings.dashboard.task_work_cache_seconds,
            ),
        )
        navigation = dashboard_services_module.TaskNavigation(
            tmux,
            services.queries,
            session_target,
        )

        app_instance = None
        resolution_drafts = dashboard_resolution_module.ResolutionDrafts()

        def resolution_screen(escalation_id: str) -> ModalScreen[bool]:
            return dashboard_resolution_module.ResolutionScreen(
                services.commands,
                services.queries,
                clock=clock,
                on_change=services.reader.watch,
                drafts=resolution_drafts,
                escalation_id=escalation_id,
            )

        async def push_palette(request: _PaletteContext) -> None:
            if app_instance is None:
                message = "dashboard palette is not mounted"
                raise ConfigError(message)
            dashboard = app_instance

            def popup(popup_request: _PaletteContext) -> ModalScreen[str | None]:
                return dashboard_palette_module.PaletteScreen(
                    services.commands,
                    services.queries,
                    tmux.select_pane,
                    on_change=services.reader.watch,
                    action_confirmation_seconds=(
                        live_settings.current().dashboard.action_confirmation_seconds
                    ),
                    initial_context=dashboard.attach_palette_drafts(popup_request),
                    start_engine=(
                        build_project_starter(project_root, overrides)
                        if popup_request.action
                        == dashboard_contract_module.PaletteAction.START.value
                        else None
                    ),
                    evidence_exporter=evidence_exporter,
                    remember_contextual_draft=dashboard.remember_contextual_palette_draft,
                )

            def popup_closed(queued: str | None) -> None:
                del queued
                dashboard.refresh_task_detail_after_resume()

            if request.action:
                await dashboard.push_screen(popup(request), callback=popup_closed)
                return
            actions = dashboard_actions_module.ActionsListScreen(
                services.queries,
                popup,
                request,
                on_change=services.reader.watch,
                action_confirmation_seconds=(
                    live_settings.current().dashboard.action_confirmation_seconds
                ),
                engine_state_refresh_hz=live_settings.current().dashboard.motion.refresh_hz,
                task_routes_available=True,
                project_diagnostics_available=(
                    DASHBOARD_RESOURCES_WIDGET in settings.dashboard.widgets
                ),
                extensions=dashboard.extension_entries(),
                resolution_screen=resolution_screen,
            )
            await dashboard.push_screen(actions, callback=dashboard.palette_return_callback())

        async def open_palette(action: str) -> None:
            await push_palette(dashboard_contract_module.PaletteRequest(action=action or None))

        app_instance = dashboard_app_module.DashboardApp(
            focus_task=focus_task,
            views=views,
            on_change=services.reader.watch,
            clock=clock,
            motion_interval_seconds=1 / settings.dashboard.motion.refresh_hz,
            settings=live_settings,
            open_palette=open_palette,
            widget_names=settings.dashboard.widgets,
            open_palette_context=push_palette,
            resolution_screen=resolution_screen,
            navigate_task_window=navigation.task_window,
            navigate_home=navigation.home,
            tmux_detach_hint=tmux_detach_hint,
            detach_tmux_client=detach_tmux_client,
        )
        await app_instance.run_async()
    finally:
        services.close()
        state.close()


async def run_palette(
    project_root: Path, overrides: Sequence[str], origin_pane: str | None = None
) -> None:
    """Run the operator palette as a popup using project-local operator services."""
    palette_module = import_module("shikiru.dashboard.palette_app")
    resolution_module = import_module("shikiru.dashboard.resolution")
    palette_request = import_module("shikiru.dashboard.contract").PaletteRequest
    clock = build_clock()
    settings = load_settings(project_root, overrides)
    start_engine = build_project_starter(project_root, overrides)
    services = build_operator(project_root, settings, clock=clock)
    try:
        evidence_exporter = TaskEvidenceExporter(
            services.queries,
            LocalEvidenceWriter(project_root),
            clock,
        )
        task_role = (
            services.queries.task_role_for_pane(origin_pane) if origin_pane is not None else None
        )
        initial_context = None
        if task_role is not None:
            task_id, role = task_role
            initial_context = palette_request(task_id=task_id, role=role.value)
        resolution_drafts = resolution_module.ResolutionDrafts()

        def resolution_screen(escalation_id: str) -> ModalScreen[bool]:
            return resolution_module.ResolutionScreen(
                services.commands,
                services.queries,
                clock=clock,
                on_change=services.reader.watch,
                drafts=resolution_drafts,
                escalation_id=escalation_id,
            )

        app_instance = palette_module.PaletteApp(
            services.commands,
            services.queries,
            Tmux(SubprocessRunner()).select_pane,
            on_change=services.reader.watch,
            action_confirmation_seconds=settings.dashboard.action_confirmation_seconds,
            engine_state_refresh_hz=settings.dashboard.motion.refresh_hz,
            start_engine=start_engine,
            initial_context=initial_context,
            evidence_exporter=evidence_exporter,
            resolution_screen=resolution_screen,
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
        engine_identity=lambda: EngineLock.identity_if_running(engine_lock),
        max_parallel=resolved_settings.orchestration.max_parallel,
        clock=resolved_clock,
    )
    commands = OperatorCommands(
        command_queue,
        resolved_clock,
        engine_up=engine_state,
    )
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


def _build_flow(
    *,
    project_root: Path,
    tasks: TaskStore,
    events: EventLog,
    runs: RunStore,
    sessions: SessionManager,
    turns: TurnRunner,
    contexts: TurnContextPreparer,
    recovery: TurnRecovery,
    writer: TransitionWriter,
    raiser: EscalationRaiser,
    evidence: EvidenceCollector,
    facts: TaskFacts,
    history: ReportHistory,
    integrator: IntegrationCoordinator,
    settings: Settings,
) -> TaskFlow:
    filer = EscalationFiler(raiser=raiser, evidence=evidence, writer=writer, tasks=tasks)
    turn_loop = TurnLoop(
        tasks=tasks,
        events=events,
        sessions=sessions,
        turns=turns,
        contexts=contexts,
        recovery=recovery,
        escalations=filer,
        runs=runs,
    )
    steps = StepRunner(
        loop=turn_loop,
        acceptance=AcceptanceGuard(events=events, facts=facts),
        escalations=filer,
        writer=writer,
        events=events,
        settings=settings,
    )
    return TaskFlow(
        tasks=tasks,
        writer=writer,
        steps=steps,
        turn_loop=turn_loop,
        escalations=filer,
        integration_turns=IntegrationTurns(
            coordinator=integrator,
            events=events,
            history=history,
            project_root=project_root,
            tasks=tasks,
            steps=steps,
            escalations=filer,
        ),
    )


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
    helper_entries = tuple(
        HelperEntry(rung, agent_pool.get(rung.agent))
        for rung in resolved_settings.roles.helper.ladder
    )
    helper = Helper(helper_entries, resolved_clock)
    connection = Database(state_db_path(project_root)).connect()
    tasks = TaskStore(connection, resolved_clock)
    runs = RunStore(connection, resolved_clock)
    events = EventLog(connection, resolved_clock)
    quotas = QuotaStore(connection)
    escalations_store = EscalationStore(connection, resolved_clock)
    command_queue = CommandQueue(connection, resolved_clock)

    decorator = CosmeticDecorator(resolved_tmux)
    titles = TaskTitles(decorator, runs, tasks, resolved_settings)

    sessions = SessionManager(
        runs=runs,
        agents=agent_pool,
        project_root=project_root,
        settings=resolved_settings,
    )
    turn_processes = TurnProcesses(
        runner=resolved_runner, clock=resolved_clock, project_root=project_root
    )
    monitor = TurnMonitor(
        sessions=sessions,
        runs=runs,
        quotas=quotas,
        events=events,
        clock=resolved_clock,
        settings=resolved_settings,
        decorator=decorator,
        refresh_titles=titles.refresh,
        processes=turn_processes,
    )

    target = exact_target(session_name(resolved_settings.project.name))
    logger, log_handler = _configure_engine_logging(project_root, resolved_settings.logging)
    _tighten_state_dir(project_root, logger)
    fault_reporter = FaultReporter(
        events=events,
        clock=resolved_clock,
        settings=resolved_settings.orchestration.faults,
        logger=logger,
    )
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
        faults=fault_reporter,
        refresh_titles=titles.refresh,
    )
    session_guard = SessionGuard(
        tmux=resolved_tmux,
        view=SessionView(resolved_tmux),
        session_target=target,
        project_name=resolved_settings.project.name,
        project_root=project_root,
        home_command=home_command(project_root),
        logger=logger,
    )
    git = Git(
        resolved_runner,
        command_timeout=resolved_settings.orchestration.timeouts.git_command,
    )
    fetched_bases = FetchedBaseRefs(
        git,
        project_root,
        resolved_settings.repo.remote,
        fetch_timeout=resolved_settings.orchestration.network.fetch_timeout_seconds,
    )
    roots = TaskRootBuilder(
        git,
        project_root,
        resolved_settings.repo,
        CacheCloner(
            resolved_runner,
            cow_args=default_cow_args(),
            copy_timeout=resolved_settings.orchestration.timeouts.cache_copy,
        ),
        fetched_bases
        if resolved_settings.orchestration.integration == PULL_REQUEST_INTEGRATION
        else LocalBaseRefs(),
        trackers=resolved_settings.project.trackers,
    )
    pull_request_closing = PullRequestClosing(
        closer=PullRequestCloser(
            github=GitHub(resolved_runner), events=events, project_root=project_root
        ),
        tasks=tasks,
        faults=fault_reporter,
        clock=resolved_clock,
        settings=resolved_settings.orchestration,
    )
    task_finisher = TaskFinisher(sessions, layout, roots, pull_request_closing)
    history = ReportHistory(project_root)
    task_facts = TaskFacts(
        git_facts=GitFacts(git), project_root=project_root, settings=resolved_settings
    )
    evidence = EvidenceCollector(history=history, facts=task_facts, project_root=project_root)
    escalation_raiser = EscalationRaiser(
        escalations_store,
        events,
        runs,
        project_root,
        atomically=lambda: transaction(connection),
        titles=titles,
        evidence=evidence,
        settings=resolved_settings,
    )
    integrator = _build_integrator(
        project_root=project_root,
        settings=resolved_settings,
        clock=resolved_clock,
        git=git,
        runner=resolved_runner,
        fetched_bases=fetched_bases,
        tasks=tasks,
        events=events,
        faults=fault_reporter,
        task_finisher=task_finisher,
    )
    contexts = TurnContextPreparer(
        history=history,
        escalation_store=escalations_store,
        facts=task_facts,
        settings=resolved_settings,
        project_root=project_root,
    )
    messages = OperatorMessages(project_root)
    writer = TransitionWriter(tasks=tasks, events=events, titles=titles, settings=resolved_settings)
    recovery = TurnRecovery(
        tasks=tasks,
        events=events,
        quota=quotas,
        clock=resolved_clock,
        writer=writer,
        settings=resolved_settings,
    )
    flow = _build_flow(
        project_root=project_root,
        tasks=tasks,
        events=events,
        runs=runs,
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
            tasks=tasks,
            project_root=project_root,
            settings=resolved_settings,
        ),
        contexts=contexts,
        recovery=recovery,
        writer=writer,
        raiser=escalation_raiser,
        evidence=evidence,
        facts=task_facts,
        history=history,
        integrator=integrator,
        settings=resolved_settings,
    )
    task_supervisor = TaskSupervisor(tasks, flow, task_finisher)
    handler_context = HandlerContext(
        tasks=tasks,
        escalations=escalations_store,
        events=events,
        runs=runs,
        project_root=project_root,
        cancel_task=task_supervisor.cancel_task,
        start_task=task_supervisor.start_task,
        finish_task=task_finisher.finish_task,
        stop_engine=task_supervisor.request_stop,
        refresh_titles=titles.refresh,
        atomically=lambda: transaction(connection),
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
        settings=resolved_settings,
        cancel_task=task_supervisor.cancel_task,
        start_task=task_supervisor.start_task,
        refresh_titles=titles.refresh,
        atomically=lambda: transaction(connection),
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
    sampler = ResourceSampler(resolved_clock, resolved_settings.orchestration.resources)
    notifier = Notifier(
        resolved_tmux,
        resolved_runner,
        fault_reporter,
        session_target=target,
        command=resolved_settings.notify.command,
        timeout_seconds=resolved_settings.notify.timeout_seconds,
    )
    scheduler = Scheduler(
        tasks=tasks,
        settings=resolved_settings,
        quotas=quotas,
        resources=sampler,
        clock=resolved_clock,
        layout=layout,
        roots=RootPreparation(
            roots,
            resolved_clock,
            budget_seconds=resolved_settings.orchestration.dispatch_budget_seconds,
        ),
        events=events,
        on_dependency_cancelled=DependencyEscalator(writer, escalation_raiser),
        on_dispatch_failure=DispatchFailureEscalator(writer, escalation_raiser),
        titles=titles,
        faults=fault_reporter,
    )
    notifications = NotificationDelivery(notifier)
    return Engine(
        lock_path=engine_lock_path(project_root),
        command_queue=command_queue,
        tasks=tasks,
        events=events,
        scheduler=scheduler,
        task_supervisor=task_supervisor,
        handlers=handlers,
        layout=layout,
        session_guard=session_guard,
        tick_guard=TickGuard(
            reporter=fault_reporter,
            clock=resolved_clock,
            settings=resolved_settings.orchestration.faults,
            logger=logger,
            scope="engine tick",
        ),
        command_guard=TickGuard(
            reporter=fault_reporter,
            clock=resolved_clock,
            settings=resolved_settings.orchestration.faults,
            logger=logger,
            scope="command loop",
        ),
        loop_guard=TickGuard(
            reporter=fault_reporter,
            clock=resolved_clock,
            settings=resolved_settings.orchestration.faults,
            logger=logger,
            scope="engine loop",
        ),
        reconciler=Reconciler(
            tasks=tasks,
            layout=layout,
            processes=turn_processes,
            resume_task=task_supervisor.resume_task,
            finish_task=task_finisher.finish_task,
            restore_escalation=escalation_raiser.ensure_actionable,
            restore_pull_request_closes=pull_request_closing.restore,
            faults=fault_reporter,
            project_root=project_root,
        ),
        notifications=notifications,
        notification_feed=NotificationFeed(events, NotificationCursor(connection), notifications),
        retention=Retention(
            store=StateRetention(connection),
            tasks=tasks,
            cursor=NotificationCursor(connection),
            project_root=project_root,
            has_open_pull_request=pull_request_closing.has_open,
            clock=resolved_clock,
            settings=resolved_settings.retention,
        ),
        retention_guard=TickGuard(
            reporter=fault_reporter,
            clock=resolved_clock,
            settings=resolved_settings.orchestration.faults,
            logger=logger,
            scope="retention",
        ),
        pull_request_closing=pull_request_closing,
        settings=resolved_settings,
        clock=resolved_clock,
        close_state=connection.close,
        logger=logger,
        log_handler=log_handler,
    )


def build_engine_process(project_root: Path, settings: Settings) -> SignalledEngine:
    """Build the engine as the `shikiru engine` process runs it: stopped by SIGTERM and SIGINT."""
    clock = build_clock()
    runner = SubprocessRunner()
    return SignalledEngine(
        engine=build_engine(project_root, settings, clock=clock, runner=runner),
        turn_processes=TurnProcesses(runner=runner, clock=clock, project_root=project_root),
        clock=clock,
        shutdown_seconds=settings.orchestration.shutdown_seconds,
    )


def _build_integrator(
    *,
    project_root: Path,
    settings: Settings,
    clock: Clock,
    git: Git,
    runner: SubprocessRunner,
    fetched_bases: FetchedBaseRefs,
    tasks: TaskStore,
    events: EventLog,
    faults: FaultReporter,
    task_finisher: TaskFinisher,
) -> Integrator:
    strategy: IntegrationStrategy
    base_refs: BaseRefs
    match settings.orchestration.integration:
        case "merge":
            base_refs = LocalBaseRefs()
            strategy = LocalMerge(
                git,
                clock,
                events=events,
                project_root=project_root,
                integration_wait=settings.orchestration.timeouts.integration_wait,
            )
        case "pr":
            base_refs = fetched_bases
            strategy = PullRequest(
                git=git,
                github=GitHub(runner),
                clock=clock,
                tasks=tasks,
                events=events,
                project_root=project_root,
                remote=settings.repo.remote,
                base_refs=fetched_bases,
                faults=faults,
                settings=settings.orchestration,
            )
    return Integrator(
        git=git,
        strategy=strategy,
        base_refs=base_refs,
        lock=asyncio.Lock(),
        project_root=project_root,
        tasks=tasks,
        events=events,
        finish_task=task_finisher.finish_task,
    )


def _tighten_state_dir(project_root: Path, logger: logging.Logger) -> None:
    state_dir = project_state_dir(project_root)
    tightened = tighten_state_dir(state_dir)
    if tightened:
        logger.info(
            "removed group and other access from %d paths under %s", len(tightened), state_dir
        )


def _configure_engine_logging(
    project_root: Path, config: LoggingConfig
) -> tuple[logging.Logger, logging.FileHandler]:
    log_path = engine_log_path(project_root)
    make_private_dir(log_path.parent)
    logger = logging.getLogger(ENGINE_LOGGER_NAME)
    handler = PrivateRotatingFileHandler(
        log_path, maxBytes=config.engine_log_max_bytes, backupCount=config.engine_log_backups
    )
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger, handler
