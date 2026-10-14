"""Shared escalation seeding, operator services and engine-side resolution checks."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import App
from textual.widgets import Label, Static

from shikiru.dashboard.resolution import ResolutionDrafts, ResolutionScreen
from shikiru.domain.commands import (
    AcceptResolution,
    AnswerResolution,
    Cancel,
    CancelResolution,
    CommandStatus,
    Resolve,
    RetryResolution,
)
from shikiru.domain.escalation import EscalationStatus, options_for
from shikiru.domain.reports import EscalationRequest
from shikiru.domain.reports import Option as AgentOption
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import EscalationReason, Role, Status, Step
from shikiru.engine.escalations import PacketContext, render_packet
from shikiru.engine.handlers.base import HandlerContext
from shikiru.engine.handlers.escalations import ResolveHandler
from shikiru.engine.handlers.tasks import CancelHandler
from shikiru.operator.commands import OperatorCommands
from shikiru.operator.queries import OperatorQueries
from shikiru.store.commands import CommandOutcome, CommandQueue
from shikiru.store.database import transaction
from shikiru.store.escalations import EscalationStore
from shikiru.store.events import EventLog
from shikiru.store.runs import RunStore
from shikiru.store.tasks import TaskStore
from shikiru.ui.theme import install_theme
from tests.support.waiting import UI_WAIT_SECONDS, wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from textual.app import ComposeResult
    from textual.screen import ModalScreen

    from shikiru.clock import Clock
    from shikiru.dashboard.contract import ResolutionScreenFactory
    from shikiru.domain.escalation import Escalation
    from shikiru.store.commands import QueuedCommand
    from shikiru.store.reader import StateReader
    from tests.support.store import Connection


MAX_PARALLEL = 2


def operator_services(
    connection: Connection,
    reader: StateReader,
    clock: Clock,
    project_dir: Path,
    *,
    engine_up: bool = False,
) -> tuple[OperatorCommands, OperatorQueries, CommandQueue]:
    queue = CommandQueue(connection, clock)
    for command in queue.pending():
        queue.mark_done(command.id)
    queries = OperatorQueries(
        reader,
        project="palette-test",
        project_root=project_dir,
        engine_up=lambda: engine_up,
        max_parallel=MAX_PARALLEL,
        clock=clock,
    )
    commands = OperatorCommands(
        queue,
        clock,
        engine_up=lambda: engine_up,
    )
    return commands, queries, queue


def seed_escalation(
    connection: Connection,
    clock: Clock,
    task_id: str,
    project_dir: Path,
    reason: EscalationReason,
    *,
    write_packet: bool = True,
    needs_input: bool = False,
    attempted: str = "One implementation and review round.",
) -> tuple[Escalation, str]:
    task = TaskStore(connection, clock).require(task_id)
    TaskStore(connection, clock).apply_transition(
        task_id,
        Transition.escalate(task, reason, step=Step.CONSULT),
    )
    agent_request = EscalationRequest(
        question="Which behavior should remain?",
        options=[
            AgentOption(
                label="Keep existing behavior",
                consequences="Preserve the current call pattern.",
                recommended=True,
            ),
            AgentOption(
                label="Change behavior",
                consequences="Update callers and documentation.",
                needs_input=needs_input,
            ),
        ],
    )
    packet = render_packet(
        PacketContext(
            task_id=task_id,
            step=Step.CONSULT,
            reason=reason,
            timeline=["turn completed: implementation"],
            attempted=attempted,
            reports="Implementation report evidence.",
            facts="Recorded git facts.",
            view_tail="Recent stream tail.",
            sessions="Leader session evidence.",
            question=agent_request.question,
            options=options_for(reason),
            agent_request=agent_request,
        )
    )
    packet_path = project_dir / f"{task_id}-{reason.value}.md"
    if write_packet:
        packet_path.write_text(packet)
    escalation = EscalationStore(connection, clock).open(
        task_id,
        step=Step.CONSULT,
        reason=reason,
        headline="A product decision is needed",
        packet_path=str(packet_path),
        options=options_for(reason),
    )
    return escalation, packet


def handler_context(connection: Connection, clock: Clock, project_dir: Path) -> HandlerContext:
    async def no_task_action(_task_id: str) -> None:
        return None

    async def no_title_refresh(_task: object) -> None:
        return None

    async def no_finish(_task: object) -> Path | None:
        return None

    return HandlerContext(
        tasks=TaskStore(connection, clock),
        escalations=EscalationStore(connection, clock),
        events=EventLog(connection, clock),
        runs=RunStore(connection, clock),
        project_root=project_dir,
        cancel_task=no_task_action,
        start_task=no_task_action,
        finish_task=no_finish,
        stop_engine=lambda: None,
        refresh_titles=no_title_refresh,
        atomically=lambda: transaction(connection),
    )


async def enqueue_and_cancel(
    connection: Connection,
    clock: Clock,
    task_id: str,
    project_dir: Path,
) -> CommandOutcome:
    queue = CommandQueue(connection, clock)
    inserted = queue.append(Cancel(task_id=task_id))
    queued = next(item for item in queue.pending() if item.id == inserted.id)
    assert isinstance(queued.command, Cancel)
    result = await CancelHandler(handler_context(connection, clock, project_dir)).handle(
        queued.command
    )
    queue.mark_done(inserted.id, result)
    outcome = queue.status(inserted.id)
    assert outcome is not None
    return outcome


async def assert_engine_applies_resolution(
    connection: Connection,
    clock: Clock,
    project_dir: Path,
    queue: CommandQueue,
    *,
    queued: QueuedCommand,
    task_id: str,
    escalation_id: str,
    expected_type: type[object],
) -> None:
    command = queued.command
    assert isinstance(command, Resolve)
    assert isinstance(command.resolution, expected_type)
    result = await ResolveHandler(handler_context(connection, clock, project_dir)).handle(command)
    queue.mark_done(queued.id, result)
    resolved_task = TaskStore(connection, clock).require(task_id)
    resolved_escalation = EscalationStore(connection, clock).get(escalation_id)
    stored = queue.status(queued.id)
    assert resolved_escalation is not None
    assert resolved_escalation.status is EscalationStatus.RESOLVED
    assert stored is not None
    assert stored.status is CommandStatus.DONE
    assert stored.result == result
    assert result["task_id"] == task_id
    assert result["status"] == resolved_task.status.value
    if isinstance(command.resolution, AnswerResolution):
        assert resolved_task.status is Status.QUEUED
        assert resolved_task.step is Step.CONSULT
    elif isinstance(command.resolution, RetryResolution):
        assert resolved_task.status is Status.QUEUED
        if command.resolution.rung is not None:
            assert resolved_task.overrides[Role.LEADER] == command.resolution.rung
    elif isinstance(command.resolution, AcceptResolution):
        assert resolved_task.status is Status.QUEUED
        assert resolved_task.step is Step.INTEGRATE
    else:
        assert isinstance(command.resolution, CancelResolution)
        assert resolved_task.status is Status.CANCELLED


def resolution_factory(
    commands: OperatorCommands,
    queries: OperatorQueries,
    clock: Clock,
    reader: StateReader,
    *,
    drafts: ResolutionDrafts | None = None,
) -> ResolutionScreenFactory:
    """Build resolution screens over the given services, sharing one set of answer drafts."""
    drafts = drafts or ResolutionDrafts()

    def build(escalation_id: str) -> ModalScreen[bool]:
        return ResolutionScreen(
            commands,
            queries,
            clock=clock,
            on_change=reader.watch,
            drafts=drafts,
            escalation_id=escalation_id,
        )

    return build


class ResolutionHost(App[None]):
    """Open one resolution screen over a neutral base and record how it closed."""

    def __init__(self, factory: ResolutionScreenFactory, escalation_id: str) -> None:
        super().__init__()
        install_theme(self)
        self._factory = factory
        self._escalation_id = escalation_id
        self.results: list[bool | None] = []

    def compose(self) -> ComposeResult:
        yield Static()

    def on_mount(self) -> None:
        self.push_screen(self._factory(self._escalation_id), callback=self.results.append)


async def wait_for_resolution(app: App[None]) -> ResolutionScreen:
    """Wait until the app shows a fully composed resolution screen and return it."""
    await wait_until(
        lambda: (
            isinstance(app.screen, ResolutionScreen) and bool(app.screen.query("#resolution-title"))
        ),
        timeout=UI_WAIT_SECONDS,
    )
    assert isinstance(app.screen, ResolutionScreen)
    return app.screen


def resolution_title(screen: ResolutionScreen) -> str:
    """Return the popup title text of `screen`."""
    return str(screen.query_one("#resolution-title", Label).content)
