"""Builds one prompt directory and runs its resumable headless turns (03 §4.3)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, TypedDict

from shikiru.agents.contracts import (
    PROJECT_ENV_VAR,
    ROLE_ENV_VAR,
    RUN_DIR_ENV_VAR,
    SLOT_ENV_VAR,
    TASK_ENV_VAR,
    FailureKind,
    TurnFailed,
    role_dir,
)
from shikiru.agents.contracts import (
    TurnSpec as AgentTurnSpec,
)
from shikiru.domain.events import EventKind
from shikiru.domain.recovery import Completed, Failed, ProtocolError, Stalled, TurnResult
from shikiru.domain.reports import (
    CURRENT_TURN_LINK_NAME,
    FACTS_FILENAME,
    HELPER_REPORT_FILENAME,
    PROMPT_FILENAME,
    REPORT_FILENAME,
    REPORT_KIND_FILENAME,
    REPORT_SCHEMA_FILENAME,
    schema_for,
)
from shikiru.errors import ShikiruError
from shikiru.helper.helper import FailureClassification
from shikiru.helper.models import Unrecoverable
from shikiru.paths import run_dir, task_root, turns_dir
from shikiru.prompts.renderer import OPERATOR_MESSAGES_KEY
from shikiru.view_log import TURN_DIR_ITERATION_MARKER, TURN_DIR_SEPARATOR
from shikiru.workspace.reports import InvalidTurnReportError, load_turn_report

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

    from shikiru.config.settings import Settings
    from shikiru.domain.reports import Report, ReportKind
    from shikiru.domain.tasks import Role, Step, Task
    from shikiru.engine.agents import AgentPool
    from shikiru.engine.monitor import TurnMonitor
    from shikiru.engine.sessions import SessionManager, SessionSelection
    from shikiru.helper.helper import Helper
    from shikiru.prompts.renderer import PromptName, PromptRenderer
    from shikiru.store.events import EventLog
    from shikiru.store.runs import RunStore
    from shikiru.workspace.messages import OperatorMessages

NUDGE_LIMIT = 2
CLASSIFY_CONFIDENCE = 0.8
_TURN_NUMBER_WIDTH = 2
TURN_PROMPT = "Read {prompt_path} and follow it exactly."
_NUDGE_MESSAGE = (
    "Your turn ended without a valid report. Submit it with `shikiru report` as described "
    "at the end of {prompt_path}."
)
_LOGGER = logging.getLogger(__name__)


class _HelperCall(TypedDict):
    model: str
    effort: str | None
    cwd: Path
    timeout: timedelta


@dataclass(frozen=True)
class TurnRequest:
    """Prompt details for one workflow step."""

    step: Step
    iteration: int
    kind: ReportKind
    template: PromptName
    context: Mapping[str, object]
    facts: str


def read_turn_report(turn_dir: Path) -> Report | None:
    """Read `turn_dir`'s submitted report or Helper rebuild; invalid files raise explicitly."""
    loaded = load_turn_report(turn_dir)
    return loaded.report if loaded is not None else None


def turn_environment(
    task: Task, role: Role, run_dir_path: Path, project_name: str
) -> dict[str, str]:
    """Return the environment every agent process of `task`'s `role` runs with (03 §4.1)."""
    if task.slot is None:
        message = f"{task.id} has no assigned slot; a turn needs a dispatched task"
        raise ShikiruError(message)
    return {
        RUN_DIR_ENV_VAR: str(run_dir_path),
        ROLE_ENV_VAR: role.value,
        PROJECT_ENV_VAR: project_name,
        TASK_ENV_VAR: task.id,
        SLOT_ENV_VAR: str(task.slot),
    }


