from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import pytest
from fake_agent.scripted import TASK_SCRIPT_DIR_ENV_VAR, role_script_env_var, task_script_path
from textual.widgets import Button, Input, ListView, Select, Static, TextArea

from shikiru.bootstrap import build_engine
from shikiru.config.loader import load_settings
from shikiru.dashboard.activity import RoleViewTail
from shikiru.dashboard.app import DashboardApp
from shikiru.dashboard.detail import (
    TASK_DETAIL_EXPORT_BUTTON,
    TASK_DETAIL_OWNERSHIP,
    TaskDetailScreen,
)
from shikiru.dashboard.motion import DashboardMotion
from shikiru.dashboard.palette import PaletteScreen
from shikiru.dashboard.views import StateDashboardViews
from shikiru.domain.commands import (
    Cancel,
    CancelResolution,
    Command,
    HandBack,
    Pause,
    ResolutionKind,
    Submit,
    TakeOver,
)
from shikiru.domain.escalation_evidence import parse_escalation_packet
from shikiru.domain.events import EventKind
from shikiru.domain.steps import CONSULT_LIMIT
from shikiru.domain.tasks import Hold, Mode, Role, Status, StatusReason, Step
from shikiru.engine.daemon import Engine, engine_is_running
from shikiru.operator.commands import OperatorCommands
from shikiru.operator.evidence_export import EXPORT_FORMAT_JSON, TaskEvidenceExporter
from shikiru.operator.evidence_writer import LocalEvidenceWriter
from shikiru.operator.queries import OperatorQueries
from shikiru.paths import engine_lock_path, run_dir, state_db_path, task_root, turns_dir
from shikiru.store.commands import CommandQueue, CommandStatus
from shikiru.store.database import Database
from shikiru.store.escalations import EscalationStore
from shikiru.store.events import EventLog
from shikiru.store.reader import StateReader
from shikiru.store.runs import RunStore
from shikiru.store.tasks import TaskStore
from shikiru.tmux.session import SessionView
from tests.support.clock import FakeClock
from tests.support.config import write_minimal_project
from tests.support.dashboard import StubServices, choose_focus_view
from tests.support.engine import (
    build_registry,
    build_tmux,
    engine_settings,
    set_role_script,
)
from tests.support.factories import CPU_UNCAPPED_PCT, TASK_CREATED_AT
from tests.support.flow import init_project_repo
from tests.support.store import reopen_command
from tests.support.waiting import wait_until_advancing

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

    from rich.text import Text

    from shikiru.config.settings import Settings
    from shikiru.dashboard.contract import PaletteRequest
    from shikiru.domain.escalation import EscalationOption
    from shikiru.store.commands import CommandOutcome
    from tests.support.store import Connection
    from tests.support.tmux import TmuxServer

pytestmark = pytest.mark.asyncio

HOME_SIZE = (120, 40)
TASK_SIZE = (120, 40)
FLOW_WAIT_SECONDS = 45
CLOCK_STEP_SECONDS = 0.25
VERIFY_RETRY_STEPS: list[dict[str, object]] = [{"kind": "accept"}, {"kind": "stall"}]
LEADER_REWORK_STEPS: list[dict[str, object]] = [{"kind": "changes_requested"}]
DECISION_QUESTION = "Which parser behavior should be retained?"
NEEDS_DECISION_STEP: dict[str, object] = {"kind": "needs_decision", "question": DECISION_QUESTION}
STALL_STEP: dict[str, object] = {"kind": "stall"}
ESCALATION_IMPLEMENTER_STEPS = [NEEDS_DECISION_STEP] * (CONSULT_LIMIT + 1) + [STALL_STEP]
REQUEST_MESSAGE = "Review the recorded changes before the next turn."
GUIDANCE_MESSAGE = "Keep the existing parser contract."
EXPORT_DESTINATION = "exports/terminal-task.json"


def _plain(widget: Static) -> str:
    return cast("Text", widget.render()).plain


