"""Run the agent turns needed to resolve integration conflicts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.agents.contracts import role_dir
from shikiru.domain.commands import ResolutionKind
from shikiru.domain.events import EventKind
from shikiru.domain.recovery import TurnCounters
from shikiru.domain.reports import (
    CURRENT_TURN_LINK_NAME,
    DECISION_ACCEPT,
    STATUS_COMPLETED,
    ConsultAnswer,
    ImplementationReport,
    ReportKind,
    Verdict,
)
from shikiru.domain.steps import CONFLICT_ROUNDS, Transition
from shikiru.domain.tasks import EscalationReason, Role, Step, Task
from shikiru.engine.integration import (
    IntegrationBlocked,
    IntegrationConflict,
    IntegrationCoordinator,
    IntegrationHeld,
    IntegrationMetadata,
    OffTaskBranch,
    PullRequestClosed,
    RebaseConflict,
    UncommittedWork,
)
from shikiru.engine.step_runner import TurnStopped
from shikiru.errors import InvariantError
from shikiru.paths import run_dir, turns_dir
from shikiru.prompts.renderer import PromptName

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

    from shikiru.engine.escalation_filing import EscalationFiler
    from shikiru.engine.history import ReportHistory
    from shikiru.engine.step_runner import StepRunner
    from shikiru.store.events import EventLog
    from shikiru.store.tasks import TaskStore


class IntegrationTurns:
    """Coordinate conflict, consult and post-rebase verify turns."""

    def __init__(
        self,
        *,
        coordinator: IntegrationCoordinator,
        events: EventLog,
        history: ReportHistory,
        project_root: Path,
        tasks: TaskStore,
        steps: StepRunner,
        escalations: EscalationFiler,
    ) -> None:
        """Coordinate the integration protocol, running its turns through `steps`."""
        self._coordinator = coordinator
        self._events = events
        self._history = history
        self._project_root = project_root
        self._tasks = tasks
        self._steps = steps
        self._escalations = escalations

    async def run(self, task: Task) -> None:
        """Run integration and its conflict turns, then escalate blocked integration.

        Resolved conflicts whose post-rebase verify never ran (the flow was interrupted) are
        verified before integrating, so no resolution merges unreviewed.
        """

        async def resolve_conflicts(
            current: Task,
            continue_rebase: Callable[[], Awaitable[None]],
            resumed_report: bool,
        ) -> tuple[Task, IntegrationMetadata]:
            resolved = await self._resolve_conflicts(
                current, continue_rebase, resolved=resumed_report
            )
            return resolved, self._history.integration_metadata(resolved)

        try:
            metadata = self._history.integration_metadata(task)
            await self._coordinator.run(
                task, resolve_conflicts, metadata, self._resume_resolved_report
            )
        except TurnStopped, IntegrationHeld:
            return
        except (
            IntegrationBlocked,
            IntegrationConflict,
            OffTaskBranch,
            PullRequestClosed,
            UncommittedWork,
        ) as error:
            current = self._tasks.require(task.id)
            transition = Transition.escalate(current, error.reason, step=Step.INTEGRATE)
            await self._escalations.escalate(current, transition, None, question=str(error))

    def _resume_resolved_report(self, task: Task) -> bool:
        history = self._history.load(task.id)
        if task.step is not Step.INTEGRATE or not history:
            return False
        label, report = history[-1]
        if not isinstance(report, ImplementationReport) or report.status != STATUS_COMPLETED:
            return False
        events = self._events.since(
            0,
            task_id=task.id,
            kinds=(
                EventKind.TURN_STARTED,
                EventKind.TURN_COMPLETED,
                EventKind.INTEGRATION_STARTED,
                EventKind.ESCALATION,
                EventKind.ESCALATION_RESOLVED,
            ),
        )
        completed = [
            event
            for event in events
            if event.kind is EventKind.TURN_COMPLETED
            and event.data.get("turn") == label
            and event.data.get("step") == Step.INTEGRATE.value
            and event.data.get("role") == Role.IMPLEMENTER.value
            and event.data.get("outcome") == STATUS_COMPLETED
        ]
        turn_started = [
            event
            for event in events
            if event.kind is EventKind.TURN_STARTED
            and event.data.get("turn") == label
            and event.data.get("step") == Step.INTEGRATE.value
            and event.data.get("role") == Role.IMPLEMENTER.value
        ]
        if not completed and not turn_started:
            return False
        report_event = completed[-1] if completed else turn_started[-1]
        integration_starts = [
            event for event in events if event.kind is EventKind.INTEGRATION_STARTED
        ]
        latest_start = integration_starts[-1].id if integration_starts else 0
        if report_event.id > latest_start and (completed or self._current_turn_is(task, label)):
            return True
        escalations = {
            event.data.get("escalation_id"): event
            for event in events
            if event.kind is EventKind.ESCALATION
        }
        for event in reversed(events):
            if event.kind is not EventKind.ESCALATION_RESOLVED:
                continue
            escalation = escalations.get(event.data.get("escalation_id"))
            if (
                event.data.get("resolution") == ResolutionKind.RETRY.value
                and escalation is not None
                and escalation.data.get("reason") == EscalationReason.INTEGRATION_BLOCKED.value
                and report_event.id < escalation.id < event.id
            ):
                return True
        return False

    def _current_turn_is(self, task: Task, label: str) -> bool:
        current_turn = (
            role_dir(run_dir(self._project_root, task.id), Role.IMPLEMENTER.value)
            / CURRENT_TURN_LINK_NAME
        )
        report_turn = turns_dir(self._project_root, task.id) / label
        return current_turn.is_symlink() and current_turn.resolve() == report_turn.resolve()

    async def _resolve_conflicts(
        self,
        task: Task,
        continue_rebase: Callable[[], Awaitable[None]],
        *,
        resolved: bool = False,
    ) -> Task:
        current = task
        context = ""
        conflict_turns = int(resolved)
        while True:
            if not resolved:
                if conflict_turns >= CONFLICT_ROUNDS:
                    raise IntegrationConflict.unresolved_rebase(current.id, conflict_turns, context)
                current, context, resolved = await self._conflict_turn(current, context)
                if resolved:
                    conflict_turns += 1
                continue
            try:
                await continue_rebase()
            except RebaseConflict as error:
                context = str(error)
                resolved = False
                continue
            current, verdict = await self._verify_conflicts(current)
            if verdict.decision == DECISION_ACCEPT:
                return current
            context = "; ".join(issue.description for issue in verdict.issues)
            conflict_turns = 0
            resolved = False

    async def _conflict_turn(self, task: Task, context: str) -> tuple[Task, str, bool]:
        step = Step.CONSULT if task.step is Step.CONSULT else Step.INTEGRATE
        current, _, stopped = await self._steps.run_step(
            task,
            step,
            TurnCounters(),
            conflict_context=context,
            force_new_turn=step is Step.INTEGRATE,
        )
        if stopped:
            raise TurnStopped
        latest = self._history.latest_report(current.id)
        if step is Step.CONSULT:
            if not isinstance(latest, ConsultAnswer):
                message = f"{current.id} integration consult produced no answer"
                raise InvariantError(message)
            return current, latest.answer, False
        if not isinstance(latest, ImplementationReport):
            message = f"{current.id} conflicts turn produced no implementation report"
            raise InvariantError(message)
        if latest.status == STATUS_COMPLETED:
            return current, context, True
        if current.step is not Step.CONSULT:
            message = f"{current.id} conflict report did not route to consult"
            raise InvariantError(message)
        return current, context, False

    async def _verify_conflicts(self, task: Task) -> tuple[Task, Verdict]:
        current, outcome = await self._steps.run_report_turn(
            task,
            role=Role.LEADER,
            template=PromptName.VERIFY,
            kind=ReportKind.VERDICT,
        )
        if not isinstance(outcome, Verdict):
            message = f"{task.id} post-rebase turn produced no verdict"
            raise InvariantError(message)
        updated, escalated = await self._steps.advance(current, outcome, role=Role.LEADER)
        if escalated:
            raise TurnStopped
        return updated, outcome
