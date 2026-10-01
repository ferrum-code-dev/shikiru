"""Run the agent turns needed to resolve integration conflicts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.domain.recovery import TurnCounters
from shikiru.domain.reports import (
    DECISION_ACCEPT,
    STATUS_COMPLETED,
    ConsultAnswer,
    ImplementationReport,
    ReportKind,
    Verdict,
)
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import Role, Step, Task
from shikiru.engine.integration import (
    IntegrationBlocked,
    IntegrationConflict,
    IntegrationCoordinator,
    IntegrationHeld,
    IntegrationMetadata,
    OffTaskBranch,
    PullRequestClosed,
    UncommittedWork,
)
from shikiru.engine.step_runner import TurnStopped
from shikiru.errors import InvariantError
from shikiru.prompts.renderer import PromptName

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from shikiru.engine.escalation_filing import EscalationFiler
    from shikiru.engine.history import ReportHistory
    from shikiru.engine.step_runner import StepRunner
    from shikiru.store.tasks import TaskStore


class IntegrationTurns:
    """Coordinate conflict, consult and post-rebase verify turns."""

    def __init__(
        self,
        *,
        coordinator: IntegrationCoordinator,
        history: ReportHistory,
        tasks: TaskStore,
        steps: StepRunner,
        escalations: EscalationFiler,
    ) -> None:
        """Coordinate the integration protocol, running its turns through `steps`."""
        self._coordinator = coordinator
        self._history = history
        self._tasks = tasks
        self._steps = steps
        self._escalations = escalations

    async def run(self, task: Task) -> None:
        """Run integration and its conflict turns, then escalate blocked integration.

        Resolved conflicts whose post-rebase verify never ran (the flow was interrupted) are
        verified before integrating, so no resolution merges unreviewed.
        """

        async def resolve_conflicts(
            current: Task, continue_rebase: Callable[[], Awaitable[None]]
        ) -> tuple[Task, IntegrationMetadata]:
            resolved = await self._resolve_conflicts(current, continue_rebase)
            return resolved, self._history.integration_metadata(resolved)

        async def continue_rebase() -> None:
            await self._coordinator.continue_rebase(task)

        try:
            current = task
            if self._awaits_review(task):
                current = await self._resolve_conflicts(task, continue_rebase, resolved=True)
            metadata = self._history.integration_metadata(current)
            await self._coordinator.run(current, resolve_conflicts, metadata)
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

    def _awaits_review(self, task: Task) -> bool:
        latest = self._history.latest_report(task.id)
        return (
            task.step is Step.INTEGRATE
            and isinstance(latest, ImplementationReport)
            and latest.status == STATUS_COMPLETED
        )

    async def _resolve_conflicts(
        self,
        task: Task,
        continue_rebase: Callable[[], Awaitable[None]],
        *,
        resolved: bool = False,
    ) -> Task:
        current = task
        context = ""
        while True:
            if not resolved:
                current, context, resolved = await self._conflict_turn(current, context)
                continue
            await continue_rebase()
            current, verdict = await self._verify_conflicts(current)
            if verdict.decision == DECISION_ACCEPT:
                return current
            context = "; ".join(issue.description for issue in verdict.issues)
            resolved = False

    async def _conflict_turn(self, task: Task, context: str) -> tuple[Task, str, bool]:
        step = Step.CONSULT if task.step is Step.CONSULT else Step.INTEGRATE
        current, _, stopped = await self._steps.run_step(
            task, step, TurnCounters(), conflict_context=context
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