def _assert_escalation_resolution_semantics(
    packet_text: str,
    expected_question: str,
    options: tuple[EscalationOption, ...],
    resolution: Select[str],
) -> None:
    packet = parse_escalation_packet(packet_text)
    assert packet.question is not None
    assert expected_question in packet.question
    assert packet.agent_request is None
    assert not packet.errors
    assert any(item.kind is ResolutionKind.ANSWER for item in packet.resolutions)
    assert any(option.label is ResolutionKind.ANSWER and option.recommended for option in options)
    assert resolution.value == ResolutionKind.ANSWER.value


@dataclass
class WorkspaceRuntime:
    project_root: Path
    settings: Settings
    clock: FakeClock
    tmux_server: TmuxServer
    connection: Connection
    reader: StateReader
    queue: CommandQueue
    queries: OperatorQueries
    commands: OperatorCommands
    exporter: TaskEvidenceExporter
    engine: Engine | None = None
    engine_task: asyncio.Task[None] | None = None

    @property
    def engine_is_up(self) -> bool:
        return engine_is_running(engine_lock_path(self.project_root))

    async def start_engine(self) -> None:
        assert self.engine_task is None
        self.engine = build_engine(
            self.project_root,
            self.settings,
            clock=self.clock,
            tmux=build_tmux(self.tmux_server),
            registry=build_registry(),
        )
        self.engine_task = asyncio.create_task(self.engine.run())
        await wait_until_advancing(
            self.clock,
            lambda: self.engine_is_up,
            FLOW_WAIT_SECONDS,
            step_seconds=CLOCK_STEP_SECONDS,
        )

    async def stop_engine(self) -> None:
        if self.engine is None or self.engine_task is None:
            return
        self.engine.request_stop()
        await wait_until_advancing(
            self.clock,
            self.engine_task.done,
            FLOW_WAIT_SECONDS,
            step_seconds=CLOCK_STEP_SECONDS,
        )
        await self.engine_task
        self.engine = None
        self.engine_task = None

    async def wait_for(
        self,
        predicate: Callable[[], bool | Awaitable[bool]],
        timeout: float = FLOW_WAIT_SECONDS,
    ) -> None:
        await wait_until_advancing(
            self.clock,
            predicate,
            timeout,
            step_seconds=CLOCK_STEP_SECONDS,
        )

    async def send(self, command: Command) -> CommandOutcome:
        queued = self.queue.append(command)

        async def completed() -> bool:
            result = self.queue.status(queued.id)
            return result is not None and result.status is not CommandStatus.PENDING

        await self.wait_for(completed)
        outcome = self.queue.status(queued.id)
        assert outcome is not None
        return outcome

    def app(self, *, focus_task: str | None = None) -> DashboardApp:
        source = StubServices(engine_running=self.engine_is_up)
        views = StateDashboardViews(
            self.reader,
            focus_task=focus_task,
            settings=self.settings,
            sources=source,
            queries=self.queries,
            clock=self.clock,
            motion=DashboardMotion(
                self.reader,
                self.project_root,
                self.settings,
                RoleViewTail(),
            ),
        )
        app_ref: list[DashboardApp] = []

        async def select_pane(_pane_id: str) -> None:
            raise AssertionError

        async def open_palette(action: str) -> None:
            await app_ref[0].push_screen(
                PaletteScreen(
                    self.commands,
                    self.queries,
                    select_pane,
                    on_change=self.reader.watch,
                    initial_action=action,
                    evidence_exporter=self.exporter,
                )
            )

        async def open_palette_context(request: PaletteRequest) -> None:
            await app_ref[0].push_screen(
                PaletteScreen(
                    self.commands,
                    self.queries,
                    select_pane,
                    on_change=self.reader.watch,
                    initial_context=request,
                    evidence_exporter=self.exporter,
                )
            )

        app = DashboardApp(
            focus_task=focus_task,
            views=views,
            on_change=self.reader.watch,
            clock=self.clock,
            motion_interval_seconds=None,
            resources_refresh_seconds=self.settings.orchestration.resources.sample_seconds,
            docker_refresh_seconds=self.settings.dashboard.docker_refresh_seconds,
            timeline_refresh_seconds=self.settings.dashboard.timeline_refresh_seconds,
            open_palette=open_palette,
            open_palette_context=open_palette_context,
            widget_names=self.settings.dashboard.widgets,
        )
        app_ref.append(app)
        return app

    async def close(self) -> None:
        await self.stop_engine()
        self.reader.close()
        self.connection.close()


