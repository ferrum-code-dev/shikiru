"""Context windows agents reported for their models."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import sqlite3  # noqa: TID251


class ContextWindowStore:
    """Remembers the latest context window each agent reported for each of its models."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Use `connection`, the engine's writer."""
        self._connection = connection

    def window(self, agent: str, model: str) -> int | None:
        """Return the window last reported for `model` on `agent`, or `None` if none was."""
        row = self._connection.execute(
            "SELECT tokens FROM model_context_windows WHERE agent = ? AND model = ?",
            (agent, model),
        ).fetchone()
        return row["tokens"] if row is not None else None

    def record(self, agent: str, model: str, tokens: int) -> None:
        """Replace the window remembered for `model` on `agent` with `tokens`."""
        self._connection.execute(
            """
            INSERT INTO model_context_windows (agent, model, tokens) VALUES (?, ?, ?)
            ON CONFLICT (agent, model) DO UPDATE SET tokens = excluded.tokens
            """,
            (agent, model, tokens),
        )
