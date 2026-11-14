"""Agent session runs: one per task role's session lineage."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.agents.contracts import (
    UNKNOWN_BILLING_DECLARATION,
    BillingDeclaration,
    Usage,
)
from shikiru.domain.runs import LegacyUsage, LegacyUsageSource, Run, RunStatus
from shikiru.domain.tasks import Role
from shikiru.domain.token_usage import UsageAccounting
from shikiru.store.database import (
    format_timestamp,
    parse_required_timestamp,
    parse_timestamp,
    require_after_insert,
)

if TYPE_CHECKING:
    import sqlite3  # noqa: TID251

    from shikiru.agents.contracts import Rung
    from shikiru.clock import Clock

_RUN_COLUMNS = (
    "runs.id, runs.task_id, runs.role, runs.agent, runs.model, runs.effort, "
    "runs.session_id, runs.resume_command, runs.last_message, runs.status, "
    "runs.uncached_input_tokens, runs.output_tokens, runs.cache_read_tokens, "
    "runs.cache_write_tokens, runs.cost_usd, runs.cost_basis, runs.context_used_pct, "
    "runs.usage_reported, runs.usage_accounting, runs.billing_mode, runs.billing_source, "
    "runs.started_at, runs.ended_at, "
    "legacy_run_usage.source AS legacy_usage_source, "
    "legacy_run_usage.usage_reported AS legacy_usage_reported, "
    "legacy_run_usage.input_tokens AS legacy_input_tokens, "
    "legacy_run_usage.uncached_input_tokens AS legacy_uncached_input_tokens, "
    "legacy_run_usage.output_tokens AS legacy_output_tokens, "
    "legacy_run_usage.cache_read_tokens AS legacy_cache_read_tokens, "
    "legacy_run_usage.cache_write_tokens AS legacy_cache_write_tokens"
)
_RUN_FROM = "FROM runs LEFT JOIN legacy_run_usage ON legacy_run_usage.run_id = runs.id"
MAX_TAKEN_OVER_ROLE_COUNT = len(Role)


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
        last_message=row["last_message"],
        status=RunStatus(row["status"]),
        usage=Usage(
            uncached_input_tokens=row["uncached_input_tokens"],
            output_tokens=row["output_tokens"],
            cache_read_tokens=row["cache_read_tokens"],
            cache_write_tokens=row["cache_write_tokens"],
            cost_usd=row["cost_usd"],
            cost_basis=row["cost_basis"],
            context_used_pct=row["context_used_pct"],
        ),
        legacy_usage=(
            LegacyUsage(
                source=LegacyUsageSource(row["legacy_usage_source"]),
                usage_reported=(
                    bool(row["legacy_usage_reported"])
                    if row["legacy_usage_reported"] is not None
                    else None
                ),
                input_tokens=row["legacy_input_tokens"],
                uncached_input_tokens=row["legacy_uncached_input_tokens"],
                output_tokens=row["legacy_output_tokens"],
                cache_read_tokens=row["legacy_cache_read_tokens"],
                cache_write_tokens=row["legacy_cache_write_tokens"],
            )
            if row["legacy_usage_source"] is not None
            else None
        ),
        usage_reported=(bool(row["usage_reported"]) if row["usage_reported"] is not None else None),
        usage_accounting=UsageAccounting(row["usage_accounting"]),
        billing_mode=row["billing_mode"],
        billing_source=row["billing_source"],
        started_at=parse_required_timestamp(row["started_at"]),
        ended_at=parse_timestamp(row["ended_at"]),
    )


def fetch_run(connection: sqlite3.Connection, run_id: int) -> Run | None:
    """Read one run by id, or `None` if it doesn't exist."""
    row = connection.execute(
        f"SELECT {_RUN_COLUMNS} {_RUN_FROM} WHERE runs.id = ?", (run_id,)
    ).fetchone()
    return _row_to_run(row) if row is not None else None


def fetch_runs_for_task(connection: sqlite3.Connection, task_id: str) -> list[Run]:
    """Read every run recorded for `task_id`, most recently started first."""
    rows = connection.execute(
        f"SELECT {_RUN_COLUMNS} {_RUN_FROM} WHERE runs.task_id = ? ORDER BY runs.id DESC",
        (task_id,),
    ).fetchall()
    return [_row_to_run(row) for row in rows]


def fetch_taken_over_roles_for_task(
    connection: sqlite3.Connection,
    task_id: str,
) -> tuple[Role, ...]:
    rows = connection.execute(
        "SELECT role FROM runs WHERE task_id = ? AND status = ? ORDER BY id DESC LIMIT ?",
        (task_id, RunStatus.TAKEN_OVER.value, MAX_TAKEN_OVER_ROLE_COUNT),
    ).fetchall()
    return tuple(Role(row["role"]) for row in rows)


def fetch_all_runs(connection: sqlite3.Connection) -> list[Run]:
    """Read every run, most recently started first."""
    rows = connection.execute(f"SELECT {_RUN_COLUMNS} {_RUN_FROM} ORDER BY runs.id DESC").fetchall()
    return [_row_to_run(row) for row in rows]


