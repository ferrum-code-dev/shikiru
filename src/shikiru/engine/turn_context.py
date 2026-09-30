"""Prompt-context preparation for a task's next turn (03 §2)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from shikiru.domain.reports import ReportKind
from shikiru.domain.tasks import Step
from shikiru.engine.task_facts import require_repo
from shikiru.engine.turns import TurnRequest
from shikiru.errors import ShikiruError
from shikiru.paths import task_root
from shikiru.prompts.renderer import PromptName

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.config.settings import Settings
    from shikiru.domain.tasks import Task
    from shikiru.engine.history import ReportHistory
    from shikiru.engine.task_facts import TaskFacts
    from shikiru.store.escalations import EscalationStore


@dataclass(frozen=True)
class NextTurn:
    """How recovery wants the next turn started: as a handoff or as a continuation."""

    handoff: bool = False
    continue_turn: bool = False


ORDINARY_TURN = NextTurn()


def template_for_step(step: Step, iteration: int) -> tuple[PromptName, ReportKind]:
    """Return the prompt template and report kind of `step`'s ordinary turn."""
    if step is Step.TRIAGE:
        return PromptName.TRIAGE, ReportKind.TRIAGE
    if step is Step.PLAN:
        return PromptName.PLAN, ReportKind.PLAN
    if step is Step.IMPLEMENT:
        template = PromptName.IMPLEMENT if iteration <= 1 else PromptName.REWORK
        return template, ReportKind.IMPLEMENTATION
    if step is Step.CONSULT:
        return PromptName.CONSULT, ReportKind.CONSULT
    if step is Step.VERIFY:
        return PromptName.VERIFY, ReportKind.VERDICT
    if step is Step.INTEGRATE:
        return PromptName.CONFLICTS, ReportKind.IMPLEMENTATION
    message = f"TaskFlow has no turn template for step {step!r}"
    raise ShikiruError(message)


class TurnContextPreparer:
    """Builds each turn's `TurnRequest`."""

    def __init__(
        self,
        *,
        history: ReportHistory,
        escalation_store: EscalationStore,
        facts: TaskFacts,
        settings: Settings,
        project_root: Path,
    ) -> None:
        """Prepare contexts from report history, operator answers and workspace facts."""
        self._history = history
        self._escalation_store = escalation_store
        self._facts = facts
        self._settings = settings
        self._project_root = project_root

    async def prepare(
        self,
        task: Task,
        step: Step,
        *,
        session_handoff: bool,
        next_turn: NextTurn = ORDINARY_TURN,
        template_override: PromptName | None = None,
        kind_override: ReportKind | None = None,
        conflict_context: str | None = None,
    ) -> TurnRequest:
        """Build the request of `task`'s next `step` turn."""
        history = self._history.load(task.id)
        continue_turn = next_turn.continue_turn
        if continue_turn:
            template_override = PromptName.CONTINUE
        template, kind = template_for_step(step, task.iteration)
        if template_override is not None:
            template = template_override
        if kind_override is not None:
            kind = kind_override
        context: dict[str, object] = {
            "request": task.request,
            "workspace": str(task_root(self._project_root, task.id)),
            "trackers": list(self._settings.project.trackers),
            "reports": self._history.context(history),
        }
        if template is PromptName.CONSULT:
            answer = (
                self._escalation_store.latest_answer(task.id)
                if task.counters.consults == 0
                else None
            )
            context["question"] = (
                answer if answer is not None else self._history.latest_question(history)
            )
        fresh = session_handoff and not continue_turn
        handoff = (fresh and bool(history)) or next_turn.handoff
        needs_facts = template in (PromptName.VERIFY, PromptName.CONFLICTS) or handoff
        facts_text = await self._facts.text(task) if needs_facts else ""
        if handoff:
            template = PromptName.HANDOFF
        if needs_facts:
            context["facts"] = facts_text
        if template is PromptName.VERIFY:
            context["base"] = require_repo(task).base_ref
            context["final_round"] = task.iteration >= self._settings.orchestration.max_iterations
        if template is PromptName.CONFLICTS:
            context["conflict"] = conflict_context or ""
        return TurnRequest(
            step=step,
            iteration=task.iteration,
            kind=kind,
            template=template,
            context=context,
            facts=facts_text,
        )