class TurnRunner:
    """Create turn files, run a process per attempt, and resolve reports and failures."""

    def __init__(
        self,
        *,
        agents: AgentPool,
        sessions: SessionManager,
        monitor: TurnMonitor,
        helper: Helper,
        renderer: PromptRenderer,
        messages: OperatorMessages,
        events: EventLog,
        runs: RunStore,
        project_root: Path,
        settings: Settings,
    ) -> None:
        self._agents = agents
        self._sessions = sessions
        self._monitor = monitor
        self._helper = helper
        self._renderer = renderer
        self._messages = messages
        self._events = events
        self._runs = runs
        self._project_root = project_root
        self._settings = settings

    async def run(
        self,
        task: Task,
        role: Role,
        selection: SessionSelection,
        request: TurnRequest,
        *,
        on_nudge: Callable[[], None],
    ) -> tuple[TurnResult, Path, SessionSelection]:
        """Run one workflow turn and any resumed nudge turns to a normalized result.

        Operator messages delivered into the turn return to the queue unless it completes.
        """
        turn_dir = self._prepare_turn_dir(
            run_dir(self._project_root, task.id), task.id, role, request
        )
        completed = False
        try:
            result, selection = await self._attempts(
                task, role, selection, request, turn_dir, on_nudge=on_nudge
            )
            completed = isinstance(result, Completed)
        finally:
            if not completed:
                self._messages.requeue(task.id, role, turn_dir)
        return result, turn_dir, selection

    async def _attempts(
        self,
        task: Task,
        role: Role,
        selection: SessionSelection,
        request: TurnRequest,
        turn_dir: Path,
        *,
        on_nudge: Callable[[], None],
    ) -> tuple[TurnResult, SessionSelection]:
        task_run_dir = run_dir(self._project_root, task.id)
        task_root_dir = task_root(self._project_root, task.id)
        prompt_path = turn_dir / PROMPT_FILENAME
        prompt = TURN_PROMPT.format(prompt_path=prompt_path)
        nudges = 0
        last_message = ""
        while True:
            agent = self._agents.get(selection.rung.agent)
            turn_spec = AgentTurnSpec(
                rung=selection.rung,
                role=role.value,
                prompt=prompt,
                cwd=task_root_dir,
                run_dir=task_run_dir,
                turn_dir=turn_dir,
                add_dirs=(),
                env=turn_environment(task, role, task_run_dir, self._settings.project.name),
                session_id=selection.session_id,
                usage=selection.run.usage,
            )
            turn = await agent.start_turn(turn_spec)
            terminal = await self._monitor.run(
                turn,
                agent=agent,
                task=task,
                role=role,
                rung=selection.rung,
                run_id=selection.run.id,
                turn_dir=turn_dir,
                run_dir=task_run_dir,
            )
            if isinstance(terminal, TurnFailed):
                return await self._classify_unknown(terminal, task, selection), selection
            if isinstance(terminal, Stalled | ProtocolError):
                return terminal, selection
            last_message = terminal.last_message
            try:
                report = read_turn_report(turn_dir)
            except InvalidTurnReportError:
                _LOGGER.exception("invalid report in completed turn %s", turn_dir)
                report = None
            if report is not None:
                return Completed(report=report), selection
            if nudges >= NUDGE_LIMIT:
                rebuilt = await self._rebuild(task, selection, turn_dir, request, last_message)
                return rebuilt, selection
            nudges += 1
            on_nudge()
            prompt = _NUDGE_MESSAGE.format(prompt_path=prompt_path)
            selection = self._sessions.session_for(task, role)

    def completed_turn(
        self, task_id: str, role: Role, request: TurnRequest
    ) -> tuple[Completed, Path] | None:
        """Return the report of `request`'s latest turn when it holds a valid one (03 §7).

        A restarted engine re-runs the step whose turn it lost. When that turn is the task's
        latest, still `role`'s current turn, and already submitted a valid report, the report is
        consumed as is instead of starting another turn.
        """
        turns_root = turns_dir(self._project_root, task_id)
        if not turns_root.is_dir():
            return None
        turn_dir = turns_root / _turn_dir_name(_turn_count(turns_root), request)
        link = role_dir(run_dir(self._project_root, task_id), role.value) / CURRENT_TURN_LINK_NAME
        if not link.is_symlink() or link.readlink() != turn_dir:
            return None
        try:
            report = read_turn_report(turn_dir)
        except InvalidTurnReportError:
            _LOGGER.exception("invalid report in interrupted turn %s", turn_dir)
            return None
        if report is None:
            return None
        return Completed(report=report), turn_dir

    def _prepare_turn_dir(
        self, task_run_dir: Path, task_id: str, role: Role, request: TurnRequest
    ) -> Path:
        turns_root = turns_dir(self._project_root, task_id)
        turns_root.mkdir(parents=True, exist_ok=True)
        turn_dir = turns_root / _turn_dir_name(_turn_count(turns_root) + 1, request)
        turn_dir.mkdir(parents=True, exist_ok=True)
        (turn_dir / REPORT_FILENAME).unlink(missing_ok=True)
        (turn_dir / HELPER_REPORT_FILENAME).unlink(missing_ok=True)
        delivered = self._messages.deliver(task_id, role, turn_dir)
        context = {**request.context, OPERATOR_MESSAGES_KEY: [str(path) for path in delivered]}
        (turn_dir / PROMPT_FILENAME).write_text(self._renderer.render(request.template, context))
        (turn_dir / REPORT_SCHEMA_FILENAME).write_text(
            json.dumps(schema_for(request.kind), indent=2)
        )
        (turn_dir / REPORT_KIND_FILENAME).write_text(request.kind.value)
        if request.facts:
            (turn_dir / FACTS_FILENAME).write_text(request.facts)
        self._point_current_turn(task_run_dir, role, turn_dir)
        return turn_dir

    @staticmethod
    def _point_current_turn(task_run_dir: Path, role: Role, turn_dir: Path) -> None:
        link = role_dir(task_run_dir, role.value) / CURRENT_TURN_LINK_NAME
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(turn_dir)

    def _helper_call(self, task: Task) -> _HelperCall:
        rung = self._settings.roles.helper.ladder[0]
        return _HelperCall(
            model=rung.model,
            effort=rung.effort,
            cwd=task_root(self._project_root, task.id),
            timeout=timedelta(seconds=self._settings.orchestration.timeouts.helper),
        )

    async def _classify_unknown(
        self, failure: TurnFailed, task: Task, selection: SessionSelection
    ) -> Failed:
        if failure.kind is not FailureKind.UNKNOWN:
            return Failed(failure.kind, failure.resets_at, failure.message)
        try:
            classification = await self._helper.classify_failure(
                failure.message, **self._helper_call(task)
            )
        except (ShikiruError, TimeoutError, OSError) as error:
            _LOGGER.exception("failure classification failed for %s", task.id)
            self._events.append(
                EventKind.HELPER_ACTION,
                task_id=task.id,
                message=f"{task.id} failure classification failed",
                data={"role": selection.run.role.value, "error": str(error)},
            )
            return Failed(FailureKind.OTHER, None, failure.message, unclassified=True)
        if isinstance(classification, FailureClassification):
            accepted = (
                classification.confidence >= CLASSIFY_CONFIDENCE
                and classification.kind is not FailureKind.UNKNOWN
            )
            self._events.append(
                EventKind.HELPER_ACTION,
                task_id=task.id,
                message=f"{task.id} failure classified as {classification.kind.value}",
                data={
                    "role": selection.run.role.value,
                    "kind": classification.kind.value,
                    "confidence": classification.confidence,
                    "accepted": accepted,
                },
            )
            if accepted:
                return Failed(classification.kind, classification.resets_at, failure.message)
        else:
            self._events.append(
                EventKind.HELPER_ACTION,
                task_id=task.id,
                message=f"{task.id} failure could not be classified",
                data={"role": selection.run.role.value, "reason": classification.unrecoverable},
            )
        return Failed(FailureKind.OTHER, None, failure.message, unclassified=True)

    async def _rebuild(
        self,
        task: Task,
        selection: SessionSelection,
        turn_dir: Path,
        request: TurnRequest,
        last_message: str,
    ) -> TurnResult:
        try:
            report = await self._helper.rebuild_report(
                request.kind, last_message, request.facts, **self._helper_call(task)
            )
        except (ShikiruError, TimeoutError, OSError) as error:
            _LOGGER.exception("report rebuild failed for %s", task.id)
            self._events.append(
                EventKind.HELPER_ACTION,
                task_id=task.id,
                message=f"{task.id} report rebuild failed",
                data={"role": selection.run.role.value, "error": str(error)},
            )
            return ProtocolError()
        if isinstance(report, Unrecoverable):
            self._events.append(
                EventKind.HELPER_ACTION,
                task_id=task.id,
                message=f"{task.id} report could not be rebuilt",
                data={"role": selection.run.role.value, "reason": report.unrecoverable},
            )
            return ProtocolError()
        (turn_dir / HELPER_REPORT_FILENAME).write_text(report.model_dump_json(indent=2))
        return Completed(report=report, rebuilt=True)


def _turn_count(turns_root: Path) -> int:
    return sum(1 for entry in turns_root.iterdir() if entry.is_dir())


def _turn_dir_name(number: int, request: TurnRequest) -> str:
    return (
        f"{number:0{_TURN_NUMBER_WIDTH}d}{TURN_DIR_SEPARATOR}{request.step.value}"
        f"{TURN_DIR_ITERATION_MARKER}{request.iteration}"
    )
