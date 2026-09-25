"""The stdio FastMCP adapter over the operator services (06 §4)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict

from shikiru.agents.contracts import parse_rung
from shikiru.config import settings as config_settings
from shikiru.domain import events as domain_events
from shikiru.domain import tasks as domain_tasks
from shikiru.domain.commands import (
    AcceptResolution,
    AnswerResolution,
    CancelResolution,
    Resolution,
    ResolutionKind,
    RetryResolution,
)
from shikiru.operator import commands as operator_commands
from shikiru.operator import queries as operator_queries
from shikiru.operator.events import MAX_WAIT_SECONDS

if TYPE_CHECKING:
    from shikiru.operator.events import EventFeed

ComplexityName = config_settings.ComplexityName
Event = domain_events.Event
EventKind = domain_events.EventKind
Mode = domain_tasks.Mode
Role = domain_tasks.Role
Status = domain_tasks.Status
Ack = operator_commands.Ack
EscalationView = operator_queries.EscalationView
PeekView = operator_queries.PeekView
StatusView = operator_queries.StatusView
TaskDetail = operator_queries.TaskDetail
TaskSummary = operator_queries.TaskSummary

SERVER_NAME = "shikiru"


class OperatorServiceBundle(Protocol):
    """The injected operator services exposed through the MCP adapter."""

    queries: operator_queries.OperatorQueries
    commands: operator_commands.OperatorCommands
    events: EventFeed


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


def build_server(services: OperatorServiceBundle) -> FastMCP:
    """Build a typed FastMCP server over injected operator services."""
    server = FastMCP(SERVER_NAME)

    _register_query_tools(server, services)
    _register_command_tools(server, services)
    _register_event_tools(server, services)
    return server


def _register_query_tools(server: FastMCP, services: OperatorServiceBundle) -> None:
    @server.tool()
    def status(task_id: str | None = None) -> StatusView:
        return services.queries.status(task_id)

    @server.tool()
    def list_tasks(statuses: list[Status] | None = None) -> list[TaskSummary]:
        return services.queries.list_tasks(statuses=statuses)

    @server.tool()
    def get_task(task_id: str) -> TaskDetail | None:
        return services.queries.get_task(task_id)

    @server.tool()
    def list_escalations() -> list[EscalationView]:
        return services.queries.list_escalations()

    @server.tool()
    def get_escalation(escalation_id: str) -> EscalationView | None:
        return services.queries.get_escalation(escalation_id)

    @server.tool()
    async def peek_session(task_id: str, role: Role | None = None) -> PeekView | None:
        return await services.queries.peek_session(task_id, role)


def _register_command_tools(server: FastMCP, services: OperatorServiceBundle) -> None:
    @server.tool()
    async def submit_task(
        *,
        request: str,
        title: str | None = None,
        mode: Mode | None = None,
        complexity: ComplexityName = "normal",
        priority: int = 0,
        depends_on: list[str] | None = None,
        leader: str | None = None,
        implementer: str | None = None,
    ) -> Ack:
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
        return await services.commands.prioritize_task(task_id, priority)

    @server.tool()
    async def pause_task(task_id: str, *, now: bool = False) -> Ack:
        return await services.commands.pause_task(task_id, now=now)

    @server.tool()
    async def resume_task(task_id: str) -> Ack:
        return await services.commands.resume_task(task_id)

    @server.tool()
    async def cancel_task(task_id: str) -> Ack:
        return await services.commands.cancel_task(task_id)

    @server.tool()
    async def retry_task(task_id: str) -> Ack:
        return await services.commands.retry_task(task_id)

    @server.tool()
    async def set_rung(task_id: str, role: Role, rung: str) -> Ack:
        return await services.commands.set_rung(task_id, role, parse_rung(rung))

    @server.tool()
    async def resolve_escalation(escalation_id: str, resolution: ResolutionInput) -> Ack:
        return await services.commands.resolve_escalation(escalation_id, resolution.to_domain())


def _register_event_tools(server: FastMCP, services: OperatorServiceBundle) -> None:
    @server.tool()
    async def wait_for_events(
        since_id: int,
        kinds: list[EventKind] | None = None,
        task_id: str | None = None,
        timeout_s: float = MAX_WAIT_SECONDS,
    ) -> list[Event]:
        """Wait for matching events, with a maximum timeout of 300 seconds."""
        return await services.events.wait(
            since_id,
            kinds=kinds,
            task=task_id,
            timeout=timeout_s,
        )
