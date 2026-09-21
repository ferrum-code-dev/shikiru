"""Operator services shared by the CLI, MCP server and dashboard palette."""

from shikiru.operator.commands import Ack, AckStatus, OperatorCommands
from shikiru.operator.doctor import Doctor, DoctorCheck, DoctorReport
from shikiru.operator.events import EventFeed
from shikiru.operator.queries import (
    EscalationView,
    OperatorQueries,
    OperatorQueryError,
    PeekView,
    StatusView,
    TaskDetail,
    TaskSummary,
)

__all__ = [
    "Ack",
    "AckStatus",
    "Doctor",
    "DoctorCheck",
    "DoctorReport",
    "EscalationView",
    "EventFeed",
    "OperatorCommands",
    "OperatorQueries",
    "OperatorQueryError",
    "PeekView",
    "StatusView",
    "TaskDetail",
    "TaskSummary",
]
