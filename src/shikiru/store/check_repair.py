"""Persist check repair observations and their progress."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from shikiru.domain.check_repair import CheckFailure, CheckRepairRound, CheckRepairStage
from shikiru.store.database import transaction

if TYPE_CHECKING:
    import sqlite3  # noqa: TID251
    from collections.abc import Sequence


class CheckRepairStore:
    """Keep repair rounds and deduplicate observations of the same pull request head."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Use the engine's SQLite connection."""
        self._connection = connection

    def start(
        self, task_id: str, url: str, head: str, failures: Sequence[CheckFailure]
    ) -> CheckRepairRound:
        """Record a failing head once, preserving its original evidence on repeated polls."""
        if not failures:
            message = "a repair round requires failing check evidence"
            raise ValueError(message)
        with transaction(self._connection, immediate=True):
            self._connection.execute(
                "INSERT INTO check_repair_rounds "
                "(task_id, pull_request_url, head_sha, failures, stage) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(task_id, pull_request_url, head_sha) DO NOTHING",
                (
                    task_id,
                    url,
                    head,
                    json.dumps([failure.model_dump() for failure in failures]),
                    CheckRepairStage.ANALYSIS.value,
                ),
            )
            row = self._connection.execute(
                "SELECT * FROM check_repair_rounds "
                "WHERE task_id = ? AND pull_request_url = ? AND head_sha = ?",
                (task_id, url, head),
            ).fetchone()
        if row is None:
            message = "check repair round vanished after insertion"
            raise LookupError(message)
        return _round(row)

    def rounds(self, task_id: str, url: str) -> list[CheckRepairRound]:
        """Return the attempts for this pull request in the order they were recorded."""
        rows = self._connection.execute(
            "SELECT * FROM check_repair_rounds WHERE task_id = ? AND pull_request_url = ? "
            "ORDER BY id",
            (task_id, url),
        ).fetchall()
        return [_round(row) for row in rows]

    def advance(
        self,
        round_id: int,
        expected: CheckRepairStage,
        stage: CheckRepairStage,
        *,
        verified_sha: str | None = None,
        published_sha: str | None = None,
    ) -> bool:
        """Advance once from the expected stage, atomically recording publication evidence."""
        cursor = self._connection.execute(
            "UPDATE check_repair_rounds SET stage = ?, "
            "verified_sha = COALESCE(?, verified_sha), published_sha = COALESCE(?, published_sha) "
            "WHERE id = ? AND stage = ?",
            (stage.value, verified_sha, published_sha, round_id, expected.value),
        )
        return cursor.rowcount == 1


def _round(row: sqlite3.Row) -> CheckRepairRound:
    return CheckRepairRound(
        id=row["id"],
        task_id=row["task_id"],
        pull_request_url=row["pull_request_url"],
        head_sha=row["head_sha"],
        failures=[CheckFailure.model_validate(item) for item in json.loads(row["failures"])],
        stage=CheckRepairStage(row["stage"]),
        verified_sha=row["verified_sha"],
        published_sha=row["published_sha"],
    )
