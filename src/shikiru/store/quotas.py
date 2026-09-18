"""Quota snapshots and agent blocks (docs/design/05-data.md §2; 03 §7, §9)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from shikiru.agents.contracts import QuotaWindow
from shikiru.store.database import format_timestamp, parse_required_timestamp, parse_timestamp

if TYPE_CHECKING:
    import sqlite3
    from datetime import datetime


@dataclass(frozen=True, slots=True)
class AgentBlock:
    """An agent blocked from dispatch until `until` (03 §7, §9)."""

    agent: str
    until: datetime
    reason: str


def _row_to_quota_window(row: sqlite3.Row) -> QuotaWindow:
    return QuotaWindow(
        name=row["window"],
        used_pct=row["used_pct"],
        resets_at=parse_timestamp(row["resets_at"]),
        observed_at=parse_required_timestamp(row["observed_at"]),
    )


def _row_to_agent_block(row: sqlite3.Row) -> AgentBlock:
    return AgentBlock(
        agent=row["agent"], until=parse_required_timestamp(row["until"]), reason=row["reason"]
    )


def fetch_quotas(connection: sqlite3.Connection, agent: str) -> list[QuotaWindow]:
    """Read every quota window recorded for `agent`."""
    rows = connection.execute(
        "SELECT agent, window, used_pct, resets_at, observed_at FROM quotas WHERE agent = ?",
        (agent,),
    ).fetchall()
    return [_row_to_quota_window(row) for row in rows]


def fetch_active_agent_blocks(connection: sqlite3.Connection, now: datetime) -> list[AgentBlock]:
    """Read every agent block still in effect at `now`."""
    rows = connection.execute(
        "SELECT agent, until, reason FROM agent_blocks WHERE until > ?",
        (format_timestamp(now),),
    ).fetchall()
    return [_row_to_agent_block(row) for row in rows]


def fetch_agent_block(connection: sqlite3.Connection, agent: str) -> AgentBlock | None:
    """Read an agent's block, including one whose deadline has passed."""
    row = connection.execute(
        "SELECT agent, until, reason FROM agent_blocks WHERE agent = ?", (agent,)
    ).fetchone()
    return _row_to_agent_block(row) if row is not None else None


class QuotaStore:
    """Persists quota snapshots and agent blocks (03 §7, §9)."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Use `connection` for every write below."""
        self._connection = connection

    def record(self, agent: str, window: QuotaWindow) -> None:
        """Upsert `agent`'s quota window, keeping the plugin-supplied `observed_at` as-is."""
        self._connection.execute(
            """
            INSERT INTO quotas (agent, window, used_pct, resets_at, observed_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (agent, window) DO UPDATE SET
              used_pct = excluded.used_pct,
              resets_at = excluded.resets_at,
              observed_at = excluded.observed_at
            """,
            (
                agent,
                window.name,
                window.used_pct,
                format_timestamp(window.resets_at) if window.resets_at is not None else None,
                format_timestamp(window.observed_at),
            ),
        )

    def quotas_for(self, agent: str) -> list[QuotaWindow]:
        """Read every quota window recorded for `agent`."""
        return fetch_quotas(self._connection, agent)

    def block(self, agent: str, until: datetime, reason: str) -> None:
        """Block `agent` from dispatch until `until`, replacing any earlier block."""
        self._connection.execute(
            "INSERT INTO agent_blocks (agent, until, reason) VALUES (?, ?, ?) "
            "ON CONFLICT (agent) DO UPDATE SET until = excluded.until, reason = excluded.reason",
            (agent, format_timestamp(until), reason),
        )

    def clear_block(self, agent: str) -> None:
        """Remove `agent`'s block, if any."""
        self._connection.execute("DELETE FROM agent_blocks WHERE agent = ?", (agent,))

    def active_blocks(self, now: datetime) -> list[AgentBlock]:
        """Read every agent block still in effect at `now`."""
        return fetch_active_agent_blocks(self._connection, now)

    def block_for(self, agent: str) -> AgentBlock | None:
        """Read `agent`'s current block, whether or not it is still active."""
        return fetch_agent_block(self._connection, agent)