class RunStore:
    """Starts, updates and reads agent session runs."""

    def __init__(self, connection: sqlite3.Connection, clock: Clock) -> None:
        """Use `connection` (the engine's writer) and `clock` for every write below."""
        self._connection = connection
        self._clock = clock

    def start(
        self,
        task_id: str,
        role: Role,
        rung: Rung,
        *,
        billing_declaration: BillingDeclaration = UNKNOWN_BILLING_DECLARATION,
    ) -> Run:
        """Start a new live run for `task_id`'s `role` on `rung`."""
        cursor = self._connection.execute(
            """
            INSERT INTO runs (
              task_id, role, agent, model, effort, status, usage_reported,
              billing_mode, billing_source, started_at
            ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
            """,
            (
                task_id,
                role.value,
                rung.agent,
                rung.model,
                rung.effort,
                RunStatus.LIVE.value,
                billing_declaration.mode,
                billing_declaration.source,
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
        return self._get_with_status(task_id, role, RunStatus.LIVE)

    def get_taken_over(self, task_id: str, role: Role) -> Run | None:
        """Return `task_id`'s run for `role` that the Operator has taken over, if any."""
        return self._get_with_status(task_id, role, RunStatus.TAKEN_OVER)

    def mark_taken_over(self, run_id: int) -> None:
        """Mark `run_id` taken over: its session runs interactively in the Operator's pane."""
        self._set_status_keeping_open(run_id, RunStatus.TAKEN_OVER)

    def mark_live(self, run_id: int) -> None:
        """Mark a taken-over `run_id` live again: the engine drives its session."""
        self._set_status_keeping_open(run_id, RunStatus.LIVE)

    def has_taken_over(self, task_id: str) -> bool:
        """Report whether the Operator has taken over any role of `task_id`."""
        row = self._connection.execute(
            "SELECT 1 FROM runs WHERE task_id = ? AND status = ? LIMIT 1",
            (task_id, RunStatus.TAKEN_OVER.value),
        ).fetchone()
        return row is not None

    def _get_with_status(self, task_id: str, role: Role, status: RunStatus) -> Run | None:
        row = self._connection.execute(
            f"SELECT {_RUN_COLUMNS} {_RUN_FROM} "
            "WHERE runs.task_id = ? AND runs.role = ? AND runs.status = ?",
            (task_id, role.value, status.value),
        ).fetchone()
        return _row_to_run(row) if row is not None else None

    def _set_status_keeping_open(self, run_id: int, status: RunStatus) -> None:
        self._connection.execute("UPDATE runs SET status = ? WHERE id = ?", (status.value, run_id))

    def for_task(self, task_id: str) -> list[Run]:
        """Return every run recorded for `task_id`."""
        return fetch_runs_for_task(self._connection, task_id)

    def set_session(self, run_id: int, session_id: str | None, resume_command: str | None) -> None:
        """Record `run_id`'s launched session id and resume command.

        `session_id` is `None` when `SessionStarted` says the session can't be resumed,
        and `resume_command` is `None` when the agent's `resume_command()` returns none.
        """
        self._connection.execute(
            "UPDATE runs SET session_id = ?, resume_command = ? WHERE id = ?",
            (session_id, resume_command, run_id),
        )

    def set_rung(self, run_id: int, rung: Rung) -> None:
        """Record a same-agent model or effort change on an existing session."""
        self._connection.execute(
            "UPDATE runs SET model = ?, effort = ? WHERE id = ?",
            (rung.model, rung.effort, run_id),
        )

    def set_last_message(self, run_id: int, message: str) -> None:
        """Persist `run_id`'s latest known message from the agent."""
        self._connection.execute("UPDATE runs SET last_message = ? WHERE id = ?", (message, run_id))

    def record_usage(self, run_id: int, usage: Usage) -> None:
        """Overwrite `run_id`'s normalized usage with its latest cumulative snapshot."""
        self._connection.execute(
            """
            UPDATE runs SET uncached_input_tokens = ?, output_tokens = ?, cache_read_tokens = ?,
              cache_write_tokens = ?, cost_usd = ?, cost_basis = ?, context_used_pct = ?,
              usage_reported = 1, usage_accounting = 'categories'
            WHERE id = ?
            """,
            (
                usage.uncached_input_tokens,
                usage.output_tokens,
                usage.cache_read_tokens,
                usage.cache_write_tokens,
                usage.cost_usd,
                usage.cost_basis,
                usage.context_used_pct,
                run_id,
            ),
        )

    def close(self, run_id: int) -> None:
        """Mark `run_id` closed: the task ended, the session is not resumable."""
        self._set_status(run_id, RunStatus.CLOSED)

    def _set_status(self, run_id: int, status: RunStatus) -> None:
        self._connection.execute(
            "UPDATE runs SET status = ?, ended_at = ? WHERE id = ?",
            (status.value, format_timestamp(self._clock.now()), run_id),
        )
