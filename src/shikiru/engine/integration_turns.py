"""Run the agent turns needed to resolve integration conflicts."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Protocol

from shikiru.domain.recovery import TurnCounters
from shikiru.domain.reports import ConsultAnswer, ImplementationReport, ReportKind, Verdict
from shikiru.domain.steps import Transition, next_step
from shikiru.domain.tasks import EscalationReason, Role, Status, Step, Task
from shikiru.engine.integration import (
    IntegrationBlocked,
    IntegrationConflict,
    IntegrationCoordinator,
)
from shikiru.errors import ShikiruError
from shikiru.prompts.renderer import PromptName

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from shikiru.config.settings import Settings
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

    async def apply(self, task: Task, transition: Transition, outcome: Outcome | None) -> Task:
        """Apply a domain transition and refresh its task title."""
        ...

    async def raise_escalation(
        self,
        task: Task,
        step: Step | None,
        reason: EscalationReason,
        outcome: Outcome | None,
        *,
        result: TurnResult | None = None,
        role: Role | None = None,
    ) -> None:
        """Create an escalation from a task flow."""
        ...

    def require_task(self, task_id: str) -> Task:
        """Reload a task or raise when it disappeared during its flow."""
        ...

    def set_consult_question(self, task_id: str, question: str) -> None:
        """Set the question consumed by a later consult turn."""
        ...


class IntegrationTurns:
    """Coordinate conflict, consult and post-rebase verify turns."""

    def __init__(
        self,
        *,
        coordinator: IntegrationCoordinator,
        history: ReportHistory,
        settings: Settings,
        tasks: TaskStore,
    ) -> None:
        """Use the task turn host while coordinating the integration protocol."""
        self._coordinator = coordinator
        self._history = history
        self._settings = settings
        self._tasks = tasks

    async def run(self, task: Task, host: IntegrationTurnHost) -> None:
        """Run integration and its conflict turns, then escalate blocked integration."""
        metadata = self._history.integration_metadata(task)

        async def resolve_conflicts(
            current: Task, continue_rebase: Callable[[], Awaitable[None]]
        ) -> Task:
            resolved = await self._resolve_conflicts(current, continue_rebase, host)
            refreshed = self._history.integration_metadata(resolved)
            metadata.summary = refreshed.summary
            metadata.residual_risks = refreshed.residual_risks
            return resolved

        try:
            await self._coordinator.run(task, resolve_conflicts, metadata)
        except IntegrationTurnStopped:
            return
        except (IntegrationBlocked, IntegrationConflict) as error:
            current = host.require_task(task.id)
            transition = dataclasses.replace(
                Transition.stay(current, step=Step.INTEGRATE, status=Status.ESCALATED),
                escalation_reason=error.reason,
            )
            updated = await host.apply(current, transition, None)
            host.set_consult_question(current.id, str(error))
            await host.raise_escalation(updated, Step.INTEGRATE, error.reason, None)

    async def _resolve_conflicts(
        self,
        task: Task,
        continue_rebase: Callable[[], Awaitable[None]],
        host: IntegrationTurnHost,
    ) -> Task:
        current = task
        context = ""
        while True:
            current, _, stopped = await host.run_step(
                current,
                Step.INTEGRATE,
                TurnCounters(),
                conflict_context=context,
            )
            if stopped:
                raise IntegrationTurnStopped
            latest = self._history.latest_report(current.id)
            if not isinstance(latest, ImplementationReport):
                message = f"{current.id} conflicts turn produced no implementation report"
                raise ShikiruError(message)
            if latest.status != "completed":
                if current.step is not Step.CONSULT:
                    message = f"{current.id} conflict report did not route to consult"
                    raise ShikiruError(message)
                current, _, stopped = await host.run_step(
                    current, Step.CONSULT, TurnCounters(), conflict_context=context
                )
                if stopped:
                    raise IntegrationTurnStopped
                answer = self._history.latest_report(current.id)
                if not isinstance(answer, ConsultAnswer):
                    message = f"{current.id} integration consult produced no answer"
                    raise ShikiruError(message)
                context = answer.answer
                continue
            await continue_rebase()
            current, verdict = await self._verify_conflicts(current, host)
            if verdict.decision == "accept":
                return current
            context = "; ".join(issue.description for issue in verdict.issues)

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
        transition = next_step(current, outcome, self._settings)
        escalated = transition.status is Status.ESCALATED
        updated = await host.apply(current, transition, outcome)
        if escalated:
            reason = transition.escalation_reason
            if reason is None:
                message = f"{task.id} post-rebase verdict escalated without a reason"
                raise ShikiruError(message)
            await host.raise_escalation(updated, transition.step, reason, outcome, role=Role.LEADER)
            raise IntegrationTurnStopped
        return updated, outcome
