"""Agent session runs: one per task role's session lineage (docs/design/05-data.md §2)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import TypeAdapter

from shikiru.agents.contracts import Usage
from shikiru.domain.runs import Run, RunStatus
from shikiru.domain.tasks import Role
from shikiru.store.database import (
    dump_json,
    format_timestamp,
    load_json,
    parse_required_timestamp,
    parse_timestamp,
    require_after_insert,
)

if TYPE_CHECKING:
    import sqlite3

    from shikiru.agents.contracts import Rung
    from shikiru.clock import Clock

_DEFAULT_COST_BASIS = "unknown"

_AGENT_STATE_ADAPTER: TypeAdapter[dict[str, Any]] = TypeAdapter(dict[str, Any])

_RUN_COLUMNS = (
    "id, task_id, role, agent, model, effort, session_id, resume_command, agent_state, "
    "last_message, status, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, "
    "cost_usd, cost_basis, context_used_pct, started_at, ended_at"
)


def _row_to_run(row: sqlite3.Row) -> Run:
    return Run(
        id=row["id"],
        task_id=row["task_id"],
        role=Role(row["role"]),
        agent=row["agent"],
        model=row["model"],
        effort=row["effort"],
        session_id=row["session_id"],
        resume_command=row["resume_command"],
        agent_state=load_json(_AGENT_STATE_ADAPTER, row["agent_state"]),
        last_message=row["last_message"],
        status=RunStatus(row["status"]),
        usage=Usage(
            input_tokens=row["input_tokens"],
            output_tokens=row["output_tokens"],
            cache_read_tokens=row["cache_read_tokens"],
            cache_write_tokens=row["cache_write_tokens"],
            cost_usd=row["cost_usd"],
            cost_basis=row["cost_basis"] if row["cost_basis"] is not None else _DEFAULT_COST_BASIS,
            context_used_pct=row["context_used_pct"],
        ),
        started_at=parse_required_timestamp(row["started_at"]),
        ended_at=parse_timestamp(row["ended_at"]),
    )


def fetch_run(connection: sqlite3.Connection, run_id: int) -> Run | None:
    """Read one run by id, or `None` if it doesn't exist."""
    row = connection.execute(f"SELECT {_RUN_COLUMNS} FROM runs WHERE id = ?", (run_id,)).fetchone()
    return _row_to_run(row) if row is not None else None


def fetch_runs_for_task(connection: sqlite3.Connection, task_id: str) -> list[Run]:
    """Read every run recorded for `task_id`, most recently started first."""
    rows = connection.execute(
        f"SELECT {_RUN_COLUMNS} FROM runs WHERE task_id = ? ORDER BY id DESC", (task_id,)
    ).fetchall()
    return [_row_to_run(row) for row in rows]


def fetch_live_runs(connection: sqlite3.Connection) -> list[Run]:
    """Read every live agent run, most recently started first."""
    rows = connection.execute(
        f"SELECT {_RUN_COLUMNS} FROM runs WHERE status = ? ORDER BY id DESC",
        (RunStatus.LIVE.value,),
    ).fetchall()
    return [_row_to_run(row) for row in rows]


class RunStore:
    """Starts, updates and reads agent session runs (03 §4.1-4.2)."""

    def __init__(self, connection: sqlite3.Connection, clock: Clock) -> None:
        """Use `connection` (the engine's writer) and `clock` for every write below."""
        self._connection = connection
        self._clock = clock

    def start(self, task_id: str, role: Role, rung: Rung) -> Run:
        """Start a new live run for `task_id`'s `role` on `rung`."""
        cursor = self._connection.execute(
            """
            INSERT INTO runs (task_id, role, agent, model, effort, agent_state, status, started_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                role.value,
                rung.agent,
                rung.model,
                rung.effort,
                dump_json(_AGENT_STATE_ADAPTER, {}),
                RunStatus.LIVE.value,
                format_timestamp(self._clock.now()),
            ),
        )
        run_id = require_after_insert(
            cursor.lastrowid, "sqlite did not allocate a rowid for the new run"
        )
        run = fetch_run(self._connection, run_id)
        return require_after_insert(run, f"run {run_id} vanished immediately after insert")

    def get_live(self, task_id: str, role: Role) -> Run | None:
        """Return `task_id`'s live run for `role`, if any."""
        row = self._connection.execute(
            f"SELECT {_RUN_COLUMNS} FROM runs WHERE task_id = ? AND role = ? AND status = ?",
            (task_id, role.value, RunStatus.LIVE.value),
        ).fetchone()
        return _row_to_run(row) if row is not None else None

    def for_task(self, task_id: str) -> list[Run]:
        """Return every run recorded for `task_id`."""
        return fetch_runs_for_task(self._connection, task_id)

    def list_live(self) -> list[Run]:
        """Return every live run across all tasks."""
        return fetch_live_runs(self._connection)

    def set_session(self, run_id: int, session_id: str | None, resume_command: str | None) -> None:
        """Record `run_id`'s launched session id and resume command.

        `session_id` is `None` when `SessionStarted` says the session can't be resumed (04 §1),
        and `resume_command` is `None` when the agent's `resume_command()` returns none.
        """
        self._connection.execute(
            "UPDATE runs SET session_id = ?, resume_command = ? WHERE id = ?",
            (session_id, resume_command, run_id),
        )

    def set_agent_state(self, run_id: int, agent_state: dict[str, Any]) -> None:
        """Persist `run_id`'s plugin-private `agent_state`."""
        self._connection.execute(
            "UPDATE runs SET agent_state = ? WHERE id = ?",
            (dump_json(_AGENT_STATE_ADAPTER, agent_state), run_id),
        )

    def set_last_message(self, run_id: int, message: str) -> None:
        """Persist `run_id`'s latest known message from the agent."""
        self._connection.execute("UPDATE runs SET last_message = ? WHERE id = ?", (message, run_id))

    def record_usage(self, run_id: int, usage: Usage) -> None:
        """Overwrite `run_id`'s usage with `usage`'s latest cumulative snapshot."""
        self._connection.execute(
            """
            UPDATE runs SET input_tokens = ?, output_tokens = ?, cache_read_tokens = ?,
              cache_write_tokens = ?, cost_usd = ?, cost_basis = ?, context_used_pct = ?
            WHERE id = ?
            """,
            (
                usage.input_tokens,
                usage.output_tokens,
                usage.cache_read_tokens,
                usage.cache_write_tokens,
                usage.cost_usd,
                usage.cost_basis,
                usage.context_used_pct,
                run_id,
            ),
        )

    def exit(self, run_id: int) -> None:
        """Mark `run_id` exited: the agent process is gone but the session is resumable."""
        self._set_status(run_id, RunStatus.EXITED)

    def close(self, run_id: int) -> None:
        """Mark `run_id` closed: the task ended, the session is not resumable."""
        self._set_status(run_id, RunStatus.CLOSED)

    def _set_status(self, run_id: int, status: RunStatus) -> None:
        self._connection.execute(
            "UPDATE runs SET status = ?, ended_at = ? WHERE id = ?",
            (status.value, format_timestamp(self._clock.now()), run_id),
        )
