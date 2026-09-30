"""Task rows: insert, transitions, holds and dependent repointing (docs/design/05-data.md §2)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import TypeAdapter

from shikiru.agents.contracts import Rung
from shikiru.domain.reports import SIBLING_REFERENCE_PREFIX, resolve_dependency
from shikiru.domain.scheduling import find_dependency_cycle
from shikiru.domain.tasks import (
    ACTIVE_STATUSES,
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
from shikiru.errors import ShikiruError, UsageError
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
from shikiru.store.events import EventLog

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Sequence

    from shikiru.clock import Clock
    from shikiru.domain.commands import Submit
    from shikiru.domain.events import EventKind
    from shikiru.domain.reports import NewTask
    from shikiru.domain.scheduling import WaitReason
    from shikiru.domain.steps import Transition

TASK_ID_PREFIX = "T-"
TASK_SEQUENCE = "task"

_HOLDS_ADAPTER: TypeAdapter[frozenset[Hold]] = TypeAdapter(frozenset[Hold])
_COUNTERS_ADAPTER: TypeAdapter[Counters] = TypeAdapter(Counters)
_RUNG_INDEX_ADAPTER: TypeAdapter[dict[Role, int]] = TypeAdapter(dict[Role, int])
_OVERRIDES_ADAPTER: TypeAdapter[dict[Role, Rung]] = TypeAdapter(dict[Role, Rung])
_PANES_ADAPTER: TypeAdapter[dict[Role, str]] = TypeAdapter(dict[Role, str])
_REPO_ADAPTER: TypeAdapter[RepoState] = TypeAdapter(RepoState)

_TASK_COLUMNS = (
    "id, seq, parent_id, origin, title, request, mode, complexity, priority, status, step, "
    "status_reason, holds, iteration, counters, rung_index, overrides, repo, slot, window_id, "
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
        repo=load_json(_REPO_ADAPTER, row["repo"]) if row["repo"] is not None else None,
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
        task_id = submit.task_id
        if task_id is None:
            message = "submit command has no allocated task id"
            raise ValueError(message)
        seq = int(task_id.removeprefix(TASK_ID_PREFIX))
        overrides: dict[Role, Rung] = {}
        if submit.leader is not None:
            overrides[Role.LEADER] = submit.leader
        if submit.implementer is not None:
            overrides[Role.IMPLEMENTER] = submit.implementer
        with transaction(self._connection):
            self._insert_row(
                task_id=task_id,
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
        return self._fetch_or_raise(task_id)

    def apply_scope_change(
        self,
        task_id: str,
        transition: Transition,
        new_tasks: Sequence[NewTask],
        *,
        origin: TaskOrigin,
    ) -> list[Task]:
        """Atomically transition a parent, enqueue children and repoint its dependents."""
        with transaction(self._connection):
            parent = fetch_task(self._connection, task_id)
            if parent is None:
                message = f"task {task_id} does not exist"
                raise UsageError(message)
            self._validate_scope_references(new_tasks)
            self.apply_transition(task_id, transition)
            if transition.status in (Status.DONE, Status.CANCELLED):
                self.set_slot(task_id, None)
            child_ids = [self._next_task_id() for _ in new_tasks]
            resolved_dependencies = [
                [resolve_dependency(ref, child_ids) for ref in new_task.depends_on]
                for new_task in new_tasks
            ]
            child_dependencies: list[tuple[str, str]] = []
            await_parent = not transition.repoint_dependents
            for child_id, new_task, depends_on in zip(
                child_ids, new_tasks, resolved_dependencies, strict=True
            ):
                self._insert_row(
                    task_id=child_id,
                    seq=int(child_id.removeprefix(TASK_ID_PREFIX)),
                    parent_id=parent.id,
                    origin=origin,
                    title=new_task.title,
                    request=new_task.request,
                    mode=None,
                    complexity="normal",
                    priority=new_task.priority,
                    depends_on=(),
                    overrides={},
                )
                child_dependencies.extend((child_id, dependency) for dependency in depends_on)
                if await_parent and parent.id not in depends_on:
                    child_dependencies.append((child_id, parent.id))
            self._connection.executemany(
                "INSERT INTO task_deps (task_id, depends_on) VALUES (?, ?)", child_dependencies
            )
            if transition.repoint_dependents:
                self._repoint_dependents(task_id, child_ids)
            self._validate_acyclic()
        return [self._fetch_or_raise(child_id) for child_id in child_ids]

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

    def _next_task_id(self) -> str:
        return f"{TASK_ID_PREFIX}{next_sequence(self._connection, TASK_SEQUENCE)}"

    def _validate_scope_references(self, new_tasks: Sequence[NewTask]) -> None:
        existing_references = {
            ref
            for new_task in new_tasks
            for ref in new_task.depends_on
            if not ref.startswith(SIBLING_REFERENCE_PREFIX)
        }
        if existing_references:
            placeholders = ", ".join("?" for _ in existing_references)
            rows = self._connection.execute(
                f"SELECT id FROM tasks WHERE id IN ({placeholders})",
                tuple(existing_references),
            ).fetchall()
            found = {row["id"] for row in rows}
            missing = existing_references - found
            if missing:
                reference = min(missing)
                message = f"scope change dependency {reference!r} does not exist"
                raise UsageError(message)

    def _validate_acyclic(self) -> None:
        graph: dict[str, list[str]] = {}
        for row in self._connection.execute("SELECT task_id, depends_on FROM task_deps"):
            graph.setdefault(row["task_id"], []).append(row["depends_on"])
        cycle = find_dependency_cycle(graph)
        if cycle:
            message = f"scope change creates a dependency cycle: {', '.join(cycle)}"
            raise UsageError(message)

    def set_mode(self, task_id: str, mode: Mode) -> None:
        """Persist the mode selected by a completed triage turn."""
        self._connection.execute("UPDATE tasks SET mode = ? WHERE id = ?", (mode.value, task_id))

    def _fetch_or_raise(self, task_id: str) -> Task:
        task = fetch_task(self._connection, task_id)
        return require_after_insert(task, f"task {task_id} vanished immediately after insert")

    def get(self, task_id: str) -> Task | None:
        """Read one task by id, or `None` if it doesn't exist."""
        return fetch_task(self._connection, task_id)

    def require(self, task_id: str) -> Task:
        """Read one task by id, or raise when it disappeared."""
        task = self.get(task_id)
        if task is None:
            message = f"{task_id} vanished while its flow was running"
            raise ShikiruError(message)
        return task

    def list_active(self) -> list[Task]:
        """Read every task holding a slot: `running`, `integrating` or `escalated`."""
        return fetch_tasks(self._connection, statuses=ACTIVE_STATUSES)

    def list_all(self) -> list[Task]:
        """Read every task, the universe `domain.scheduling.pick_ready` needs for dep lookups."""
        return fetch_tasks(self._connection)

    def apply_transition(self, task_id: str, transition: Transition) -> None:
        """Persist `transition`'s step, status, iteration, counters, rungs and triage results.

        Overrides are merged into the stored row: the transition sets its own roles and clears a
        role only while it still holds the rung the transition saw, so a concurrent operator
        override survives the turn that was running (03 §2-3, §6-7). Complexity and title are
        written only when the transition sets them.
        """
        overrides = self._merged_overrides(task_id, transition)
        now = format_timestamp(self._clock.now())
        status = transition.status.value
        self._connection.execute(
            """
            UPDATE tasks SET
              step = ?, status = ?, status_reason = ?, iteration = ?,
              counters = ?, rung_index = ?, overrides = ?,
              complexity = COALESCE(?, complexity), title = COALESCE(?, title),
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
                dump_json(_OVERRIDES_ADAPTER, overrides),
                transition.complexity,
                transition.title,
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

    def _merged_overrides(self, task_id: str, transition: Transition) -> dict[Role, Rung]:
        stored = self.require(task_id).overrides
        kept = {
            role: rung
            for role, rung in stored.items()
            if transition.cleared_overrides.get(role) != rung
        }
        return {**kept, **transition.set_overrides}

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

    def finish(
        self,
        task_id: str,
        transition: Transition,
        event: EventKind,
        *,
        message: str,
        data: dict[str, Any],
    ) -> None:
        """Enter a terminal `transition`, free the slot and append its terminal `event` at once.

        Status and event land in one transaction, so a restart never sees a terminal task
        without its terminal event, nor the event twice (03 §7).
        """
        with transaction(self._connection):
            self.apply_transition(task_id, transition)
            self.set_slot(task_id, None)
            EventLog(self._connection, self._clock).append(
                event, task_id=task_id, message=message, data=data
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

    def set_repo(self, task_id: str, repo: RepoState) -> None:
        """Record `task_id`'s checkout state (03 §5)."""
        self._connection.execute(
            "UPDATE tasks SET repo = ? WHERE id = ?",
            (dump_json(_REPO_ADAPTER, repo), task_id),
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

    def set_depends_on(self, task_id: str, depends_on: Sequence[str]) -> None:
        """Replace `task_id`'s dependency set (03 §2's `NewTask` `#k` sibling references)."""
        with transaction(self._connection):
            self._connection.execute("DELETE FROM task_deps WHERE task_id = ?", (task_id,))
            self._connection.executemany(
                "INSERT INTO task_deps (task_id, depends_on) VALUES (?, ?)",
                [(task_id, dep) for dep in depends_on],
            )

    def _repoint_dependents(self, old_task_id: str, new_task_ids: Sequence[str]) -> None:
        dependents = [
            row["task_id"]
            for row in self._connection.execute(
                "SELECT task_id FROM task_deps WHERE depends_on = ?", (old_task_id,)
            ).fetchall()
        ]
        self._connection.execute("DELETE FROM task_deps WHERE depends_on = ?", (old_task_id,))
        self._connection.executemany(
            "INSERT INTO task_deps (task_id, depends_on) VALUES (?, ?)",
            [(dependent, new_task_id) for dependent in dependents for new_task_id in new_task_ids],
        )
