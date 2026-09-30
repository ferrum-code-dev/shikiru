"""Run the agent turns needed to resolve integration conflicts."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from shikiru.domain.recovery import TurnCounters
from shikiru.domain.reports import ConsultAnswer, ImplementationReport, ReportKind, Verdict
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import Role, Step, Task
from shikiru.engine.integration import (
    IntegrationBlocked,
    IntegrationConflict,
    IntegrationCoordinator,
    IntegrationHeld,
    IntegrationMetadata,
    PullRequestClosed,
    UncommittedWork,
)
from shikiru.errors import ShikiruError
from shikiru.prompts.renderer import PromptName

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from shikiru.domain.recovery import TurnResult
    from shikiru.domain.steps import Outcome
    from shikiru.engine.history import ReportHistory
    from shikiru.store.tasks import TaskStore


class IntegrationTurnStopped(Exception):
    """Stop an integration callback after its task escalates or pauses."""


class IntegrationTurnHost(Protocol):
    """TaskFlow callbacks used while an integration conflict is being resolved."""

    async def run_step(
        self,
        task: Task,
        step: Step,
        counters: TurnCounters,
        *,
        conflict_context: str | None = None,
    ) -> tuple[Task, TurnCounters, bool]:
        """Run one step through TaskFlow's shared turn and recovery loop."""
        ...

    async def run_report_turn(
        self,
        task: Task,
        *,
        role: Role,
        template: PromptName,
        kind: ReportKind,
    ) -> tuple[Task, ImplementationReport | Verdict]:
        """Run an integration report through TaskFlow's shared turn loop."""
        ...

    async def advance(
        self, task: Task, outcome: Outcome, *, role: Role | None = None
    ) -> tuple[Task, bool]:
        """Apply the transition a report earns, escalating when it says so."""
        ...

    async def escalate(
        self,
        task: Task,
        transition: Transition,
        outcome: Outcome | None,
        *,
        question: str | None = None,
        result: TurnResult | None = None,
        role: Role | None = None,
    ) -> Task:
        """Apply and packet an escalation through TaskFlow's shared boundary."""
        ...


class IntegrationTurns:
    """Coordinate conflict, consult and post-rebase verify turns."""

    def __init__(
        self,
        *,
        coordinator: IntegrationCoordinator,
        history: ReportHistory,
        tasks: TaskStore,
    ) -> None:
        """Use the task turn host while coordinating the integration protocol."""
        self._coordinator = coordinator
        self._history = history
        self._tasks = tasks

    async def run(self, task: Task, host: IntegrationTurnHost) -> None:
        """Run integration and its conflict turns, then escalate blocked integration.

        Resolved conflicts whose post-rebase verify never ran (the flow was interrupted) are
        verified before integrating, so no resolution merges unreviewed.
        """

        async def resolve_conflicts(
            current: Task, continue_rebase: Callable[[], Awaitable[None]]
        ) -> tuple[Task, IntegrationMetadata]:
            resolved = await self._resolve_conflicts(current, continue_rebase, host)
            return resolved, self._history.integration_metadata(resolved)

        async def continue_rebase() -> None:
            await self._coordinator.continue_rebase(task)

        try:
            current = task
            if self._awaits_review(task):
                current = await self._resolve_conflicts(task, continue_rebase, host, resolved=True)
            metadata = self._history.integration_metadata(current)
            await self._coordinator.run(current, resolve_conflicts, metadata)
        except IntegrationTurnStopped, IntegrationHeld:
            return
        except (
            IntegrationBlocked,
            IntegrationConflict,
            PullRequestClosed,
            UncommittedWork,
        ) as error:
            current = self._tasks.require(task.id)
            transition = Transition.escalate(current, error.reason, step=Step.INTEGRATE)
            await host.escalate(current, transition, None, question=str(error))

    def _awaits_review(self, task: Task) -> bool:
        latest = self._history.latest_report(task.id)
        return (
            task.step is Step.INTEGRATE
            and isinstance(latest, ImplementationReport)
            and latest.status == "completed"
        )

    async def _resolve_conflicts(
        self,
        task: Task,
        continue_rebase: Callable[[], Awaitable[None]],
        host: IntegrationTurnHost,
        *,
        resolved: bool = False,
    ) -> Task:
        current = task
        context = ""
        while True:
            if not resolved:
                current, context, resolved = await self._conflict_turn(current, context, host)
                continue
            await continue_rebase()
            current, verdict = await self._verify_conflicts(current, host)
            if verdict.decision == "accept":
                return current
            context = "; ".join(issue.description for issue in verdict.issues)
            resolved = False

    async def _conflict_turn(
        self, task: Task, context: str, host: IntegrationTurnHost
    ) -> tuple[Task, str, bool]:
        step = Step.CONSULT if task.step is Step.CONSULT else Step.INTEGRATE
        current, _, stopped = await host.run_step(
            task, step, TurnCounters(), conflict_context=context
        )
        if stopped:
            raise IntegrationTurnStopped
        latest = self._history.latest_report(current.id)
        if step is Step.CONSULT:
            if not isinstance(latest, ConsultAnswer):
                message = f"{current.id} integration consult produced no answer"
                raise ShikiruError(message)
            return current, latest.answer, False
        if not isinstance(latest, ImplementationReport):
            message = f"{current.id} conflicts turn produced no implementation report"
            raise ShikiruError(message)
        if latest.status == "completed":
            return current, context, True
        if current.step is not Step.CONSULT:
            message = f"{current.id} conflict report did not route to consult"
            raise ShikiruError(message)
        return current, context, False

    async def _verify_conflicts(
        self, task: Task, host: IntegrationTurnHost
    ) -> tuple[Task, Verdict]:
        current, outcome = await host.run_report_turn(
            task,
            role=Role.LEADER,
            template=PromptName.VERIFY,
            kind=ReportKind.VERDICT,
        )
        if not isinstance(outcome, Verdict):
            message = f"{task.id} post-rebase turn produced no verdict"
            raise ShikiruError(message)
        updated, escalated = await host.advance(current, outcome, role=Role.LEADER)
        if escalated:
            raise IntegrationTurnStopped
        return updated, outcome
