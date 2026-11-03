"""The durable progress of PR feedback rounds and of the replies they owe."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from shikiru.domain.feedback import FeedbackSnapshot, FeedbackSourceKind
from shikiru.domain.feedback_rounds import (
    UNFINISHED_STAGES,
    FeedbackReply,
    FeedbackRound,
    FeedbackStage,
    ReplyState,
)
from shikiru.domain.reports import FeedbackAnalysis
from shikiru.store.database import transaction

if TYPE_CHECKING:
    import sqlite3  # noqa: TID251
    from collections.abc import Sequence

_ROUND_COLUMNS = (
    "id, task_id, pull_request_url, review_ids, snapshot, stage, analysis, attempts, blocked, "
    "expected_remote_sha, verified_sha, published_sha, checks, verified_replies"
)
_REPLY_COLUMNS = "round_id, source_id, kind, marker, body, state, posted_url"
_UNFINISHED = tuple(stage.value for stage in sorted(UNFINISHED_STAGES))


class FeedbackStore:
    """Stores, per task, the feedback rounds of its pull request and each round's replies."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Use `connection`, the engine's writer."""
        self._connection = connection

    def start(
        self,
        *,
        task_id: str,
        pull_request_url: str,
        review_ids: Sequence[str],
        snapshot: FeedbackSnapshot,
        expected_remote_sha: str,
    ) -> FeedbackRound:
        """Record a new round over `snapshot`, starting at its analysis."""
        cursor = self._connection.execute(
            "INSERT INTO feedback_rounds "
            "(task_id, pull_request_url, review_ids, snapshot, stage, expected_remote_sha) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                task_id,
                pull_request_url,
                json.dumps(list(review_ids)),
                snapshot.model_dump_json(),
                FeedbackStage.ANALYSIS.value,
                expected_remote_sha,
            ),
        )
        return self._require(_row_id(cursor))

    def active(self, task_id: str) -> FeedbackRound | None:
        """Return `task_id`'s unfinished round, if it has one."""
        row = self._connection.execute(
            f"SELECT {_ROUND_COLUMNS} FROM feedback_rounds WHERE task_id = ? "
            f"AND stage IN ({_marks(_UNFINISHED)}) ORDER BY id LIMIT 1",
            (task_id, *_UNFINISHED),
        ).fetchone()
        return None if row is None else _round(row)

    def last_pull_request_url(self, task_id: str) -> str | None:
        """Return the pull request of `task_id`'s latest round, or `None` without any round."""
        row = self._connection.execute(
            "SELECT pull_request_url FROM feedback_rounds WHERE task_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        return None if row is None else str(row["pull_request_url"])

    def seen_review_ids(self, task_id: str, pull_request_url: str) -> set[str]:
        """Return every review a round of `task_id` on `pull_request_url` was started for."""
        rows = self._connection.execute(
            "SELECT review_ids FROM feedback_rounds WHERE task_id = ? AND pull_request_url = ?",
            (task_id, pull_request_url),
        ).fetchall()
        return {review for row in rows for review in json.loads(row["review_ids"])}

    def set_stage(self, round_id: int, stage: FeedbackStage) -> None:
        """Move the round to `stage`."""
        self._set(round_id, "stage", stage.value)

    def save_analysis(
        self, round_id: int, analysis: FeedbackAnalysis, stage: FeedbackStage
    ) -> None:
        """Keep the Leader's `analysis` and move the round to `stage`."""
        with transaction(self._connection):
            self._set(round_id, "analysis", analysis.model_dump_json())
            self._set(round_id, "stage", stage.value)

    def complete_implementation(self, round_id: int, attempts: int, checks: str) -> None:
        """Atomically record a completed rework and advance to verification."""
        with transaction(self._connection):
            self._set(round_id, "checks", checks)
            self._set(round_id, "attempts", attempts)
            self._set(round_id, "stage", FeedbackStage.VERIFICATION.value)

    def set_blocked(self, round_id: int, *, blocked: bool) -> None:
        """Record whether the round waits for the Operator."""
        self._set(round_id, "blocked", int(blocked))

    def reset_after_retry(self, round_id: int, stage: FeedbackStage) -> None:
        """Clear the block and the attempts of a retried round and resume it at `stage`."""
        with transaction(self._connection):
            self._set(round_id, "blocked", 0)
            self._set(round_id, "attempts", 0)
            self._set(round_id, "stage", stage.value)

    def save_verified(
        self,
        round_id: int,
        *,
        sha: str,
        replies: dict[str, str],
        stage: FeedbackStage,
        planned: Sequence[FeedbackReply] = (),
    ) -> None:
        """Record the tip and replies the Leader verified, with any `planned` replies owed now.

        The verification, the planned replies and the move to `stage` commit together.
        """
        with transaction(self._connection):
            self._set(round_id, "verified_sha", sha)
            self._set(round_id, "verified_replies", json.dumps(replies))
            self._insert_replies(planned)
            self._set(round_id, "stage", stage.value)

    def set_expected_remote(self, round_id: int, sha: str) -> None:
        """Record the remote head the round's push may overwrite."""
        self._set(round_id, "expected_remote_sha", sha)

    def save_published(self, round_id: int, sha: str) -> None:
        """Record the commit published to the remote branch."""
        self._set(round_id, "published_sha", sha)

    def plan_replies(self, round_id: int, replies: Sequence[FeedbackReply]) -> None:
        """Store the replies the round owes, keeping any already stored, and move to sending."""
        with transaction(self._connection):
            self._insert_replies(replies)
            self._set(round_id, "stage", FeedbackStage.REPLIES.value)

    def replies(self, round_id: int) -> list[FeedbackReply]:
        """Return the round's replies in the order they were planned."""
        rows = self._connection.execute(
            f"SELECT {_REPLY_COLUMNS} FROM feedback_replies WHERE round_id = ? ORDER BY rowid",
            (round_id,),
        ).fetchall()
        return [_reply(row) for row in rows]

    def set_reply_state(
        self, round_id: int, source_id: str, state: ReplyState, posted_url: str | None = None
    ) -> None:
        """Record how far the reply to `source_id` got."""
        self._connection.execute(
            "UPDATE feedback_replies SET state = ?, posted_url = COALESCE(?, posted_url) "
            "WHERE round_id = ? AND source_id = ?",
            (state.value, posted_url, round_id, source_id),
        )

    def _insert_replies(self, replies: Sequence[FeedbackReply]) -> None:
        for reply in replies:
            self._connection.execute(
                f"INSERT OR IGNORE INTO feedback_replies ({_REPLY_COLUMNS}) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    reply.round_id,
                    reply.source_id,
                    reply.kind.value,
                    reply.marker,
                    reply.body,
                    reply.state.value,
                    reply.posted_url,
                ),
            )

    def _require(self, round_id: int) -> FeedbackRound:
        row = self._connection.execute(
            f"SELECT {_ROUND_COLUMNS} FROM feedback_rounds WHERE id = ?", (round_id,)
        ).fetchone()
        if row is None:
            message = f"feedback round {round_id} vanished"
            raise LookupError(message)
        return _round(row)

    def _set(self, round_id: int, column: str, value: str | int) -> None:
        self._connection.execute(
            f"UPDATE feedback_rounds SET {column} = ? WHERE id = ?", (value, round_id)
        )


