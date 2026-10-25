"""The stdio FastMCP adapter over the operator services."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict

from shikiru.agents.contracts import parse_rung
from shikiru.config.settings import ComplexityName
from shikiru.domain.commands import (
    AcceptResolution,
    AnswerResolution,
    CancelResolution,
    Resolution,
    ResolutionKind,
    RetryResolution,
)
from shikiru.domain.events import Event, EventKind
from shikiru.domain.tasks import Mode, Role, Status
from shikiru.operator.commands import Ack
from shikiru.operator.events import MAX_WAIT_SECONDS
from shikiru.operator.queries import (
    EscalationView,
    PeekView,
    StatusView,
    TaskDetail,
    TaskSummary,
)

if TYPE_CHECKING:
    from shikiru.operator.services import OperatorServices

SERVER_NAME = "shikiru"


class ResolutionInput(BaseModel):
    """Typed MCP input for an escalation resolution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: ResolutionKind
    text: str | None = None
    rung: str | None = None

    def to_domain(self) -> Resolution:
        """Convert this transport model to a typed domain resolution."""
        if self.kind is ResolutionKind.ANSWER:
            if self.text is None:
                message = "answer resolution requires text"
                raise ValueError(message)
            return AnswerResolution(text=self.text)
        if self.kind is ResolutionKind.RETRY:
            return RetryResolution(rung=parse_rung(self.rung) if self.rung is not None else None)
        if self.kind is ResolutionKind.ACCEPT:
            return AcceptResolution()
        return CancelResolution()


def build_server(services: OperatorServices) -> FastMCP:
    """Build a typed FastMCP server over injected operator services."""
    server = FastMCP(SERVER_NAME)

    _register_query_tools(server, services)
    _register_command_tools(server, services)
    _register_session_tools(server, services)
    _register_event_tools(server, services)
    return server


def _register_query_tools(server: FastMCP, services: OperatorServices) -> None:
    @server.tool()
    def status(task_id: str | None = None) -> StatusView:
        """Read engine, queue, usage and escalation status, optionally with one task's details."""
        return services.queries.status(task_id)

    @server.tool()
    def list_tasks(statuses: list[Status] | None = None) -> list[TaskSummary]:
        """List task summaries, optionally filtered by task status."""
        return services.queries.list_tasks(statuses=statuses)

    @server.tool()
    def get_task(task_id: str) -> TaskDetail | None:
        """Read one task's details, history, reports and sessions, or return null if absent."""
        return services.queries.get_task(task_id)

    @server.tool()
    def list_escalations() -> list[EscalationView]:
        """List all currently open escalations with their packets."""
        return services.queries.list_escalations()

    @server.tool()
    def get_escalation(escalation_id: str) -> EscalationView | None:
        """Read one open escalation and its packet, or return null if absent."""
        return services.queries.get_escalation(escalation_id)

    @server.tool()
    def peek_session(task_id: str, role: Role | None = None) -> PeekView | None:
        """Read one task role's, or every role's, last message, usage and recent view lines."""
        return services.queries.peek_session(task_id, role)


def _register_command_tools(server: FastMCP, services: OperatorServices) -> None:
    @server.tool()
    async def submit_task(
        *,
        request: str,
        title: str | None = None,
        mode: Mode | None = None,
        complexity: ComplexityName | None = None,
        priority: int = 0,
        depends_on: list[str] | None = None,
        leader: str | None = None,
        implementer: str | None = None,
    ) -> Ack:
        """Queue a task with optional title, mode, complexity, priority, dependencies and rungs."""
        return await services.commands.submit_task(
            request,
            title=title,
            mode=mode,
            complexity=complexity,
            priority=priority,
            depends_on=depends_on or (),
            leader=parse_rung(leader) if leader is not None else None,
            implementer=parse_rung(implementer) if implementer is not None else None,
        )

    @server.tool()
    async def prioritize_task(task_id: str, priority: int) -> Ack:
        """Set a queued task's dispatch priority."""
        return await services.commands.prioritize_task(task_id, priority)

    @server.tool()
    async def pause_task(task_id: str, *, now: bool = False) -> Ack:
        """Pause a task, optionally requesting an immediate pause."""
        return await services.commands.pause_task(task_id, now=now)

    @server.tool()
    async def resume_task(task_id: str) -> Ack:
        """Resume a paused task."""
        return await services.commands.resume_task(task_id)

    @server.tool()
    async def cancel_task(task_id: str) -> Ack:
        """Cancel a task."""
        return await services.commands.cancel_task(task_id)

    @server.tool()
    async def retry_task(task_id: str) -> Ack:
        """Retry a failed or escalated task from its current step."""
        return await services.commands.retry_task(task_id)

    @server.tool()
    async def set_rung(task_id: str, role: Role, rung: str) -> Ack:
        """Set the task role's agent, model and optional effort rung."""
        return await services.commands.set_rung(task_id, role, parse_rung(rung))

    @server.tool()
    async def resolve_escalation(escalation_id: str, resolution: ResolutionInput) -> Ack:
        """Resolve an open escalation with an answer, retry, accept or cancel decision."""
        return await services.commands.resolve_escalation(escalation_id, resolution.to_domain())


def _register_session_tools(server: FastMCP, services: OperatorServices) -> None:
    @server.tool()
    async def send_message(task_id: str, role: Role, text: str) -> Ack:
        """Queue a message the role reads at the start of its next turn."""
        return await services.commands.send_message(task_id, role, text)

    @server.tool()
    async def take_over(task_id: str, role: Role) -> Ack:
        """Stop the role's turn and run its session interactively in its pane."""
        return await services.commands.take_over(task_id, role)

    @server.tool()
    async def hand_back(task_id: str, role: Role) -> Ack:
        """Return a taken-over role's session to the engine and resume the task."""
        return await services.commands.hand_back(task_id, role)


def _register_event_tools(server: FastMCP, services: OperatorServices) -> None:
    @server.tool()
    async def wait_for_events(
        since_id: int,
        kinds: list[EventKind] | None = None,
        task_id: str | None = None,
        timeout_s: float = MAX_WAIT_SECONDS,
    ) -> list[Event]:
        """Wait up to 300 seconds for events after since_id with optional kind and task filters."""
        return await services.events.wait(
            since_id,
            kinds=kinds,
            task=task_id,
            timeout=timeout_s,
        )