async def _runtime(project_root: Path, tmux_server: TmuxServer) -> WorkspaceRuntime:
    write_minimal_project(project_root)
    await init_project_repo(project_root)
    settings = engine_settings(max_cpu_pct=CPU_UNCAPPED_PCT, max_parallel=2)
    defaults = load_settings(project_root)
    settings = settings.model_copy(
        update={
            "dashboard": settings.dashboard.model_copy(
                update={"widgets": defaults.dashboard.widgets}
            )
        }
    )
    tmux = build_tmux(tmux_server)
    await SessionView(tmux).ensure(settings.project.name, project_root, home_command=("/bin/sh",))
    clock = FakeClock(TASK_CREATED_AT)
    database = Database(state_db_path(project_root))
    connection = database.connect()
    reader = StateReader(Database(state_db_path(project_root)), clock)

    def engine_is_up() -> bool:
        return engine_is_running(engine_lock_path(project_root))

    queue = CommandQueue(connection, clock)
    queries = OperatorQueries(
        reader,
        project=settings.project.name,
        project_root=project_root,
        engine_up=engine_is_up,
        max_parallel=settings.orchestration.max_parallel,
        clock=clock,
    )
    commands = OperatorCommands(queue, clock, engine_up=engine_is_up)
    exporter = TaskEvidenceExporter(queries, LocalEvidenceWriter(project_root), clock)
    return WorkspaceRuntime(
        project_root,
        settings,
        clock,
        tmux_server,
        connection,
        reader,
        queue,
        queries,
        commands,
        exporter,
    )


async def _submit(runtime: WorkspaceRuntime, title: str, request: str) -> str:
    queued = runtime.queue.append(Submit(title=title, request=request, mode=Mode.IMPLEMENT))

    async def completed() -> bool:
        outcome = runtime.queue.status(queued.id)
        return outcome is not None and outcome.status is CommandStatus.DONE

    await runtime.wait_for(completed)
    outcome = runtime.queue.status(queued.id)
    assert outcome is not None
    assert outcome.result is not None
    return str(outcome.result["task_id"])


async def _submit_scripted_task(
    runtime: WorkspaceRuntime,
    script_dir: Path,
    title: str,
    request: str,
    scripts: dict[Role, list[dict[str, object]]],
    *,
    mode: Mode | None = None,
) -> str:
    queued = runtime.queue.append(Submit(title=title, request=request, mode=mode))
    assert isinstance(queued.command, Submit)
    task_id = queued.command.task_id
    assert task_id is not None
    task_script_path(str(script_dir), task_id).write_text(
        json.dumps({role.value: steps for role, steps in scripts.items()})
    )
    await runtime.wait_for(
        lambda: (
            (outcome := runtime.queue.status(queued.id)) is not None
            and outcome.status is CommandStatus.DONE
        )
    )
    return task_id


async def _wait_for_task(runtime: WorkspaceRuntime, task_id: str, status: Status) -> None:
    await runtime.wait_for(
        lambda: (
            (task := TaskStore(runtime.connection, runtime.clock).get(task_id)) is not None
            and task.status is status
        )
    )


async def _wait_for_terminal_task_cleanup(runtime: WorkspaceRuntime, task_id: str) -> None:
    await runtime.wait_for(lambda: not task_root(runtime.project_root, task_id).exists())
    task = TaskStore(runtime.connection, runtime.clock).require(task_id)
    assert task.window_id is not None

    async def task_window_closed() -> bool:
        windows = await runtime.tmux_server.run(
            "list-windows",
            "-t",
            f"=[shikiru] {runtime.settings.project.name}",
            "-F",
            "#{window_name}",
        )
        return not any(name.startswith(task_id) for name in windows.stdout.splitlines())

    await runtime.wait_for(task_window_closed)


