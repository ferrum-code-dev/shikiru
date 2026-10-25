"""What an E2E attempt looked like when it failed, and whether a rerun is fair."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from shikiru.agents.sdk.turn import STREAM_FILENAME_SUFFIX
from shikiru.domain.events import Event, EventKind
from shikiru.engine.turn_processes import TURN_PID_NAME, read_turn_pid
from shikiru.errors import ShikiruError
from shikiru.paths import (
    RUNS_DIR_NAME,
    TURNS_DIR_NAME,
    engine_log_path,
    project_state_dir,
    state_db_path,
)
from shikiru.store.database import Database
from shikiru.store.events import EventLog
from shikiru.store.tasks import TaskStore

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.clock import Clock
    from shikiru.domain.tasks import Task
    from shikiru.process import SubprocessRunner

GIT_PROBE_TIMEOUT_SECONDS = 30.0
EVENTS_SHOWN = 20
ENGINE_LOG_TAIL_LINES = 40
RERUN_OUTSIDE_WORKSPACE = "an agent wrote in the operator checkout"
RERUN_SKIPPED_RULE = "an agent skipped a rule"
SANDBOX_DENIAL_MARKERS = ("Operation not permitted", "was denied permission", "Permission denied")
SANDBOX_DENIAL_NOTE = (
    "sandbox denial ({marker!r}) in an agent transcript: not agent flakiness and never rerun; "
    "check the Codex sandbox writable roots (run directory, worktree git directory, "
    "git common directory) and `shikiru doctor`"
)


class ShikiruFailure(AssertionError):
    """A failure of Shikiru or its environment rather than of an agent's work."""


@dataclass(frozen=True, slots=True)
class Snapshot:
    """The observable state of a project under test at one moment."""

    events: tuple[Event, ...]
    tasks: tuple[Task, ...]
    checkout_status: str
    engine_log_tail: str
    turn_alive: bool
    sandbox_denial: str | None = None

    @property
    def last_event_id(self) -> int:
        """Return the id of the newest event, or zero when none was recorded."""
        return self.events[-1].id if self.events else 0

    def has_event(self, kind: EventKind) -> bool:
        """Return whether an event of `kind` was recorded."""
        return any(event.kind is kind for event in self.events)

    def render(self, events_shown: int = EVENTS_SHOWN) -> str:
        """Return the last event, every task's state, the checkout status and the log tail."""
        last = self.events[-1] if self.events else None
        shown = self.events[-events_shown:]
        lines = [f"last event: {_event_line(last) if last else 'none'}", "recent events:"]
        lines += [f"  {_event_line(event)}" for event in shown]
        lines.append("tasks:")
        lines += [f"  {_task_line(task)}" for task in self.tasks]
        lines.append(f"turn process alive: {self.turn_alive}")
        lines.append(f"operator checkout git status:\n{self.checkout_status.strip() or '  clean'}")
        lines.append(f"engine.log tail:\n{self.engine_log_tail}")
        return "\n".join(lines)


def _event_line(event: Event) -> str:
    return f"#{event.id} {event.kind.value} {event.task_id or '-'}: {event.message}"


def _task_line(task: Task) -> str:
    holds = sorted(hold.value for hold in task.holds)
    step = task.step.value if task.step else None
    return (
        f"{task.id} status={task.status.value} step={step} holds={holds} "
        f"wait_reason={task.wait_reason}"
    )


async def take_snapshot(project_root: Path, runner: SubprocessRunner, clock: Clock) -> Snapshot:
    """Read the events, task states, turn processes, checkout status and log of `project_root`."""
    events: tuple[Event, ...] = ()
    tasks: tuple[Task, ...] = ()
    database = state_db_path(project_root)
    if database.is_file():
        connection = Database(database).connect_readonly()
        try:
            events = tuple(EventLog(connection, clock).since(0))
            tasks = tuple(TaskStore(connection, clock).list_all())
        finally:
            connection.close()
    status = await runner.run(
        ["git", "status", "--porcelain"],
        timeout=GIT_PROBE_TIMEOUT_SECONDS,
        cwd=project_root,
        check=False,
    )
    return Snapshot(
        events=events,
        tasks=tasks,
        checkout_status=status.stdout or status.stderr,
        engine_log_tail=_tail(project_root),
        turn_alive=_turn_alive(project_root, runner),
        sandbox_denial=_sandbox_denial(project_root),
    )


def _sandbox_denial(project_root: Path) -> str | None:
    runs_root = project_state_dir(project_root) / RUNS_DIR_NAME
    for stream in sorted(runs_root.glob(f"*/{TURNS_DIR_NAME}/*/*{STREAM_FILENAME_SUFFIX}")):
        text = stream.read_text(errors="replace")
        for marker in SANDBOX_DENIAL_MARKERS:
            if marker in text:
                return marker
    return None


def _tail(project_root: Path) -> str:
    log = engine_log_path(project_root)
    if not log.is_file():
        return "  (no engine log)"
    return "\n".join(log.read_text().splitlines()[-ENGINE_LOG_TAIL_LINES:])


def _turn_alive(project_root: Path, runner: SubprocessRunner) -> bool:
    runs_root = project_state_dir(project_root) / RUNS_DIR_NAME
    for path in runs_root.glob(f"*/*/{TURN_PID_NAME}"):
        try:
            identity = read_turn_pid(path)
        except FileNotFoundError:
            continue
        if runner.is_running(identity):
            return True
    return False


def _is_environment_failure(error: Exception) -> bool:
    if isinstance(error, TimeoutError):
        return False
    return isinstance(error, ShikiruFailure | ShikiruError | OSError)


def write_diagnostics(path: Path, error: Exception, snapshot: Snapshot) -> None:
    """Write `error` with every recorded event and the rest of `snapshot` to `path`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{type(error).__name__}: {error}\n\n{snapshot.render(len(snapshot.events))}\n")


def rerun_reason(error: Exception, snapshot: Snapshot) -> str | None:
    """Return why a failed attempt is agent flakiness that a rerun may fix, else `None`.

    Only two classes qualify, and only when the engine recorded no `engine_fault` and no agent
    transcript shows a sandbox denial, which is a configuration fault, never flakiness: an agent
    wrote into the operator checkout (a `integration_waiting` event, or a dirty checkout), or
    it skipped a rule so the delivered work is wrong (a plain assertion on the outcome). Any
    Shikiru, lifecycle, CLI or environment failure never reruns.
    """
    if (
        snapshot.has_event(EventKind.ENGINE_FAULT)
        or snapshot.sandbox_denial is not None
        or _is_environment_failure(error)
    ):
        return None
    if snapshot.has_event(EventKind.INTEGRATION_WAITING) or snapshot.checkout_status.strip():
        return RERUN_OUTSIDE_WORKSPACE
    if type(error) is AssertionError:
        return RERUN_SKIPPED_RULE
    return None
