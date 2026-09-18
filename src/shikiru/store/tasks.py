"""Task rows: insert, transitions, holds and dependent repointing (docs/design/05-data.md §2)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from shikiru.agents.contracts import Rung
from shikiru.domain.tasks import (
    Counters,
    Hold,
    Mode,
    RepoState,
    Role,
    Status,
    Step,
    Task,
    TaskOrigin,
)
from shikiru.store.database import (
    dump_json,
    format_timestamp,
    load_json,
    next_sequence,
    parse_required_timestamp,
    parse_timestamp,
    require_after_insert,
    transaction,
)

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Sequence

    from shikiru.clock import Clock
    from shikiru.domain.commands import Submit
    from shikiru.domain.reports import NewTask
    from shikiru.domain.scheduling import WaitReason
    from shikiru.domain.steps import Transition

TASK_ID_PREFIX = "T-"
TASK_SEQUENCE = "task"

_ACTIVE_STATUSES = (Status.RUNNING, Status.INTEGRATING, Status.ESCALATED)

_HOLDS_ADAPTER: TypeAdapter[frozenset[Hold]] = TypeAdapter(frozenset[Hold])
_COUNTERS_ADAPTER: TypeAdapter[Counters] = TypeAdapter(Counters)
_RUNG_INDEX_ADAPTER: TypeAdapter[dict[Role, int]] = TypeAdapter(dict[Role, int])
_OVERRIDES_ADAPTER: TypeAdapter[dict[Role, Rung]] = TypeAdapter(dict[Role, Rung])
_PANES_ADAPTER: TypeAdapter[dict[Role, str]] = TypeAdapter(dict[Role, str])
_REPOS_ADAPTER: TypeAdapter[list[RepoState]] = TypeAdapter(list[RepoState])

_TASK_COLUMNS = (
    "id, seq, parent_id, origin, title, request, mode, complexity, priority, status, step, "
    "status_reason, holds, iteration, counters, rung_index, overrides, repos, slot, window_id, "
    "panes, wait_reason, requeued_at, summary, created_at, started_at, finished_at"
)


def _row_to_task(row: sqlite3.Row, depends_on: Sequence[str]) -> Task:
    return Task(
        id=row["id"],
        seq=row["seq"],
        parent_id=row["parent_id"],
        origin=TaskOrigin(row["origin"]),
        title=row["title"],
        request=row["request"],
        mode=Mode(row["mode"]) if row["mode"] is not None else None,
        complexity=row["complexity"],
        priority=row["priority"],
        depends_on=list(depends_on),
        status=Status(row["status"]),
        step=Step(row["step"]) if row["step"] is not None else None,
        status_reason=row["status_reason"],
        holds=load_json(_HOLDS_ADAPTER, row["holds"]),
        iteration=row["iteration"],
        counters=load_json(_COUNTERS_ADAPTER, row["counters"]),
        rung_index=load_json(_RUNG_INDEX_ADAPTER, row["rung_index"]),
        overrides=load_json(_OVERRIDES_ADAPTER, row["overrides"]),
        repos=load_json(_REPOS_ADAPTER, row["repos"]),
        slot=row["slot"],
        window_id=row["window_id"],
        panes=load_json(_PANES_ADAPTER, row["panes"]),
        wait_reason=row["wait_reason"],
        requeued_at=parse_timestamp(row["requeued_at"]),
        summary=row["summary"],
        created_at=parse_required_timestamp(row["created_at"]),
        started_at=parse_timestamp(row["started_at"]),
        finished_at=parse_timestamp(row["finished_at"]),
    )


def _fetch_depends_on(
    connection: sqlite3.Connection, task_ids: Sequence[str]
) -> dict[str, list[str]]:
    deps: dict[str, list[str]] = {task_id: [] for task_id in task_ids}
    if not task_ids:
        return deps
    placeholders = ", ".join("?" for _ in task_ids)
    rows = connection.execute(
        f"SELECT task_id, depends_on FROM task_deps WHERE task_id IN ({placeholders})",
        tuple(task_ids),
    ).fetchall()
    for row in rows:
        deps[row["task_id"]].append(row["depends_on"])
    return deps


def _rows_to_tasks(connection: sqlite3.Connection, rows: Sequence[sqlite3.Row]) -> list[Task]:
    deps = _fetch_depends_on(connection, [row["id"] for row in rows])
    return [_row_to_task(row, deps[row["id"]]) for row in rows]


def fetch_task(connection: sqlite3.Connection, task_id: str) -> Task | None:
    """Read one task by id, or `None` if it doesn't exist."""
    row = connection.execute(
        f"SELECT {_TASK_COLUMNS} FROM tasks WHERE id = ?", (task_id,)
    ).fetchone()
    if row is None:
        return None
    return _row_to_task(row, _fetch_depends_on(connection, [task_id])[task_id])