async def _export_terminal_task(
    runtime: WorkspaceRuntime,
    task_id: str,
    status: Status,
    destination: str,
) -> dict[str, Any]:
    app = runtime.app(focus_task=task_id)
    async with app.run_test(size=TASK_SIZE) as pilot:
        identity = app.query_one("#task-detail-identity", Static)
        await runtime.wait_for(
            lambda: task_id in _plain(identity) and status.value in _plain(identity)
        )
        app.query_one(f"#{TASK_DETAIL_EXPORT_BUTTON}", Button).focus()
        await pilot.press("enter")
        await runtime.wait_for(lambda: isinstance(app.screen, PaletteScreen))
        palette = cast("PaletteScreen", app.screen)
        palette.query_one("#export-destination", Input).value = destination
        cast("Select[str]", palette.query_one("#export-format", Select)).value = EXPORT_FORMAT_JSON
        cast("Select[str]", palette.query_one("#export-overwrite", Select)).value = "refuse"
        cast("Select[str]", palette.query_one("#export-signals", Select)).value = "exclude"
        await pilot.click("#palette-submit")
        await runtime.wait_for(
            lambda: "Exported JSON" in _plain(palette.query_one("#palette-result", Static))
        )
    return cast("dict[str, Any]", json.loads((runtime.project_root / destination).read_text()))


async def test_fake_agent_workspace_finds_verdict_and_applies_contextual_message(
    tmp_path: Path,
    tmux_server: TmuxServer,
) -> None:
    runtime = await _runtime(tmp_path, tmux_server)
    try:
        await set_role_script(tmux_server, Role.IMPLEMENTER, VERIFY_RETRY_STEPS)
        await set_role_script(tmux_server, Role.LEADER, LEADER_REWORK_STEPS)
        await runtime.start_engine()
        title = "Find the strict parser task"
        task_id = await _submit(runtime, title, "Review strict parser handling.")

        async def rework_is_live() -> bool:
            task = TaskStore(runtime.connection, runtime.clock).get(task_id)
            verdicts = EventLog(runtime.connection, runtime.clock).since(
                0, kinds=[EventKind.VERDICT], task_id=task_id
            )
            return (
                task is not None
                and task.status is Status.RUNNING
                and task.step is Step.IMPLEMENT
                and bool(verdicts)
            )

        await runtime.wait_for(rework_is_live)
        app = runtime.app()
        async with app.run_test(size=HOME_SIZE) as pilot:
            search = app.query_one("#task-browser-search", Input)
            await pilot.press("/")
            assert app.focused is search
            search.value = title
            await pilot.pause()
            await runtime.wait_for(lambda: bool(app.query_one(f"#task-row-{task_id}")))
            task_list = app.query_one("#task-browser-list", ListView)
            await pilot.press("enter")
            await runtime.wait_for(lambda: app.focused is task_list)
            await pilot.press("enter")
            await runtime.wait_for(lambda: isinstance(app.screen, TaskDetailScreen))
            detail_verdict = app.screen.query_one("#task-detail-verdict", Static)
            await runtime.wait_for(lambda: "changes_requested" in _plain(detail_verdict))
            await pilot.press("ctrl+p")
            await runtime.wait_for(lambda: isinstance(app.screen, PaletteScreen))
            palette = cast("PaletteScreen", app.screen)
            assert palette.query_one("#task-id", Input).value == task_id
            assert cast("Select[str]", palette.query_one("#role", Select)).value == (
                Role.IMPLEMENTER.value
            )
            cast("Select[str]", palette.query_one("#action", Select)).value = "message"
            await pilot.pause()
            palette.query_one("#message", TextArea).text = REQUEST_MESSAGE
            await pilot.click("#palette-submit")
            await runtime.wait_for(
                lambda: any(
                    outcome.status is CommandStatus.DONE
                    for outcome in runtime.queries.command_history(limit=20).entries
                )
            )
            await runtime.wait_for(
                lambda: "Applied" in _plain(palette.query_one("#palette-result", Static))
            )
            delivered = list(
                (run_dir(tmp_path, task_id) / "messages" / Role.IMPLEMENTER.value).glob("*.md")
            )
            assert len(delivered) == 1
            assert delivered[0].read_text() == REQUEST_MESSAGE
    finally:
        await runtime.close()


