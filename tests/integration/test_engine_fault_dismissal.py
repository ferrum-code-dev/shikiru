"""Fault dismissal is a durable operator command and closes only its selected occurrence."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest

from shikiru.config.settings import FaultsConfig
from shikiru.domain.commands import CommandStatus, DismissFault
from shikiru.domain.events import EventKind
from shikiru.domain.faults import FAULT_EVENT_ID_KEY, FAULT_EVENT_KINDS, open_fault_streaks
from shikiru.engine.fault_reporter import FaultReporter
from shikiru.engine.handlers import DismissFaultHandler, HandlerRegistry
from shikiru.errors import CommandFailed, CommandRejectedError
from shikiru.operator.commands import AckStatus, OperatorCommands
from shikiru.store.commands import CommandQueue
from shikiru.store.events import EventLog
from tests.support.clock import FakeClock
from tests.support.factories import TASK_CREATED_AT

if TYPE_CHECKING:
    from shikiru.store.database import Database
    from tests.support.store import Connection

_FAULTS = FaultsConfig(
    backoff_initial_seconds=1,
    backoff_max_seconds=4,
    unexpected_fault_limit=3,
    stop_notify_seconds=1,
    repeat_notify_seconds=30,
)
_LOGGER = logging.getLogger("fault-dismissal-test")
_SCOPE = "layout restore"
_TASK_ID = "T-1"


@pytest.mark.asyncio
async def test_dismissal_persists_and_a_new_occurrence_reopens_after_the_repeat_window_is_cleared(
    database: Database, connection: Connection
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    events = EventLog(connection, clock)
    reporter = FaultReporter(events=events, clock=clock, settings=_FAULTS, logger=_LOGGER)
    failure = CommandFailed(("tmux",), 1, "session disappeared")
    reporter.report(failure, scope=_SCOPE, task_id=_TASK_ID)
    (original,) = events.since(0, kinds=[EventKind.ENGINE_FAULT])

    queue = CommandQueue(connection, clock)
    commands = OperatorCommands(queue, clock, engine_up=lambda: False)
    ack = await commands.dismiss_fault(_SCOPE, _TASK_ID, original.id)
    assert ack.status is AckStatus.QUEUED
    (queued,) = queue.pending()
    assert isinstance(queued.command, DismissFault)

    handlers = HandlerRegistry([DismissFaultHandler(reporter)])
    result = await handlers.handle(queued.command)
    queue.mark_done(queued.id, result)

    (dismissed,) = events.since(0, kinds=[EventKind.ENGINE_FAULT_DISMISSED])
    assert dismissed.data[FAULT_EVENT_ID_KEY] == original.id
    outcome = queue.status(queued.id)
    assert outcome is not None
    assert outcome.status is CommandStatus.DONE
    assert events.since(0, kinds=[EventKind.ENGINE_RECOVERED]) == []

    restarted_connection = database.connect()
    try:
        restarted_events = EventLog(restarted_connection, clock)
        assert open_fault_streaks(restarted_events.since(0, kinds=FAULT_EVENT_KINDS)) == []
    finally:
        restarted_connection.close()

    reporter.report(failure, scope=_SCOPE, task_id=_TASK_ID)
    faults = open_fault_streaks(events.since(0, kinds=FAULT_EVENT_KINDS))
    (fault,) = faults
    (latest,) = events.since(0, kinds=[EventKind.ENGINE_FAULT])[-1:]
    assert (fault.event_id, fault.repeat) == (latest.id, 1)


def test_a_dismissal_from_an_old_card_cannot_hide_a_newer_fault(
    connection: Connection,
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    events = EventLog(connection, clock)
    reporter = FaultReporter(events=events, clock=clock, settings=_FAULTS, logger=_LOGGER)
    recorded = {
        "scope": _SCOPE,
        "error": "CommandFailed",
        "recoverable": True,
        "repeat": 1,
    }
    old = events.append(EventKind.ENGINE_FAULT, message="old", task_id=_TASK_ID, data=recorded)
    latest = events.append(
        EventKind.ENGINE_FAULT, message="latest", task_id=_TASK_ID, data=recorded
    )

    with pytest.raises(CommandRejectedError, match="no longer open"):
        reporter.dismiss(
            _SCOPE,
            task_id=_TASK_ID,
            fault_event_id=old.id,
            command_id=1,
        )

    (fault,) = open_fault_streaks(events.since(0, kinds=FAULT_EVENT_KINDS))
    assert fault.event_id == latest.id
