"""Gathers the evidence of a task escalation and files it with the `EscalationRaiser` (03 §8)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from shikiru.agents.contracts import role_dir
from shikiru.domain.recovery import Failed, ProtocolError, Stalled
from shikiru.domain.reports import ConsultAnswer, ImplementationReport, Verdict
from shikiru.domain.steps import ACTING_ROLE
from shikiru.domain.tasks import EscalationReason
from shikiru.errors import ShikiruError
from shikiru.paths import run_dir

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from shikiru.domain.recovery import TurnResult
    from shikiru.domain.steps import Outcome
    from shikiru.domain.tasks import Role, Step, Task
    from shikiru.engine.escalations import EscalationRaiser
    from shikiru.engine.history import ReportHistory
    from shikiru.engine.task_facts import TaskFacts

_LOGGER = logging.getLogger(__name__)

ESCALATION_PANE_TAIL_LINES = 60
"""Lines of pane scrollback captured as escalation evidence (03 §8)."""

FACTS_UNAVAILABLE = "(facts unavailable: {error})"


def escalation_narrative(
    reason: EscalationReason,
    outcome: Outcome | None,
    result: TurnResult | None = None,
) -> tuple[str, str]:
    """Return the default question and the attempted-recovery text of an escalation."""
    question = "An Operator decision is needed to continue."
    attempted = "Automatic recovery has no option left"
    if reason is EscalationReason.ITERATIONS_EXHAUSTED:
        question = "The final round still requested changes; choose accept, supersede or cancel."
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
    """Collects reports, workspace facts and the pane tail, then records the escalation."""

    def __init__(
        self,
        *,
        raiser: EscalationRaiser,
        history: ReportHistory,
        facts: TaskFacts,
        project_root: Path,
    ) -> None:
        """Record escalations through `raiser` with evidence from the task's files and git."""
        self._raiser = raiser
        self._history = history
        self._facts = facts
        self._project_root = project_root

    async def raise_escalation(
        self,
        task: Task,
        step: Step | None,
        reason: EscalationReason,
        outcome: Outcome | None,
        *,
        question: str | None = None,
        result: TurnResult | None = None,
        role: Role | None = None,
        apply_state: Callable[[], Task],
    ) -> None:
        """Build the packet and commit `apply_state` with the escalation for a task flow."""
        narrative_question, attempted = escalation_narrative(reason, outcome, result)
        if question is None:
            question = narrative_question
        try:
            history = self._history.load(task.id)
        except Exception:
            _LOGGER.exception("could not load report history for escalation of %s", task.id)
            history = []
        await self._raiser.raise_escalation(
            task,
            step=step,
            reason=reason,
            question=question,
            attempted=attempted,
            reports=self._history.context(history),
            facts=await self._workspace_facts(task),
            view_tail=self._view_tail(task, step, role),
            apply_state=apply_state,
        )

    async def _workspace_facts(self, task: Task) -> str:
        if task.repo is None:
            return ""
        try:
            return await self._facts.text(task)
        except ShikiruError as error:
            return FACTS_UNAVAILABLE.format(error=error)

    def _view_tail(self, task: Task, step: Step | None, role_override: Role | None) -> str:
        role = role_override
        if role is None and step is not None:
            role = ACTING_ROLE[step]
        if role is None:
            return ""
        path = role_dir(run_dir(self._project_root, task.id), role.value) / "view.jsonl"
        if not path.is_file():
            return ""
        return "\n".join(path.read_text().splitlines()[-ESCALATION_PANE_TAIL_LINES:])
