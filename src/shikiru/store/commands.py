"""Operator command queue: append, dispatch and acknowledge (docs/design/05-data.md §2)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import TypeAdapter

from shikiru.domain.commands import (
    COMMAND_ID_KEY,
    Command,
    CommandStatus,
    IdentifiedCommand,
    Submit,
)
from shikiru.store.database import (
    dump_json,
    format_timestamp,
    load_json,
    next_sequence,
    require_after_insert,
    transaction,
)
from shikiru.store.tasks import TASK_ID_PREFIX, TASK_SEQUENCE

if TYPE_CHECKING:
    import sqlite3  # noqa: TID251

    from shikiru.clock import Clock

_COMMAND_ADAPTER: TypeAdapter[Command] = TypeAdapter(Command)
_RESULT_ADAPTER: TypeAdapter[dict[str, Any] | None] = TypeAdapter(dict[str, Any] | None)


def _identified(command: Command, command_id: int) -> Command:
    if isinstance(command, IdentifiedCommand):
        return command.model_copy(update={COMMAND_ID_KEY: command_id})
    return command


@dataclass(frozen=True, slots=True)
class QueuedCommand:
    """One pending command, as `CommandQueue.pending()` hands it to the engine."""

    id: int
    command: Command


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    """A command's lifecycle status, for `OperatorCommands` to wait on its ack."""

    id: int
    status: CommandStatus
    result: dict[str, Any] | None


class CommandQueue:
    """Appends operator commands and lets the engine dispatch and acknowledge them."""

    def __init__(self, connection: sqlite3.Connection, clock: Clock) -> None:
        """Use `connection` and `clock` for every write below.

        Unlike the other stores, this connection isn't only the engine's: clients (CLI, MCP)
        append commands and, for `Submit`, allocate the task id here too (principle 3, 01 §3).
        """
        self._connection = connection
        self._clock = clock

    def append(self, command: Command) -> QueuedCommand:
        """Append `command`; a `Submit` is given its `T-n` id here, atomically (principle 3)."""
        ts = format_timestamp(self._clock.now())
        with transaction(self._connection):
            if isinstance(command, Submit):
                seq = next_sequence(self._connection, TASK_SEQUENCE)
                command = command.model_copy(update={"task_id": f"{TASK_ID_PREFIX}{seq}"})
            cursor = self._connection.execute(
                "INSERT INTO commands (ts, kind, payload) VALUES (?, ?, ?)",
                (ts, command.kind.value, dump_json(_COMMAND_ADAPTER, command)),
            )
            command_id = cursor.lastrowid
        command_id = require_after_insert(
            command_id, "sqlite did not allocate a rowid for the new command"
        )
        return QueuedCommand(id=command_id, command=command)

    def pending(self) -> list[QueuedCommand]:
        """Read every pending command, oldest first, as typed `domain.commands.Command` models."""
        rows = self._connection.execute(
            "SELECT id, payload FROM commands WHERE status = ? ORDER BY id",
            (CommandStatus.PENDING.value,),
        ).fetchall()
        return [
            QueuedCommand(
                id=row["id"],
                command=_identified(load_json(_COMMAND_ADAPTER, row["payload"]), row["id"]),
            )
            for row in rows
        ]

    def mark_done(self, command_id: int, result: dict[str, Any] | None = None) -> None:
        """Mark `command_id` done, recording `result` if the client needs it."""
        self._set_status(command_id, CommandStatus.DONE, result)

    def mark_rejected(self, command_id: int, result: dict[str, Any] | None = None) -> None:
        """Mark `command_id` rejected, recording `result` (e.g. an error message)."""
        self._set_status(command_id, CommandStatus.REJECTED, result)

    def _set_status(
        self, command_id: int, status: CommandStatus, result: dict[str, Any] | None
    ) -> None:
        self._connection.execute(
            "UPDATE commands SET status = ?, result = ? WHERE id = ?",
            (
                status.value,
                dump_json(_RESULT_ADAPTER, result),
                command_id,
            ),
        )

    def status(self, command_id: int) -> CommandOutcome | None:
        """Read `command_id`'s current lifecycle status, or `None` if it doesn't exist."""
        row = self._connection.execute(
            "SELECT id, status, result FROM commands WHERE id = ?", (command_id,)
        ).fetchone()
        if row is None:
            return None
        result = row["result"]
        return CommandOutcome(
            id=row["id"],
            status=CommandStatus(row["status"]),
            result=load_json(_RESULT_ADAPTER, result) if result is not None else None,
        )
