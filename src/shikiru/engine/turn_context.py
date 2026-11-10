"""Prompt-context preparation for a task's next turn."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from shikiru.domain.reports import FeedbackAction, ReportKind
from shikiru.domain.tasks import Step
from shikiru.engine.task_facts import require_repo
from shikiru.engine.turns import TurnRequest
from shikiru.errors import InvariantError
from shikiru.paths import task_root
from shikiru.prompts.renderer import PromptName

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from shikiru.config.settings import Settings
    from shikiru.domain.feedback import FeedbackInput
    from shikiru.domain.reports import Report
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

_JSON_INDENT = 2

FEEDBACK_TEMPLATES = frozenset(
    {PromptName.FEEDBACK_ANALYSIS, PromptName.FEEDBACK_REWORK, PromptName.FEEDBACK_VERIFY}
)


class ContextKey(StrEnum):
    """The variables the prompt templates read from a turn's context."""

    REQUEST = "request"
    TASK_ID = "task_id"
    WORKSPACE = "workspace"
    TRACKERS = "trackers"
    REPORTS = "reports"
    QUESTION = "question"
    OPERATOR_DECISION = "operator_decision"
    FACTS = "facts"
    BASE = "base"
    FINAL_ROUND = "final_round"
    CONFLICT = "conflict"
    MODE = "mode"
    FEEDBACK_SNAPSHOT = "feedback_snapshot"
    FEEDBACK_ANALYSIS = "feedback_analysis"
    FEEDBACK_WORK = "feedback_work"


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
    message = f"No turn template exists for step {step!r}"
    raise InvariantError(message)


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
        feedback: FeedbackInput | None = None,
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
            ContextKey.REQUEST: task.request,
            ContextKey.TASK_ID: task.id,
            ContextKey.WORKSPACE: str(task_root(self._project_root, task.id)),
            ContextKey.TRACKERS: list(self._settings.project.trackers),
            ContextKey.REPORTS: self._history.context(history),
        }
        context.update(self._decision_context(task, template, history))
        fresh = session_handoff and not continue_turn
        handoff = (fresh and bool(history)) or next_turn.handoff
        needs_facts = (
            template in (PromptName.VERIFY, PromptName.CONFLICTS, *FEEDBACK_TEMPLATES) or handoff
        )
        facts_text = await self._facts.text(task) if needs_facts else ""
        if handoff and template not in FEEDBACK_TEMPLATES:
            template = PromptName.HANDOFF
        if needs_facts:
            context[ContextKey.FACTS] = facts_text
        if template in FEEDBACK_TEMPLATES:
            context.update(self._feedback_context(task, template, feedback))
        if template is PromptName.VERIFY:
            context[ContextKey.BASE] = require_repo(task).base_ref
            context[ContextKey.FINAL_ROUND] = (
                task.iteration >= self._settings.orchestration.max_iterations
            )
        if template is PromptName.CONFLICTS:
            context[ContextKey.CONFLICT] = conflict_context or ""
        return TurnRequest(
            step=step,
            iteration=task.iteration,
            kind=kind,
            template=template,
            context=context,
            facts=facts_text,
            feedback=feedback,
        )

    def _decision_context(
        self, task: Task, template: PromptName, history: Sequence[tuple[str, Report]]
    ) -> dict[str, object]:
        if template is PromptName.CONSULT:
            return {
                ContextKey.QUESTION: self._history.latest_question(history),
                ContextKey.OPERATOR_DECISION: (
                    self._escalation_store.latest_answer(task.id)
                    if task.counters.consults == 0
                    else None
                ),
            }
        if template is PromptName.FEEDBACK_ANALYSIS:
            return {
                ContextKey.OPERATOR_DECISION: self._escalation_store.latest_feedback_answer(task.id)
            }
        return {}

    @staticmethod
    def _feedback_context(
        task: Task, template: PromptName, feedback: FeedbackInput | None
    ) -> dict[str, object]:
        if feedback is None:
            message = f"{task.id} {template.value} turn has no feedback to work from"
            raise InvariantError(message)
        context: dict[str, object] = {
            ContextKey.MODE: task.mode.value if task.mode is not None else ""
        }
        if template is PromptName.FEEDBACK_ANALYSIS:
            context[ContextKey.FEEDBACK_SNAPSHOT] = feedback.snapshot.model_dump_json(
                indent=_JSON_INDENT
            )
            return context
        analysis = feedback.analysis
        if analysis is None:
            message = f"{task.id} {template.value} turn has no analysis to work from"
            raise InvariantError(message)
        if template is PromptName.FEEDBACK_REWORK:
            addressed = {
                decision.source_id
                for decision in analysis.decisions
                if decision.action is FeedbackAction.ADDRESS
            }
            context[ContextKey.FEEDBACK_SNAPSHOT] = feedback.snapshot.only(
                addressed
            ).model_dump_json(indent=_JSON_INDENT)
            context[ContextKey.FEEDBACK_WORK] = [
                work.model_dump_json(indent=_JSON_INDENT) for work in analysis.work
            ]
            return context
        context[ContextKey.BASE] = require_repo(task).base_ref
        context[ContextKey.FINAL_ROUND] = feedback.final_round
        context[ContextKey.FEEDBACK_SNAPSHOT] = feedback.snapshot.model_dump_json(
            indent=_JSON_INDENT
        )
        context[ContextKey.FEEDBACK_ANALYSIS] = analysis.model_dump_json(indent=_JSON_INDENT)
        return context