def _row_id(cursor: sqlite3.Cursor) -> int:
    row_id = cursor.lastrowid
    if row_id is None:
        message = "inserted feedback round has no row id"
        raise LookupError(message)
    return row_id


def _marks(values: Sequence[str]) -> str:
    return ", ".join("?" for _ in values)


def _round(row: sqlite3.Row) -> FeedbackRound:
    analysis = row["analysis"]
    return FeedbackRound(
        id=row["id"],
        task_id=row["task_id"],
        pull_request_url=row["pull_request_url"],
        review_ids=json.loads(row["review_ids"]),
        snapshot=FeedbackSnapshot.model_validate_json(row["snapshot"]),
        stage=FeedbackStage(row["stage"]),
        analysis=None if analysis is None else FeedbackAnalysis.model_validate_json(analysis),
        attempts=row["attempts"],
        blocked=bool(row["blocked"]),
        expected_remote_sha=row["expected_remote_sha"],
        verified_sha=row["verified_sha"],
        published_sha=row["published_sha"],
        checks=row["checks"],
        verified_replies=json.loads(row["verified_replies"]),
    )


def _reply(row: sqlite3.Row) -> FeedbackReply:
    return FeedbackReply(
        round_id=row["round_id"],
        source_id=row["source_id"],
        kind=FeedbackSourceKind(row["kind"]),
        marker=row["marker"],
        body=row["body"],
        state=ReplyState(row["state"]),
        posted_url=row["posted_url"],
    )