def fetch_tasks(
    connection: sqlite3.Connection, *, statuses: Sequence[Status] | None = None
) -> list[Task]:
    """Read every task, optionally restricted to `statuses`, ordered by `seq`."""
    if statuses is None:
        rows = connection.execute(f"SELECT {_TASK_COLUMNS} FROM tasks ORDER BY seq").fetchall()
    else:
        placeholders = ", ".join("?" for _ in statuses)
        rows = connection.execute(
            f"SELECT {_TASK_COLUMNS} FROM tasks WHERE status IN ({placeholders}) ORDER BY seq",
            tuple(status.value for status in statuses),
        ).fetchall()
    return _rows_to_tasks(connection, rows)


class TaskStore:
    """Inserts task rows, applies transitions and updates their scheduling fields (03 §1-2)."""

    def __init__(self, connection: sqlite3.Connection, clock: Clock) -> None:
        """Use `connection` (the engine's writer) and `clock` for every write below."""
        self._connection = connection
        self._clock = clock

    def insert_from_submit(self, submit: Submit) -> Task:
        """Insert the task a `Submit` command already allocated an id for (principle 3)."""
        seq = int(submit.task_id.removeprefix(TASK_ID_PREFIX))
        overrides: dict[Role, Rung] = {}
        if submit.leader is not None:
            overrides[Role.LEADER] = submit.leader
        if submit.implementer is not None:
            overrides[Role.IMPLEMENTER] = submit.implementer
        with transaction(self._connection):
            self._insert_row(
                task_id=submit.task_id,
                seq=seq,
                parent_id=None,
                origin=TaskOrigin.OPERATOR,
                title=submit.title,
                request=submit.request,
                mode=submit.mode,
                complexity=submit.complexity,
                priority=submit.priority,
                depends_on=submit.depends_on,
                overrides=overrides,
            )
        return self._fetch_or_raise(submit.task_id)

    def insert_child(
        self,
        new_task: NewTask,
        *,
        depends_on: Sequence[str],
        parent_id: str,
        origin: TaskOrigin,
    ) -> Task:
        """Insert a task enqueued from a split, plan or verdict report (03 §2, §6; D4).

        `depends_on` is the caller's already-resolved dependency ids; `new_task.depends_on`
        (raw `T-n`/`#k` references) is not read here.
        """
        with transaction(self._connection):
            seq = next_sequence(self._connection, TASK_SEQUENCE)
            task_id = f"{TASK_ID_PREFIX}{seq}"
            self._insert_row(
                task_id=task_id,
                seq=seq,
                parent_id=parent_id,
                origin=origin,
                title=new_task.title,
                request=new_task.request,
                mode=None,
                complexity="normal",
                priority=new_task.priority,
                depends_on=depends_on,
                overrides={},
            )
        return self._fetch_or_raise(task_id)

    def _insert_row(
        self,
        *,
        task_id: str,
        seq: int,
        parent_id: str | None,
        origin: TaskOrigin,
        title: str,
        request: str,
        mode: Mode | None,
        complexity: str,
        priority: int,
        depends_on: Sequence[str],
        overrides: dict[Role, Rung],
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO tasks (
              id, seq, parent_id, origin, title, request, mode, complexity, priority,
              status, overrides, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                seq,
                parent_id,
                origin.value,
                title,
                request,
                mode.value if mode is not None else None,
                complexity,
                priority,
                Status.QUEUED.value,
                dump_json(_OVERRIDES_ADAPTER, overrides),
                format_timestamp(self._clock.now()),
            ),
        )
        self._connection.executemany(
            "INSERT INTO task_deps (task_id, depends_on) VALUES (?, ?)",
            [(task_id, dep) for dep in depends_on],
        )

    def _fetch_or_raise(self, task_id: str) -> Task:
        task = fetch_task(self._connection, task_id)
        return require_after_insert(task, f"task {task_id} vanished immediately after insert")

    def get(self, task_id: str) -> Task | None:
        """Read one task by id, or `None` if it doesn't exist."""
        return fetch_task(self._connection, task_id)

    def list_queued(self) -> list[Task]:
        """Read every `queued` task, ordered by `seq`."""
        return fetch_tasks(self._connection, statuses=(Status.QUEUED,))

    def list_active(self) -> list[Task]:
        """Read every task holding a slot: `running`, `integrating` or `escalated`."""
        return fetch_tasks(self._connection, statuses=_ACTIVE_STATUSES)

    def list_all(self) -> list[Task]:
        """Read every task, the universe `domain.scheduling.pick_ready` needs for dep lookups."""
        return fetch_tasks(self._connection)

    def apply_transition(self, task_id: str, transition: Transition) -> None:
        """Persist `transition`'s step, status, iteration, counters and rungs (03 §2-3, §6-7)."""
        now = format_timestamp(self._clock.now())
        status = transition.status.value
        self._connection.execute(
            """
            UPDATE tasks SET
              step = ?, status = ?, status_reason = ?, iteration = ?,
              counters = ?, rung_index = ?, overrides = ?,
              started_at = CASE WHEN ? = ? AND started_at IS NULL THEN ? ELSE started_at END,
              finished_at = CASE WHEN ? IN (?, ?) AND finished_at IS NULL
                THEN ? ELSE finished_at END
            WHERE id = ?
            """,
            (
                transition.step.value if transition.step is not None else None,
                status,
                transition.status_reason,
                transition.iteration,
                dump_json(_COUNTERS_ADAPTER, transition.counters),
                dump_json(_RUNG_INDEX_ADAPTER, transition.rung_index),
                dump_json(_OVERRIDES_ADAPTER, transition.overrides),
                status,
                Status.RUNNING.value,
                now,
                status,
                Status.DONE.value,
                Status.CANCELLED.value,
                now,
                task_id,
            ),
        )

    def set_holds(self, task_id: str, holds: frozenset[Hold]) -> None:
        """Persist `task_id`'s task-scoped holds (03 §1.2, §9)."""
        self._connection.execute(
            "UPDATE tasks SET holds = ? WHERE id = ?",
            (dump_json(_HOLDS_ADAPTER, holds), task_id),
        )

    def set_priority(self, task_id: str, priority: int) -> None:
        """Set `task_id`'s dispatch priority (`prioritize`)."""
        self._connection.execute("UPDATE tasks SET priority = ? WHERE id = ?", (priority, task_id))

    def set_wait_reason(self, task_id: str, reason: WaitReason | None) -> None:
        """Persist why the scheduler isn't dispatching `task_id` yet (03 §10)."""
        self._connection.execute(
            "UPDATE tasks SET wait_reason = ? WHERE id = ?",
            (reason.value if reason is not None else None, task_id),
        )

    def set_slot(self, task_id: str, slot: int | None) -> None:
        """Assign or clear `task_id`'s dispatch slot."""
        self._connection.execute("UPDATE tasks SET slot = ? WHERE id = ?", (slot, task_id))

    def set_window(self, task_id: str, window_id: str | None) -> None:
        """Record `task_id`'s tmux window id."""
        self._connection.execute(
            "UPDATE tasks SET window_id = ? WHERE id = ?", (window_id, task_id)
        )

    def set_panes(self, task_id: str, panes: dict[Role, str]) -> None:
        """Record `task_id`'s `{role: pane_id}` map, the only place pane ids live (03 §1.1)."""
        self._connection.execute(
            "UPDATE tasks SET panes = ? WHERE id = ?",
            (dump_json(_PANES_ADAPTER, panes), task_id),
        )

    def set_repos(self, task_id: str, repos: Sequence[RepoState]) -> None:
        """Record `task_id`'s repo checkout state (03 §5)."""
        self._connection.execute(
            "UPDATE tasks SET repos = ? WHERE id = ?",
            (dump_json(_REPOS_ADAPTER, list(repos)), task_id),
        )

    def set_requeued(self, task_id: str) -> None:
        """Mark `task_id` requeued now, giving it dispatch precedence (03 §1.1)."""
        self._connection.execute(
            "UPDATE tasks SET requeued_at = ? WHERE id = ?",
            (format_timestamp(self._clock.now()), task_id),
        )

    def set_summary(self, task_id: str, summary: str) -> None:
        """Record `task_id`'s final summary."""
        self._connection.execute("UPDATE tasks SET summary = ? WHERE id = ?", (summary, task_id))

    def repoint_dependents(self, old_task_id: str, new_task_ids: Sequence[str]) -> None:
        """Repoint every dependency on `old_task_id` onto each of `new_task_ids` (03 §2, §6)."""
        with transaction(self._connection):
            dependents = [
                row["task_id"]
                for row in self._connection.execute(
                    "SELECT task_id FROM task_deps WHERE depends_on = ?", (old_task_id,)
                ).fetchall()
            ]
            self._connection.execute("DELETE FROM task_deps WHERE depends_on = ?", (old_task_id,))
            self._connection.executemany(
                "INSERT INTO task_deps (task_id, depends_on) VALUES (?, ?)",
                [
                    (dependent, new_task_id)
                    for dependent in dependents
                    for new_task_id in new_task_ids
                ],
            )
