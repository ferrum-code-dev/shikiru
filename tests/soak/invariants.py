"""The five invariants of Phase 7d, read from durable state, git and the process table."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from fake_agent.scripted import task_change_filename

from shikiru.domain.commands import CommandStatus
from shikiru.domain.events import EventKind
from shikiru.domain.faults import ERROR_KEY, RECOVERABLE_KEY, SCOPE_KEY
from shikiru.domain.tasks import Hold, Status, Step, Task
from shikiru.process import SubprocessRunner
from tests.soak import processes
from tests.soak.model import IntegrationMode, Violation

if TYPE_CHECKING:
    from collections.abc import Hashable
    from datetime import datetime

    from shikiru.domain.events import Event
    from tests.soak.arena import Arena

RUNNER = SubprocessRunner()
GIT_TIMEOUT_SECONDS = 30.0
SETTLE_SECONDS = 5.0
COMMAND_SETTLE_SECONDS = 30.0
NOTIFY_SETTLE_SECONDS = 45.0
SCAN_INTERVAL = timedelta(seconds=2)
HELD_SAMPLES = 2
BASE_BRANCH = "main"
TERMINAL = (Status.DONE, Status.CANCELLED)
URGENT_EVENTS = (EventKind.ESCALATION, EventKind.ENGINE_FAULT)
COMMITTING_STEPS = (Step.IMPLEMENT.value, Step.PLAN.value)
COMPLETED = "completed"


class Persistence:
    """Confirms a state-based finding only once it has been observed continuously."""

    def __init__(self, settle_seconds: float) -> None:
        self._settle = timedelta(seconds=settle_seconds)
        self._first: dict[Hashable, datetime] = {}

    def confirmed(self, observed: dict[Hashable, Violation], now: datetime) -> list[Violation]:
        self._first = {key: self._first.get(key, now) for key in observed}
        return [v for key, v in observed.items() if now - self._first[key] >= self._settle]

    def reset(self) -> None:
        self._first.clear()


@dataclass(slots=True)
class Ledger:
    """What the driver issued, for the checks that compare it with what the engine did."""

    commands: dict[int, str] = field(default_factory=dict)
    kinds: dict[str, str] = field(default_factory=dict)


class InvariantChecker:
    def __init__(self, arena: Arena, ledger: Ledger) -> None:
        self._arena = arena
        self._ledger = ledger
        self._structure = Persistence(SETTLE_SECONDS)
        self._commands = Persistence(COMMAND_SETTLE_SECONDS)
        self._notices = Persistence(NOTIFY_SETTLE_SECONDS)
        self._seen_turns: set[tuple[int, float | None]] = set()
        self._held_samples: dict[str, int] = {}
        self._last_scan: datetime | None = None

    def sample(self, now: datetime, *, stable: bool) -> list[Violation]:
        """Invariants 1, 2 and 5 over the current state; the last waits for a stable engine."""
        reader = self._arena.reader
        assert reader is not None
        tasks = reader.list_tasks()
        found = self._held_turns(tasks)
        observed = self._two_owners(tasks, now)
        if stable:
            observed |= self._escalations(tasks)
            found += self._commands.confirmed(self._pending_commands(), now)
            found += self._notices.confirmed(self._undelivered(), now)
        else:
            self._commands.reset()
            self._notices.reset()
        return found + self._structure.confirmed(observed, now)

    def _held_turns(self, tasks: list[Task]) -> list[Violation]:
        held_before = dict(self._held_samples)
        self._held_samples = {
            task.id: held_before.get(task.id, 0) + 1 if task.holds else 0 for task in tasks
        }
        found: list[Violation] = []
        for turn in self._arena.turn_processes():
            key = (turn.identity.pid, turn.identity.started_at)
            if key in self._seen_turns:
                continue
            self._seen_turns.add(key)
            if (
                held_before.get(turn.task_id, 0) >= HELD_SAMPLES
                and self._held_samples[turn.task_id]
            ):
                found.append(
                    Violation(
                        2,
                        turn.task_id,
                        f"a {turn.role} turn (pid {turn.identity.pid}) started in a held task",
                    )
                )
        return found

    def _two_owners(self, tasks: list[Task], now: datetime) -> dict[Hashable, Violation]:
        observed: dict[Hashable, Violation] = {}
        live = self._arena.turn_processes()
        scan = self._last_scan is None or now - self._last_scan >= SCAN_INTERVAL
        for task in tasks:
            if task.status in TERMINAL:
                continue
            turns = [turn for turn in live if turn.task_id == task.id]
            if len({turn.identity.pid for turn in turns}) > 1:
                observed[1, task.id, "turns"] = Violation(
                    1, task.id, f"{len(turns)} live recorded turn processes"
                )
            if Hold.INTERVENTION in task.holds and turns:
                observed[1, task.id, "takeover"] = Violation(
                    1, task.id, "a headless turn runs beside a taken-over session"
                )
            if scan and self._arena.worktree(task.id).exists():
                groups = {
                    processes.group_of(p)
                    for p in processes.agents_in(self._arena.worktree(task.id))
                }
                if len(groups - {None}) > 1:
                    observed[1, task.id, "scan"] = Violation(
                        1, task.id, f"agent processes of {len(groups)} groups share the worktree"
                    )
        if scan:
            self._last_scan = now
        return observed

    def _pending_commands(self) -> dict[Hashable, Violation]:
        return {
            ("command", queued.id): Violation(
                5, None, f"command {queued.id} ({queued.command.kind.value}) stays pending"
            )
            for queued in self._arena.commands().pending()
        }

    def _escalations(self, tasks: list[Task]) -> dict[Hashable, Violation]:
        reader = self._arena.reader
        assert reader is not None
        open_ones = reader.list_open_escalations()
        by_task = {escalation.task_id for escalation in open_ones}
        observed: dict[Hashable, Violation] = {}
        for task in tasks:
            if task.status is Status.ESCALATED and task.id not in by_task:
                observed[5, task.id, "row"] = Violation(
                    5, task.id, "escalated without an open escalation"
                )
        statuses = {task.id: task.status for task in tasks}
        for escalation in open_ones:
            if statuses.get(escalation.task_id) is not Status.ESCALATED:
                observed[5, escalation.id, "block"] = Violation(
                    5,
                    escalation.task_id,
                    f"{escalation.id} is open but the task is {statuses.get(escalation.task_id)}",
                )
            if not Path(escalation.packet_path).exists():
                observed[5, escalation.id, "packet"] = Violation(
                    5, escalation.task_id, f"{escalation.id} has no packet file"
                )
        return observed

    def _undelivered(self) -> dict[Hashable, Violation]:
        reader = self._arena.reader
        assert reader is not None
        notified = self._arena.notified()
        return {
            ("notice", event.id): Violation(
                5, event.task_id, f"{event.kind.value} event {event.id} never reached the hook"
            )
            for event in reader.events_since(0, kinds=URGENT_EVENTS)
            if event.id not in notified
        }

    async def final(self, *, terminal_by: str) -> list[Violation]:
        """Invariants 3 and 4, the command ledger, the engine's own faults and the end state."""
        reader = self._arena.reader
        assert reader is not None
        tasks = {task.id: task for task in reader.list_tasks()}
        events = reader.events_since(0)
        found = self._engine_faults(events)
        found += self._commands_resolved()
        found += self._not_terminal(tasks, terminal_by)
        for task in tasks.values():
            found += await self._integrated(task, events)
            found += await self._dependencies(task, tasks)
        return found

    def _engine_faults(self, events: list[Event]) -> list[Violation]:
        found: list[Violation] = []
        for event in events:
            if event.kind is EventKind.ENGINE_STOPPED and "reason" in event.data:
                found.append(Violation(0, None, f"the engine stopped on a fault: {event.data}"))
            if event.kind is EventKind.ENGINE_FAULT and not event.data.get(RECOVERABLE_KEY, True):
                found.append(
                    Violation(
                        0,
                        event.task_id,
                        f"unexpected engine fault in {event.data.get(SCOPE_KEY)}: "
                        f"{event.data.get(ERROR_KEY)}: {event.message}",
                    )
                )
        return found

    def _commands_resolved(self) -> list[Violation]:
        found: list[Violation] = []
        queue = self._arena.commands()
        for command_id, kind in self._ledger.commands.items():
            outcome = queue.status(command_id)
            if outcome is None:
                found.append(Violation(5, None, f"command {command_id} ({kind}) is gone"))
            elif outcome.status is CommandStatus.PENDING:
                found.append(Violation(5, None, f"command {command_id} ({kind}) is still pending"))
            elif outcome.status is CommandStatus.REJECTED and not (outcome.result or {}).get(
                "error"
            ):
                found.append(
                    Violation(
                        5, None, f"command {command_id} ({kind}) was rejected without a reason"
                    )
                )
        return found

    def _not_terminal(self, tasks: dict[str, Task], terminal_by: str) -> list[Violation]:
        reader = self._arena.reader
        assert reader is not None
        open_ids = {escalation.task_id for escalation in reader.list_open_escalations()}

        def explained(task: Task) -> bool:
            if task.status is Status.ESCALATED:
                return task.id in open_ids
            return task.status is Status.QUEUED and any(
                explained(tasks[dep])
                for dep in task.depends_on
                if dep in tasks and tasks[dep].status not in TERMINAL
            )

        return [
            Violation(
                5,
                task.id,
                f"still {task.status.value} {terminal_by} (step {task.step}, "
                f"holds {sorted(task.holds)}, wait {task.wait_reason})",
            )
            for task in tasks.values()
            if task.status not in TERMINAL and not explained(task)
        ]

    async def _is_ancestor(self, sha: str, repository: Path, ref: str) -> bool:
        result = await RUNNER.run(
            ["git", "merge-base", "--is-ancestor", sha, ref],
            timeout=GIT_TIMEOUT_SECONDS,
            cwd=repository,
        )
        return result.returncode == 0

    async def _has_file(self, repository: Path, tree: str, name: str) -> bool:
        result = await RUNNER.run(
            ["git", "cat-file", "-e", f"{tree}:{name}"], timeout=GIT_TIMEOUT_SECONDS, cwd=repository
        )
        return result.returncode == 0

    @property
    def _landing(self) -> Path:
        return (
            self._arena.remote
            if self._arena.integration is IntegrationMode.PR
            else self._arena.root
        )

    async def _integrated(self, task: Task, events: list[Event]) -> list[Violation]:
        name = task_change_filename(task.id)
        if task.status is Status.DONE:
            return await self._done_work(task, name, events)
        if task.status is Status.CANCELLED and task.repo is not None:
            committed = any(
                e.kind is EventKind.TURN_COMPLETED
                and e.task_id == task.id
                and e.data.get("step") in COMMITTING_STEPS
                and e.data.get("outcome") == COMPLETED
                for e in events
            )
            kept = (
                await RUNNER.run(
                    ["git", "rev-parse", "--verify", f"refs/heads/{task.repo.branch}"],
                    timeout=GIT_TIMEOUT_SECONDS,
                    cwd=self._arena.root,
                )
            ).returncode == 0
            if (
                committed
                and not kept
                and not await self._has_file(self._landing, BASE_BRANCH, name)
            ):
                return [
                    Violation(
                        3,
                        task.id,
                        f"cancelled with committed work, but branch {task.repo.branch} is gone "
                        f"and the work is not on {BASE_BRANCH}",
                    )
                ]
        return []

    async def _done_work(self, task: Task, name: str, events: list[Event]) -> list[Violation]:
        found: list[Violation] = []
        if not await self._has_file(self._landing, BASE_BRANCH, name):
            found.append(Violation(3, task.id, f"done, but {name} is not on {BASE_BRANCH}"))
        if self._arena.integration is IntegrationMode.MERGE:
            done = [e for e in events if e.kind is EventKind.TASK_DONE and e.task_id == task.id]
            for sha in _commits(done[-1]) if done else []:
                if not await self._is_ancestor(sha, self._arena.root, BASE_BRANCH):
                    message = f"done, but commit {sha[:10]} is not in {BASE_BRANCH}"
                    found.append(Violation(3, task.id, message))
        return found

    async def _dependencies(self, task: Task, tasks: dict[str, Task]) -> list[Violation]:
        if task.repo is None:
            return []
        found: list[Violation] = []
        for dependency in task.depends_on:
            if tasks.get(dependency) is not None and tasks[dependency].status is Status.DONE:
                name = task_change_filename(dependency)
                if not await self._has_file(self._arena.root, task.repo.base_sha, name):
                    found.append(
                        Violation(
                            4,
                            task.id,
                            f"its base {task.repo.base_sha[:10]} lacks the done {dependency}",
                        )
                    )
        return found

    async def at_rest(self) -> list[Violation]:
        """After the engine stopped: nothing of the arena runs, and nothing was left behind."""
        found = [
            Violation(
                5, None, f"a live process outlived the engine: {process.pid} {process.name()}"
            )
            for process in processes.owned_by(self._arena.directory)
        ]
        if not await self._arena.tmux_server_is_gone():
            found.append(Violation(5, None, "the tmux server on the private socket still runs"))
        leaked = sorted(path.name for path in self._arena.tmpdir.iterdir())
        if leaked:
            found.append(Violation(5, None, f"temporary files were left behind: {leaked[:5]}"))
        return found


def _commits(event: Event) -> list[str]:
    return [str(entry).split()[0] for entry in event.data.get("commits", [])]