async def test_fake_agent_escalation_updates_an_open_resolution_form_from_another_operator(
    tmp_path: Path,
    tmux_server: TmuxServer,
) -> None:
    runtime = await _runtime(tmp_path, tmux_server)
    external_connection = None
    try:
        await set_role_script(tmux_server, Role.IMPLEMENTER, ESCALATION_IMPLEMENTER_STEPS)
        await set_role_script(tmux_server, Role.LEADER, [{"kind": "accept"}])
        await runtime.start_engine()
        task_id = await _submit(runtime, "Escalate parser choice", "Choose parser behavior.")

        async def escalation_open() -> bool:
            return bool(EscalationStore(runtime.connection, runtime.clock).list_open())

        await runtime.wait_for(escalation_open)
        escalation = EscalationStore(runtime.connection, runtime.clock).list_open()[0]
        app = runtime.app(focus_task=task_id)
        async with app.run_test(size=TASK_SIZE) as pilot:
            await pilot.press("ctrl+p")
            await runtime.wait_for(lambda: isinstance(app.screen, PaletteScreen))
            palette = cast("PaletteScreen", app.screen)
            cast("Select[str]", palette.query_one("#action", Select)).value = "resolve"
            await pilot.pause()
            cast("Select[str]", palette.query_one("#escalation", Select)).value = escalation.id
            await pilot.pause()
            packet_view = runtime.queries.get_escalation(escalation.id)
            assert packet_view is not None
            assert packet_view.packet is not None
            resolution = cast("Select[str]", palette.query_one("#resolution", Select))
            _assert_escalation_resolution_semantics(
                packet_view.packet,
                DECISION_QUESTION,
                escalation.options,
                resolution,
            )
            answer = palette.query_one("#resolution-text", TextArea)
            answer.text = GUIDANCE_MESSAGE
            summary = _plain(palette.query_one("#palette-escalation-summary", Static))
            assert DECISION_QUESTION in summary
            external_connection = Database(state_db_path(tmp_path)).connect()
            external_queue = CommandQueue(external_connection, runtime.clock)
            external_commands = OperatorCommands(
                external_queue,
                runtime.clock,
                engine_up=lambda: runtime.engine_is_up,
            )
            external_send = asyncio.create_task(
                external_commands.resolve_escalation(escalation.id, CancelResolution())
            )
            await runtime.wait_for(external_send.done)
            external_ack = await external_send
            assert external_ack.status.value == "done"
            await runtime.wait_for(
                lambda: (
                    palette.query_one("#palette-submit", Button).disabled
                    and "resolved"
                    in _plain(palette.query_one("#palette-escalation-status", Static)).lower()
                )
            )
            assert answer.text == GUIDANCE_MESSAGE
            assert (
                "answer" in _plain(palette.query_one("#palette-escalation-summary", Static)).lower()
            )
            await _wait_for_task(runtime, task_id, Status.CANCELLED)
    finally:
        if external_connection is not None:
            external_connection.close()
        await runtime.close()


