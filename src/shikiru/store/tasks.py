"""Task rows: insert, transitions, holds and dependent repointing (docs/design/05-data.md §2)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from pydantic import TypeAdapter

from shikiru.agents.contracts import Rung
from shikiru.domain.escalation import EscalationStatus
from shikiru.domain.events import EventKind
from shikiru.domain.reports import SIBLING_REFERENCE_PREFIX, resolve_dependency
from shikiru.domain.scheduling import find_dependency_cycle
from shikiru.domain.tasks import (
    ACTIVE_STATUSES,
    DEFAULT_COMPLEXITY,
    Counters,
    Hold,
    Mode,
    RepoState,
    Role,
    Status,
    StatusReason,
    Step,
    Task,
    TaskOrigin,
)
from shikiru.errors import InvariantError, UsageError
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
from shikiru.store.events import EventLog, fetch_latest_events

if TYPE_CHECKING:
    import sqlite3  # noqa: TID251
    from collections.abc import Sequence

    from shikiru.clock import Clock
    from shikiru.config.settings import ComplexityName
    from shikiru.domain.commands import Submit
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
_BROWSER_CATEGORY_ACTIVE = "active"
_BROWSER_CATEGORY_WAITING = "waiting"
_BROWSER_CATEGORY_HISTORY = "history"
_BROWSER_CATEGORY_ALL = "all"
_BROWSER_STATUS_FILTERS = frozenset(status.value for status in Status)
_BROWSER_FILTERS = _BROWSER_STATUS_FILTERS | {
    _BROWSER_CATEGORY_ACTIVE,
    _BROWSER_CATEGORY_WAITING,
    _BROWSER_CATEGORY_HISTORY,
    _BROWSER_CATEGORY_ALL,
}
_RELATIONSHIP_LIMIT = 24
_FAMILY_OUTCOME_ORDER = (
    Status.QUEUED.value,
    Status.RUNNING.value,
    Status.INTEGRATING.value,
    Status.ESCALATED.value,
    Status.DONE.value,
    Status.CANCELLED.value,
    StatusReason.SUPERSEDED.value,
)


@dataclass(frozen=True)
class TaskBrowserPage:
    """A bounded task page with counts for the chosen filter and current project."""

    tasks: tuple[Task, ...]
    open_decision_task_ids: frozenset[str]
    matching_count: int
    filtered_count: int
    project_count: int
    selected_task_matches: bool
    ancestor_tasks: tuple[Task, ...] = ()


@dataclass(frozen=True)
class TaskRelationships:
    """A bounded task family group, separate dependency edges and recorded replacements."""

    family_parent: Task
    family_root: Task
    family_children: tuple[Task, ...]
    family_child_count: int
    dependencies: tuple[Task, ...]
    dependency_count: int
    blocking_dependencies: tuple[Task, ...]
    blocking_dependency_count: int
    dependents: tuple[Task, ...]
    dependent_count: int
    replacements: tuple[Task, ...]
    replacement_count: int
    family_outcomes: tuple[tuple[str, int], ...]
    page_offset: int
    page_size: int
    has_more: bool


_TASK_COLUMNS = (
    "id, seq, parent_id, origin, title, request, mode, complexity, complexity_explicit, "
    "priority, status, step, "
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
        complexity=row["complexity"] if row["complexity_explicit"] else None,
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


def fetch_task_browser_page(
    connection: sqlite3.Connection,
    *,
    category: str,
    search: str,
    limit: int,
    offset: int,
    selected_task_id: str | None = None,
    diagnosed_stall_task_ids: Sequence[str] = (),
    quota_wait_task_ids: Sequence[str] = (),
) -> TaskBrowserPage:
    """Read one ordered browser page and exact counts without loading the full task table."""
    if category not in _BROWSER_FILTERS:
        message = f"unknown task browser filter: {category!r}"
        raise ValueError(message)
    if limit < 1 or offset < 0:
        message = "task browser limit must be positive and offset must be non-negative"
        raise ValueError(message)

    category_where, category_parameters = _task_browser_category(category, quota_wait_task_ids)
    match_where, match_parameters = _task_browser_search(category_where, search)
    counts = connection.execute(
        "SELECT "
        f"(SELECT COUNT(*) FROM tasks t WHERE {category_where}) AS filtered_count, "
        f"(SELECT COUNT(*) FROM tasks t WHERE {match_where}) AS matching_count, "
        "(SELECT COUNT(*) FROM tasks) AS project_count",
        (*category_parameters, *category_parameters, *match_parameters),
    ).fetchone()
    order, order_parameters = _task_browser_order(category, diagnosed_stall_task_ids)
    rows = connection.execute(
        f"SELECT {_TASK_COLUMNS}, "
        "EXISTS (SELECT 1 FROM escalations e WHERE e.task_id = t.id AND e.status = ?) "
        f"AS has_open_decision FROM tasks t WHERE {match_where} "
        f"ORDER BY {order} LIMIT ? OFFSET ?",
        (
            EscalationStatus.OPEN.value,
            *category_parameters,
            *match_parameters,
            *order_parameters,
            limit,
            offset,
        ),
    ).fetchall()
    selected_task_matches = False
    if selected_task_id is not None:
        selected_task_matches = (
            connection.execute(
                f"SELECT EXISTS (SELECT 1 FROM tasks t WHERE t.id = ? AND {match_where})",
                (selected_task_id, *category_parameters, *match_parameters),
            ).fetchone()[0]
            == 1
        )
    tasks = _rows_to_tasks(connection, rows)
    ancestor_tasks = _fetch_task_ancestors(connection, [task.id for task in tasks])
    return TaskBrowserPage(
        tasks=tuple(tasks),
        open_decision_task_ids=frozenset(row["id"] for row in rows if row["has_open_decision"]),
        matching_count=counts["matching_count"],
        filtered_count=counts["filtered_count"],
        project_count=counts["project_count"],
        selected_task_matches=selected_task_matches,
        ancestor_tasks=tuple(ancestor_tasks),
    )


def _fetch_task_ancestors(connection: sqlite3.Connection, task_ids: Sequence[str]) -> list[Task]:
    if not task_ids:
        return []
    placeholders = ", ".join("?" for _ in task_ids)
    rows = connection.execute(
        "WITH RECURSIVE ancestry(parent_id) AS ("
        f"SELECT parent_id FROM tasks WHERE id IN ({placeholders}) AND parent_id IS NOT NULL "
        "UNION SELECT parent.parent_id FROM tasks parent "
        "JOIN ancestry child ON parent.id = child.parent_id "
        "WHERE parent.parent_id IS NOT NULL) "
        f"SELECT {_TASK_COLUMNS} FROM tasks WHERE id IN "
        "(SELECT parent_id FROM ancestry) ORDER BY seq",
        tuple(task_ids),
    ).fetchall()
    return _rows_to_tasks(connection, rows)


def _fetch_tasks_by_ids(
    connection: sqlite3.Connection, task_ids: Sequence[str], *, offset: int
) -> tuple[Task, ...]:
    page_ids = task_ids[offset : offset + _RELATIONSHIP_LIMIT]
    if not page_ids:
        return ()
    placeholders = ", ".join("?" for _ in page_ids)
    rows = connection.execute(
        f"SELECT {_TASK_COLUMNS} FROM tasks WHERE id IN ({placeholders}) ORDER BY seq",
        tuple(page_ids),
    ).fetchall()
    return tuple(_rows_to_tasks(connection, rows))


def _fetch_relationship_tasks(
    connection: sqlite3.Connection,
    *,
    column: str,
    task_id: str,
    offset: int,
) -> tuple[tuple[Task, ...], int]:
    rows = connection.execute(
        f"SELECT {_TASK_COLUMNS} FROM tasks WHERE {column} = ? ORDER BY seq LIMIT ? OFFSET ?",
        (task_id, _RELATIONSHIP_LIMIT, offset),
    ).fetchall()
    count = connection.execute(
        f"SELECT COUNT(*) FROM tasks WHERE {column} = ?", (task_id,)
    ).fetchone()[0]
    return tuple(_rows_to_tasks(connection, rows)), count


def _fetch_dependency_tasks(
    connection: sqlite3.Connection, task_id: str, *, offset: int
) -> tuple[tuple[Task, ...], int]:
    rows = connection.execute(
        f"SELECT {_TASK_COLUMNS} FROM tasks WHERE id IN "
        "(SELECT depends_on FROM task_deps WHERE task_id = ?) ORDER BY seq LIMIT ? OFFSET ?",
        (task_id, _RELATIONSHIP_LIMIT, offset),
    ).fetchall()
    count = connection.execute(
        "SELECT COUNT(*) FROM task_deps WHERE task_id = ?", (task_id,)
    ).fetchone()[0]
    return tuple(_rows_to_tasks(connection, rows)), count


def _fetch_blocking_dependency_tasks(
    connection: sqlite3.Connection, task_id: str, *, offset: int
) -> tuple[tuple[Task, ...], int]:
    rows = connection.execute(
        f"SELECT {_TASK_COLUMNS} FROM tasks WHERE status != ? AND id IN "
        "(SELECT depends_on FROM task_deps WHERE task_id = ?) ORDER BY seq LIMIT ? OFFSET ?",
        (Status.DONE.value, task_id, _RELATIONSHIP_LIMIT, offset),
    ).fetchall()
    count = connection.execute(
        "SELECT COUNT(*) FROM tasks WHERE status != ? AND id IN "
        "(SELECT depends_on FROM task_deps WHERE task_id = ?)",
        (Status.DONE.value, task_id),
    ).fetchone()[0]
    return tuple(_rows_to_tasks(connection, rows)), count


def _fetch_dependent_tasks(
    connection: sqlite3.Connection, task_id: str, *, offset: int
) -> tuple[tuple[Task, ...], int]:
    rows = connection.execute(
        f"SELECT {_TASK_COLUMNS} FROM tasks WHERE id IN "
        "(SELECT task_id FROM task_deps WHERE depends_on = ?) ORDER BY seq LIMIT ? OFFSET ?",
        (task_id, _RELATIONSHIP_LIMIT, offset),
    ).fetchall()
    count = connection.execute(
        "SELECT COUNT(*) FROM task_deps WHERE depends_on = ?", (task_id,)
    ).fetchone()[0]
    return tuple(_rows_to_tasks(connection, rows)), count


def _family_root_id(connection: sqlite3.Connection, task_id: str) -> str:
    row = connection.execute(
        "WITH RECURSIVE ancestors(id, parent_id) AS ("
        "SELECT id, parent_id FROM tasks WHERE id = ? UNION ALL "
        "SELECT parent.id, parent.parent_id FROM tasks parent "
        "JOIN ancestors child ON parent.id = child.parent_id) "
        "SELECT id FROM ancestors WHERE parent_id IS NULL LIMIT ?",
        (task_id, 1),
    ).fetchone()
    if row is None:
        message = f"task {task_id} has no family root"
        raise ValueError(message)
    return row[0]


def _family_outcomes(connection: sqlite3.Connection, root_id: str) -> tuple[tuple[str, int], ...]:
    rows = connection.execute(
        "WITH RECURSIVE family(id) AS ("
        "SELECT id FROM tasks WHERE id = ? UNION ALL "
        "SELECT child.id FROM tasks child JOIN family parent ON child.parent_id = parent.id) "
        "SELECT tasks.status, tasks.status_reason, COUNT(*) AS count "
        "FROM tasks JOIN family ON family.id = tasks.id GROUP BY tasks.status, tasks.status_reason",
        (root_id,),
    ).fetchall()
    counts = dict.fromkeys(_FAMILY_OUTCOME_ORDER, 0)
    for row in rows:
        outcome = (
            StatusReason.SUPERSEDED.value
            if row["status"] == Status.CANCELLED.value
            and row["status_reason"] == StatusReason.SUPERSEDED.value
            else row["status"]
        )
        counts[outcome] += row["count"]
    return tuple((outcome, counts[outcome]) for outcome in _FAMILY_OUTCOME_ORDER)


def fetch_task_relationships(
    connection: sqlite3.Connection,
    task_id: str,
    *,
    offset: int = 0,
) -> TaskRelationships | None:
    """Read one bounded task-relationship page from a consistent database snapshot."""
    if offset < 0:
        message = "task relationship offset cannot be negative"
        raise ValueError(message)
    with transaction(connection):
        task = fetch_task(connection, task_id)
        if task is None:
            return None
        replacement_task_ids: list[str] = []
        if task.status_reason is StatusReason.SUPERSEDED:
            events = fetch_latest_events(
                connection,
                1,
                kinds=[EventKind.SCOPE_CHANGE],
                task_id=task.id,
            )
            if events:
                recorded_ids = events[0].data.get("new_task_ids")
                if isinstance(recorded_ids, list):
                    replacement_task_ids = [
                        replacement_id
                        for replacement_id in cast("list[object]", recorded_ids)
                        if isinstance(replacement_id, str)
                    ]
        family_parent = (
            fetch_task(connection, task.parent_id) if task.parent_id is not None else task
        )
        if family_parent is None:
            message = f"task {task.id} has a missing parent {task.parent_id}"
            raise ValueError(message)
        family_children, family_child_count = _fetch_relationship_tasks(
            connection,
            column="parent_id",
            task_id=task.id,
            offset=offset,
        )
        dependencies, dependency_count = _fetch_dependency_tasks(connection, task.id, offset=offset)
        blocking_dependencies, blocking_dependency_count = _fetch_blocking_dependency_tasks(
            connection,
            task.id,
            offset=offset,
        )
        dependents, dependent_count = _fetch_dependent_tasks(connection, task.id, offset=offset)
        replacements = tuple(
            replacement
            for replacement in _fetch_tasks_by_ids(
                connection,
                replacement_task_ids,
                offset=offset,
            )
            if replacement.parent_id == task.id
        )
        root_id = _family_root_id(connection, task.id)
        family_root = fetch_task(connection, root_id)
        if family_root is None:
            message = f"family root {root_id} disappeared while reading {task.id}"
            raise ValueError(message)
        return TaskRelationships(
            family_parent=family_parent,
            family_root=family_root,
            family_children=family_children,
            family_child_count=family_child_count,
            dependencies=dependencies,
            dependency_count=dependency_count,
            blocking_dependencies=blocking_dependencies,
            blocking_dependency_count=blocking_dependency_count,
            dependents=dependents,
            dependent_count=dependent_count,
            replacements=replacements,
            replacement_count=len(replacement_task_ids),
            family_outcomes=_family_outcomes(connection, root_id),
            page_offset=offset,
            page_size=_RELATIONSHIP_LIMIT,
            has_more=any(
                offset + _RELATIONSHIP_LIMIT < count
                for count in (
                    family_child_count,
                    dependency_count,
                    blocking_dependency_count,
                    dependent_count,
                    len(replacement_task_ids),
                )
            ),
        )


def fetch_task_browser_pages(
    connection: sqlite3.Connection,
    *,
    category: str,
    search: str,
    limit: int,
    page_count: int,
    selected_task_id: str | None = None,
    diagnosed_stall_task_ids: Sequence[str] = (),
    quota_wait_task_ids: Sequence[str] = (),
) -> tuple[TaskBrowserPage, ...]:
    """Read a bounded page range from one SQLite snapshot."""
    if page_count < 1:
        message = "task browser page count must be positive"
        raise ValueError(message)

    with transaction(connection):
        pages: list[TaskBrowserPage] = []
        for page_index in range(page_count):
            offset = page_index * limit
            page = fetch_task_browser_page(
                connection,
                category=category,
                search=search,
                limit=limit,
                offset=offset,
                selected_task_id=selected_task_id,
                diagnosed_stall_task_ids=diagnosed_stall_task_ids,
                quota_wait_task_ids=quota_wait_task_ids,
            )
            pages.append(page)
            if offset + len(page.tasks) >= page.matching_count:
                break
    return tuple(pages)


def _task_browser_category(
    category: str,
    quota_wait_task_ids: Sequence[str],
) -> tuple[str, tuple[str, ...]]:
    if category == _BROWSER_CATEGORY_ALL:
        return "1 = 1", ()
    if category in _BROWSER_STATUS_FILTERS:
        return "t.status = ?", (category,)
    if category == _BROWSER_CATEGORY_WAITING:
        quota_wait = ""
        parameters: tuple[str, ...] = ()
        if quota_wait_task_ids:
            placeholders = ", ".join("?" for _ in quota_wait_task_ids)
            quota_wait = f" OR t.id IN ({placeholders})"
            parameters = tuple(quota_wait_task_ids)
        return (
            (
                "(t.status = ? OR t.status = ? OR "
                "(t.status IN (?, ?) AND "
                f"(json_array_length(t.holds) > 0 OR t.wait_reason IS NOT NULL{quota_wait})))"
            ),
            (
                Status.QUEUED.value,
                Status.ESCALATED.value,
                Status.RUNNING.value,
                Status.INTEGRATING.value,
                *parameters,
            ),
        )
    statuses = {
        _BROWSER_CATEGORY_ACTIVE: (Status.RUNNING, Status.INTEGRATING, Status.ESCALATED),
        _BROWSER_CATEGORY_HISTORY: (Status.DONE, Status.CANCELLED),
    }
    selected = statuses[category]
    placeholders = ", ".join("?" for _ in selected)
    return (
        f"t.status IN ({placeholders})",
        tuple(status.value for status in selected),
    )


def _task_browser_search(category_where: str, search: str) -> tuple[str, tuple[str, ...]]:
    if not search:
        return category_where, ()
    return (
        (
            f"{category_where} AND "
            "(instr(casefold(t.id), casefold(?)) > 0 OR "
            "instr(casefold(t.title), casefold(?)) > 0)"
        ),
        (search, search),
    )


def _task_browser_order(
    category: str,
    diagnosed_stall_task_ids: Sequence[str],
) -> tuple[str, tuple[str, ...]]:
    queued = Status.QUEUED.value
    running = Status.RUNNING.value
    integrating = Status.INTEGRATING.value
    escalated = Status.ESCALATED.value
    done = Status.DONE.value
    cancelled = Status.CANCELLED.value
    open_status = EscalationStatus.OPEN.value
    decision_first = (
        f"CASE WHEN t.status = '{escalated}' OR EXISTS ("
        f"SELECT 1 FROM escalations e WHERE e.task_id = t.id AND e.status = '{open_status}'"
        ") THEN 0 ELSE 1 END"
    )
    if category == _BROWSER_CATEGORY_ACTIVE:
        stall_first = ""
        if diagnosed_stall_task_ids:
            placeholders = ", ".join("?" for _ in diagnosed_stall_task_ids)
            stall_first = f"CASE WHEN t.id IN ({placeholders}) THEN 0 ELSE 1 END, "
        return (
            f"{decision_first}, {stall_first}COALESCE(t.started_at, t.created_at), t.seq",
            tuple(diagnosed_stall_task_ids),
        )
    if category == _BROWSER_CATEGORY_WAITING:
        return (
            (
                f"CASE WHEN t.status = '{queued}' THEN 0 ELSE 1 END, "
                f"CASE WHEN t.status = '{queued}' AND t.requeued_at IS NULL THEN 1 ELSE 0 END, "
                f"CASE WHEN t.status = '{queued}' THEN -t.priority ELSE 0 END, "
                f"CASE WHEN t.status = '{queued}' THEN t.seq END, "
                f"CASE WHEN t.status != '{queued}' THEN COALESCE(t.started_at, t.created_at) END, "
                "t.seq"
            ),
            (),
        )
    if category == _BROWSER_CATEGORY_HISTORY:
        return "t.finished_at DESC, t.seq DESC", ()
    return (
        (
            f"CASE WHEN t.status IN ('{done}', '{cancelled}') THEN 1 ELSE 0 END, "
            f"{decision_first}, "
            f"CASE WHEN t.status = '{queued}' THEN 1 ELSE 0 END, "
            f"CASE WHEN t.status = '{queued}' AND t.requeued_at IS NULL THEN 1 ELSE 0 END, "
            f"CASE WHEN t.status = '{queued}' THEN -t.priority ELSE 0 END, "
            f"CASE WHEN t.status = '{queued}' THEN t.seq END, "
            f"CASE WHEN t.status IN ('{running}', '{integrating}', '{escalated}') "
            "THEN COALESCE(t.started_at, t.created_at) END, "
            f"CASE WHEN t.status IN ('{done}', '{cancelled}') THEN t.finished_at END DESC, "
            "t.seq"
        ),
        (),
    )


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
                    complexity=None,
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
        complexity: ComplexityName | None,
        priority: int,
        depends_on: Sequence[str],
        overrides: dict[Role, Rung],
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO tasks (
              id, seq, parent_id, origin, title, request, mode, complexity,
              complexity_explicit, priority, status, overrides, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                seq,
                parent_id,
                origin.value,
                title,
                request,
                mode.value if mode is not None else None,
                complexity or DEFAULT_COMPLEXITY,
                complexity is not None,
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
            raise InvariantError(message)
        return task

    def list_active(self) -> list[Task]:
        """Read every task holding a slot: `running`, `integrating` or `escalated`."""
        return fetch_tasks(self._connection, statuses=ACTIVE_STATUSES)

    def list_all(self) -> list[Task]:
        """Read every task, the universe `domain.scheduling.pick_ready` needs for dep lookups."""
        return fetch_tasks(self._connection)

    def apply_transition(self, task_id: str, transition: Transition) -> None:
        """Persist `transition`'s step, status, iteration, counters, rungs and triage results.

        The decision is reconciled with the stored row in the same write
        (`Transition.rebased_onto`): only what it changed relative to its `base` is applied, and a
        rung the operator set since wins over the turn's rung move and rejection count (03 §3).
        Overrides merge the same way: the transition sets its own roles and clears a role only
        while it still holds the rung the transition saw. Complexity and title are written only
        when the transition sets them.
        """
        current = self.require(task_id)
        overrides = self._merged_overrides(current, transition)
        transition = transition.rebased_onto(current)
        now = format_timestamp(self._clock.now())
        status = transition.status.value
        self._connection.execute(
            """
            UPDATE tasks SET
              step = ?, status = ?, status_reason = ?, iteration = ?,
              counters = ?, rung_index = ?, overrides = ?,
              complexity = COALESCE(?, complexity),
              complexity_explicit = complexity_explicit OR ? IS NOT NULL,
              mode = COALESCE(?, mode), title = COALESCE(?, title),
              started_at = CASE WHEN ? = ? AND started_at IS NULL THEN ? ELSE started_at END,
              finished_at = CASE WHEN ? IN (?, ?) AND finished_at IS NULL
                THEN ? ELSE finished_at END,
              wait_reason = CASE WHEN ? IN (?, ?) THEN NULL ELSE wait_reason END
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
                transition.complexity,
                transition.mode.value if transition.mode is not None else None,
                transition.title,
                status,
                Status.RUNNING.value,
                now,
                status,
                Status.DONE.value,
                Status.CANCELLED.value,
                now,
                status,
                Status.DONE.value,
                Status.CANCELLED.value,
                task_id,
            ),
        )

    @staticmethod
    def _merged_overrides(current: Task, transition: Transition) -> dict[Role, Rung]:
        kept = {
            role: rung
            for role, rung in current.overrides.items()
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
