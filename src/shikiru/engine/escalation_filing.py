"""Files a task flow's escalation with its gathered evidence."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.domain.recovery import Failed, ProtocolError, Stalled
from shikiru.domain.reports import ConsultAnswer, ImplementationReport, Verdict
from shikiru.domain.tasks import EscalationReason, Status
from shikiru.errors import InvariantError

if TYPE_CHECKING:
    from shikiru.domain.recovery import TurnResult
    from shikiru.domain.steps import Outcome, Transition
    from shikiru.domain.tasks import Role, Task
    from shikiru.engine.escalations import EscalationRaiser
    from shikiru.engine.evidence import EvidenceCollector
    from shikiru.engine.transitions import TransitionWriter
    from shikiru.store.tasks import TaskStore


def escalation_narrative(
    reason: EscalationReason,
    outcome: Outcome | None,
    result: TurnResult | None = None,
) -> tuple[str, str]:
    """Return the default question and the attempted-recovery text of an escalation."""
    question = "An Operator decision is needed to continue."
    attempted = "Automatic recovery has no option left"
    if reason is EscalationReason.ITERATIONS_EXHAUSTED:
        question = (
            "The final round still requested changes; give final-round guidance, accept or cancel."
        )
        attempted = "Implement and verify cycled to the configured iteration limit."
    elif reason is EscalationReason.ENGINE_ERROR:
        attempted = "The engine raised an error no automatic recovery covers."
    elif isinstance(outcome, ConsultAnswer) and outcome.escalation is not None:
        question = outcome.escalation.question
        attempted = "The Leader escalated during a consult turn."
    elif isinstance(outcome, Verdict) and outcome.escalation is not None:
        question = outcome.escalation.question
        attempted = "The Leader escalated during verification."
    elif isinstance(outcome, ImplementationReport):
        question = outcome.question
        attempted = "The Implementer's question exceeded the consult limit."
    elif isinstance(result, Failed):
        question = result.message
        attempted = f"The agent reported a {result.kind.value} failure."
    elif isinstance(result, Stalled):
        question = "The agent's turn stream stopped progressing."
        attempted = "Automatic stall recovery was exhausted."
    elif isinstance(result, ProtocolError):
        question = "The agent did not produce a valid report."
        attempted = "The Helper could not rebuild it."
    elif result is not None:
        question = "The agent session could not continue."
        attempted = f"Recovery ended with {type(result).__name__}."
    return question, attempted


class EscalationFiler:
    """Collects the evidence, then persists the escalated state and records the escalation."""

    def __init__(
        self,
        *,
        raiser: EscalationRaiser,
        evidence: EvidenceCollector,
        writer: TransitionWriter,
        tasks: TaskStore,
    ) -> None:
        """Record escalations through `raiser` with evidence from `evidence`, state via `writer`."""
        self._raiser = raiser
        self._evidence = evidence
        self._writer = writer
        self._tasks = tasks

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
        """Apply the escalating `transition` with its packet and return the stored task."""
        reason = transition.escalation_reason
        if transition.status is not Status.ESCALATED or reason is None:
            message = f"{task.id} escalation has no escalated status or reason"
            raise InvariantError(message)
        narrative_question, attempted = escalation_narrative(reason, outcome, result)
        evidence = await self._evidence.collect(task, transition.step, role)
        agent_request = (
            outcome.escalation if isinstance(outcome, (ConsultAnswer, Verdict)) else None
        )
        await self._raiser.raise_escalation(
            task,
            step=transition.step,
            reason=reason,
            question=narrative_question if question is None else question,
            attempted=attempted,
            reports=evidence.reports,
            facts=evidence.facts,
            view_tail=evidence.view_tail,
            apply_state=lambda: self._writer.persist(task, transition, outcome),
            agent_request=agent_request,
        )
        return self._tasks.require(task.id)