async def test_fake_agent_task_takeover_leaves_other_task_progress_and_pause_visible(
    tmp_path: Path,
    tmux_server: TmuxServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = await _runtime(tmp_path, tmux_server)
    try:
        script_dir = tmp_path / "task-scripts"
        script_dir.mkdir()
        monkeypatch.setenv(TASK_SCRIPT_DIR_ENV_VAR, str(script_dir))
        monkeypatch.setenv(role_script_env_var(Role.LEADER.value), json.dumps([{"kind": "accept"}]))
        await runtime.start_engine()

        held_task_id = await _submit_scripted_task(
            runtime,
            script_dir,
            "Hold this implementation",
            "Inspect this task.",
            {Role.IMPLEMENTER: [{"kind": "stall"}]},
        )
        progressing_task_id = await _submit_scripted_task(
            runtime,
            script_dir,
            "Continue the other task",
            "Finish this task.",
            {Role.IMPLEMENTER: [{"kind": "accept"}]},
        )

        async def held_task_has_session() -> bool:
            run = RunStore(runtime.connection, runtime.clock).get_live(
                held_task_id, Role.IMPLEMENTER
            )
            return run is not None and run.session_id is not None

        await runtime.wait_for(held_task_has_session)
        paused = await runtime.send(Pause(task_id=held_task_id))
        takeover = await runtime.send(TakeOver(task_id=held_task_id, role=Role.IMPLEMENTER))
        assert paused.status is CommandStatus.DONE
        assert takeover.status is CommandStatus.DONE
        await _wait_for_task(runtime, progressing_task_id, Status.DONE)
        owned = TaskStore(runtime.connection, runtime.clock).get(held_task_id)
        assert owned is not None
        assert Hold.INTERVENTION in owned.holds
        assert Hold.PAUSED in owned.holds
        handed_back = await runtime.send(HandBack(task_id=held_task_id, role=Role.IMPLEMENTER))
        assert handed_back.status is CommandStatus.DONE
        await runtime.wait_for(
            lambda: (
                (current := TaskStore(runtime.connection, runtime.clock).get(held_task_id))
                is not None
                and Hold.INTERVENTION not in current.holds
                and Hold.PAUSED in current.holds
            )
        )
        app = runtime.app(focus_task=held_task_id)
        async with app.run_test(size=TASK_SIZE):
            ownership = app.query_one(f"#{TASK_DETAIL_OWNERSHIP}", Static)
            await runtime.wait_for(lambda: "paused" in _plain(ownership).lower())
            assert "operator owns" not in _plain(ownership).lower()
            progressed = TaskStore(runtime.connection, runtime.clock).get(progressing_task_id)
            assert progressed is not None
            assert progressed.status is Status.DONE
    finally:
        await runtime.close()


async def test_dashboard_queued_submit_survives_engine_and_dashboard_restarts_once(
    tmp_path: Path,
    tmux_server: TmuxServer,
) -> None:
    runtime = await _runtime(tmp_path, tmux_server)
    try:
        app = runtime.app()
        async with app.run_test(size=HOME_SIZE) as pilot:
            await pilot.press("ctrl+p")
            await runtime.wait_for(lambda: isinstance(app.screen, PaletteScreen))
            palette = cast("PaletteScreen", app.screen)
            cast("Select[str]", palette.query_one("#action", Select)).value = "submit"
            await pilot.pause()
            palette.query_one(
                "#request", TextArea
            ).text = "Queue this task while the engine is stopped."
            await pilot.click("#palette-submit")
            await runtime.wait_for(lambda: bool(runtime.queue.pending()))
            pending = runtime.queue.pending()[0]
            await runtime.wait_for(
                lambda: "Pending" in _plain(palette.query_one("#palette-result", Static))
            )
            await pilot.press("escape")
            await runtime.wait_for(lambda: not isinstance(app.screen, PaletteScreen))

        await runtime.start_engine()
        await runtime.wait_for(
            lambda: (
                (outcome := runtime.queue.status(pending.id)) is not None
                and outcome.status is CommandStatus.DONE
            )
        )
        first_outcome = runtime.queue.status(pending.id)
        assert first_outcome is not None
        assert first_outcome.result is not None
        task_id = str(first_outcome.result["task_id"])
        await _wait_for_task(runtime, task_id, Status.DONE)
        await runtime.stop_engine()
        reopen_command(runtime.connection, pending.id)
        await runtime.start_engine()
        await runtime.wait_for(
            lambda: (
                (outcome := runtime.queue.status(pending.id)) is not None
                and outcome.status is CommandStatus.DONE
            )
        )
        queued_events = EventLog(runtime.connection, runtime.clock).since(
            0, kinds=[EventKind.TASK_QUEUED], task_id=task_id
        )
        assert len(queued_events) == 1
        assert len(TaskStore(runtime.connection, runtime.clock).list_all()) == 1
        restarted = runtime.app()
        async with restarted.run_test(size=HOME_SIZE) as pilot:
            status = restarted.query_one("#workspace-command-status", Static)
            await runtime.wait_for(lambda: f"#{pending.id} · Applied" in _plain(status))
            await choose_focus_view(pilot, "Command history · Pending, Applied and Rejected")
            history = restarted.screen.query_one("#command-history-copy", Static)
            await runtime.wait_for(lambda: f"#{pending.id} · Applied" in _plain(history))
    finally:
        await runtime.close()


async def test_real_terminal_cleanup_remains_exportable_with_missing_evidence_marked(
    tmp_path: Path,
    tmux_server: TmuxServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = await _runtime(tmp_path, tmux_server)
    try:
        script_dir = tmp_path / "task-scripts"
        script_dir.mkdir()
        monkeypatch.setenv(TASK_SCRIPT_DIR_ENV_VAR, str(script_dir))
        await runtime.start_engine()
        done_task_id = await _submit_scripted_task(
            runtime,
            script_dir,
            "Export after integration",
            "Finish and export this task.",
            {
                Role.IMPLEMENTER: [{"kind": "accept"}],
                Role.LEADER: [{"kind": "accept"}],
            },
            mode=Mode.IMPLEMENT,
        )
        await _wait_for_task(runtime, done_task_id, Status.DONE)
        cancelled_task_id = await _submit_scripted_task(
            runtime,
            script_dir,
            "Export after cancellation",
            "Cancel this task while the implementer is active.",
            {Role.IMPLEMENTER: [{"kind": "stall"}]},
            mode=Mode.IMPLEMENT,
        )

        async def cancelled_task_has_session() -> bool:
            run = RunStore(runtime.connection, runtime.clock).get_live(
                cancelled_task_id, Role.IMPLEMENTER
            )
            return run is not None and run.session_id is not None

        await runtime.wait_for(cancelled_task_has_session)
        cancelled = await runtime.send(Cancel(task_id=cancelled_task_id))
        assert cancelled.status is CommandStatus.DONE
        await _wait_for_task(runtime, cancelled_task_id, Status.CANCELLED)
        superseded_task_id = await _submit_scripted_task(
            runtime,
            script_dir,
            "Export after superseding",
            "Supersede this task and retain the recorded outcome.",
            {
                Role.LEADER: [
                    {
                        "kind": "supersede",
                        "new_tasks": [{"title": "Replacement task", "request": "Continue."}],
                    }
                ]
            },
            mode=Mode.IMPLEMENT,
        )

        async def superseded_task_is_terminal() -> bool:
            task = TaskStore(runtime.connection, runtime.clock).get(superseded_task_id)
            return (
                task is not None
                and task.status is Status.CANCELLED
                and task.status_reason is StatusReason.SUPERSEDED
            )

        await runtime.wait_for(superseded_task_is_terminal)
        task_ids = (done_task_id, cancelled_task_id, superseded_task_id)
        for task_id in task_ids:
            await _wait_for_terminal_task_cleanup(runtime, task_id)
        evidence_turns = turns_dir(tmp_path, done_task_id)
        report_candidates = list(evidence_turns.rglob("report.json"))
        assert report_candidates
        report_candidates[0].unlink()
        (tmp_path / "exports").mkdir()
        command_count = runtime.queries.command_history(limit=50).total_count
        for task_id, status in zip(
            task_ids, (Status.DONE, Status.CANCELLED, Status.CANCELLED), strict=True
        ):
            destination = f"exports/{task_id}.json"
            document = await _export_terminal_task(runtime, task_id, status, destination)
            assert document["task_id"] == task_id
            assert document["outcome"]["status"] == status.value
            if task_id == done_task_id:
                assert any(
                    item["section"] == "reports" for item in document["unavailable_sections"]
                )
            if task_id == superseded_task_id:
                assert document["outcome"]["reason"] == StatusReason.SUPERSEDED.value
        assert runtime.queries.command_history(limit=50).total_count == command_count
    finally:
        await runtime.close()
