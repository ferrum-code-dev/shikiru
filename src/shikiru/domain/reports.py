"""Report models an agent's turn produces, validated by `shikiru report`."""

from __future__ import annotations

from enum import StrEnum, auto
from typing import TYPE_CHECKING, Annotated, Any, Final, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from shikiru.config.settings import ComplexityName
from shikiru.domain.tasks import TASK_TITLE_MAX_LENGTH, DomainModel, Mode

if TYPE_CHECKING:
    from collections.abc import Sequence

DECISION_ACCEPT: Final = "accept"
DECISION_SUPERSEDE: Final = "supersede"
DECISION_CHANGES_REQUESTED: Final = "changes_requested"
DECISION_ANSWER: Final = "answer"
DECISION_ESCALATE: Final = "escalate"
SEVERITY_BLOCKER: Final = "blocker"
STATUS_COMPLETED: Final = "completed"

REPORT_JSON_INDENT = 2
CURRENT_TURN_LINK_NAME = "current_turn"
PROMPT_FILENAME = "prompt.md"
FACTS_FILENAME = "facts.md"
REPORT_KIND_FILENAME = "report.kind"
FEEDBACK_FILENAME = "feedback.json"
REPORT_SCHEMA_FILENAME = "report.schema.json"
REPORT_FILENAME = "report.json"
HELPER_REPORT_FILENAME = "helper.json"

SIBLING_REFERENCE_PREFIX = "#"
DEPENDS_ON_PATTERN = rf"^(T-\d+|{SIBLING_REFERENCE_PREFIX}\d+)$"

_TaskReference = Annotated[str, StringConstraints(pattern=DEPENDS_ON_PATTERN)]
_Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def resolve_dependency(reference: str, sibling_ids: Sequence[str]) -> str:
    """Replace a report-local sibling reference with the assigned task id."""
    if reference.startswith(SIBLING_REFERENCE_PREFIX):
        return sibling_ids[int(reference.removeprefix(SIBLING_REFERENCE_PREFIX)) - 1]
    return reference


def _require_iff(*, condition: bool, present: bool, field: str, rule: str) -> None:
    if condition and not present:
        message = f"{field}: required when {rule}"
        raise ValueError(message)
    if not condition and present:
        message = f"{field}: must be empty unless {rule}"
        raise ValueError(message)


def _require_unique(identities: list[str], field: str) -> None:
    duplicates = sorted({identity for identity in identities if identities.count(identity) > 1})
    if duplicates:
        message = f"{field}: duplicate ids {duplicates}"
        raise ValueError(message)


def _require_sibling_references_in_range(new_tasks: list[NewTask], field: str) -> None:
    for new_task in new_tasks:
        for reference in new_task.depends_on:
            if reference.startswith(SIBLING_REFERENCE_PREFIX) and not (
                1 <= int(reference[1:]) <= len(new_tasks)
            ):
                message = (
                    f"{field}: sibling reference {reference!r} must be #1..#{len(new_tasks)} "
                    "(1-based)"
                )
                raise ValueError(message)


class NewTask(DomainModel):
    """A task to enqueue from a plan or verdict report."""

    title: str
    request: str
    priority: int = 0
    depends_on: list[_TaskReference] = Field(
        default_factory=list,
        description=(
            "Each item is an existing 'T-n' or a sibling '#k': the k-th task of this list, "
            "counting from 1."
        ),
    )


class Commit(DomainModel):
    """One commit an agent made in the project's repository during its turn."""

    sha: str
    subject: str


class TestRun(DomainModel):
    """One test (or test suite) the agent ran."""

    name: str
    result: Literal["passed", "failed", "skipped"]
    details: str = ""


class Issue(DomainModel):
    """One issue a verdict raises against an implementation."""

    severity: Literal["blocker", "major", "minor"]
    description: str
    location: str = ""


class Option(DomainModel):
    """One option offered to the Operator in an escalation."""

    label: str
    consequences: str
    recommended: bool = False
    needs_input: bool = Field(
        default=False,
        description="True when choosing this option means the Operator must supply something.",
    )


class EscalationRequest(DomainModel):
    """The Leader's escalation packet content: a question and its options."""

    question: str
    options: list[Option] = Field(description="Exactly one option must have recommended=true.")

    @model_validator(mode="after")
    def _exactly_one_recommended_option(self) -> Self:
        recommended = [option for option in self.options if option.recommended]
        if len(recommended) != 1:
            message = f"options: exactly one option must be recommended, found {len(recommended)}"
            raise ValueError(message)
        return self


class TriageReport(DomainModel):
    """The Leader's `triage` report."""

    title: str = Field(max_length=TASK_TITLE_MAX_LENGTH)
    mode: Mode
    complexity: ComplexityName
    acceptance_criteria: list[str]
    references: list[str]


class ImplementationReport(DomainModel):
    """The Implementer's `implement`/`rework`/`conflicts` report."""

    status: Literal["completed", "needs_decision", "blocked"]
    summary: str
    commits: list[Commit]
    tests: list[TestRun]
    trackers_updated: bool
    risks: list[str] = Field(default_factory=list)
    question: str = Field(
        default="", description="Required unless status is 'completed'; otherwise empty."
    )

    @model_validator(mode="after")
    def _question_required_unless_completed(self) -> Self:
        if self.status != STATUS_COMPLETED and not self.question:
            message = "question: required unless status is 'completed'"
            raise ValueError(message)
        return self


class PlanReport(DomainModel):
    """The Leader's `plan` report."""

    summary: str
    commits: list[Commit]
    new_tasks: list[NewTask] = Field(default_factory=list)
    scope_changes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _sibling_references_in_range(self) -> Self:
        _require_sibling_references_in_range(self.new_tasks, "new_tasks")
        return self


class ConsultAnswer(DomainModel):
    """The Leader's answer to a `consult` turn."""

    decision: Literal["answer", "escalate"]
    answer: str = Field(
        default="", description="Required and non-empty iff decision is 'answer'; otherwise empty."
    )
    escalation: EscalationRequest | None = Field(
        default=None, description="Required iff decision is 'escalate'; otherwise unset."
    )

    @model_validator(mode="after")
    def _answer_required_iff_decision_is_answer(self) -> Self:
        _require_iff(
            condition=self.decision == DECISION_ANSWER,
            present=bool(self.answer),
            field="answer",
            rule="decision is 'answer'",
        )
        return self

    @model_validator(mode="after")
    def _escalation_required_iff_decision_is_escalate(self) -> Self:
        _require_iff(
            condition=self.decision == DECISION_ESCALATE,
            present=self.escalation is not None,
            field="escalation",
            rule="decision is 'escalate'",
        )
        return self


class Verdict(DomainModel):
    """The Leader's `verify` report (post-rebase `conflicts` verify)."""

    decision: Literal["accept", "changes_requested", "escalate", "supersede"]
    summary: str
    issues: list[Issue] = Field(
        default_factory=list,
        description="Required and non-empty iff decision is 'changes_requested'; otherwise empty.",
    )
    residual_risks: list[str] = Field(default_factory=list)
    new_tasks: list[NewTask] = Field(
        default_factory=list,
        description="Required and non-empty iff decision is 'supersede'; otherwise empty.",
    )
    scope_changes: list[str] = Field(default_factory=list)
    escalation: EscalationRequest | None = Field(
        default=None, description="Required iff decision is 'escalate'; otherwise unset."
    )

    @model_validator(mode="after")
    def _issues_non_empty_iff_changes_requested(self) -> Self:
        _require_iff(
            condition=self.decision == DECISION_CHANGES_REQUESTED,
            present=bool(self.issues),
            field="issues",
            rule="decision is 'changes_requested'",
        )
        return self

    @model_validator(mode="after")
    def _new_tasks_non_empty_iff_supersede(self) -> Self:
        _require_iff(
            condition=self.decision == DECISION_SUPERSEDE,
            present=bool(self.new_tasks),
            field="new_tasks",
            rule="decision is 'supersede'",
        )
        _require_sibling_references_in_range(self.new_tasks, "new_tasks")
        return self

    @model_validator(mode="after")
    def _escalation_required_iff_decision_is_escalate(self) -> Self:
        _require_iff(
            condition=self.decision == DECISION_ESCALATE,
            present=self.escalation is not None,
            field="escalation",
            rule="decision is 'escalate'",
        )
        return self


class FeedbackAction(StrEnum):
    """What the Leader decided to do about one source item of PR review feedback."""

    ADDRESS = auto()
    ALREADY_ADDRESSED = auto()
    DISAGREE = auto()
    NEEDS_OPERATOR = auto()


class FeedbackWork(DomainModel):
    """One implementation instruction, possibly shared by several duplicate source items."""

    id: _Text
    instruction: _Text


class FeedbackDecision(DomainModel):
    """The Leader's decision for one review body or original inline discussion."""

    source_id: _Text
    action: FeedbackAction
    points: list[_Text] = Field(
        min_length=1,
        description="Every substantive point the source item raises, one entry each.",
    )
    detail: _Text = Field(
        description=(
            "address: the concrete work requested; already_addressed: the commit or file that "
            "shows it; disagree: the specific reason; needs_operator: the question to ask."
        )
    )
    work_id: str = Field(
        default="", description="Required iff action is 'address': the id of a `work` entry."
    )
    reply: str = Field(
        default="",
        description="Proposed reply to this source item; required unless action is "
        "'needs_operator', otherwise empty.",
    )

    @model_validator(mode="after")
    def _work_id_required_iff_address(self) -> Self:
        _require_iff(
            condition=self.action is FeedbackAction.ADDRESS,
            present=bool(self.work_id),
            field="work_id",
            rule="action is 'address'",
        )
        return self

    @model_validator(mode="after")
    def _reply_required_unless_needs_operator(self) -> Self:
        _require_iff(
            condition=self.action is not FeedbackAction.NEEDS_OPERATOR,
            present=bool(self.reply.strip()),
            field="reply",
            rule="action is not 'needs_operator'",
        )
        return self


class FeedbackAnalysis(DomainModel):
    """The Leader's `feedback_analysis` report: one decision per source item of a snapshot."""

    summary: _Text
    work: list[FeedbackWork] = Field(
        default_factory=list,
        description="Implementation instructions; one may serve several duplicate source items.",
    )
    decisions: list[FeedbackDecision] = Field(min_length=1)

    @model_validator(mode="after")
    def _identities_are_unique(self) -> Self:
        _require_unique([decision.source_id for decision in self.decisions], "decisions")
        _require_unique([work.id for work in self.work], "work")
        return self

    @model_validator(mode="after")
    def _work_matches_address_decisions(self) -> Self:
        work_ids = {work.id for work in self.work}
        used = {
            decision.work_id
            for decision in self.decisions
            if decision.action is FeedbackAction.ADDRESS
        }
        unknown = used - work_ids
        if unknown:
            message = f"decisions: work_id {sorted(unknown)} is not an id in `work`"
            raise ValueError(message)
        unused = work_ids - used
        if unused:
            message = f"work: {sorted(unused)} is not the work_id of any 'address' decision"
            raise ValueError(message)
        return self


class FeedbackRework(ImplementationReport):
    """The Implementer's report on the accepted work of a feedback round."""


class VerifiedReply(DomainModel):
    """The reply the Leader verified for one source item."""

    source_id: _Text
    reply: _Text


class FeedbackVerdict(Verdict):
    """The Leader's verification of a feedback round's changes and replies."""

    replies: list[VerifiedReply] = Field(
        default_factory=list,
        description=(
            "Required and non-empty iff decision is 'accept': the final reply for every source "
            "item whose decision has a reply; otherwise empty."
        ),
    )

    @model_validator(mode="after")
    def _decision_is_available(self) -> Self:
        if self.decision == DECISION_SUPERSEDE:
            message = "decision: 'supersede' is not available for feedback"
            raise ValueError(message)
        return self

    @model_validator(mode="after")
    def _replies_required_iff_accept(self) -> Self:
        _require_iff(
            condition=self.decision == DECISION_ACCEPT,
            present=bool(self.replies),
            field="replies",
            rule="decision is 'accept'",
        )
        _require_unique([reply.source_id for reply in self.replies], "replies")
        return self


class ReportKind(StrEnum):
    """The `report.kind` discriminator naming a turn's report model."""

    TRIAGE = auto()
    IMPLEMENTATION = auto()
    PLAN = auto()
    CONSULT = auto()
    VERDICT = auto()
    FEEDBACK_ANALYSIS = auto()
    FEEDBACK_REWORK = auto()
    FEEDBACK_VERDICT = auto()


FEEDBACK_REPORT_KINDS: Final = frozenset(
    {ReportKind.FEEDBACK_ANALYSIS, ReportKind.FEEDBACK_REWORK, ReportKind.FEEDBACK_VERDICT}
)

Report = (
    TriageReport | ImplementationReport | PlanReport | ConsultAnswer | Verdict | FeedbackAnalysis
)

_MODELS_BY_KIND: dict[ReportKind, type[Report]] = {
    ReportKind.TRIAGE: TriageReport,
    ReportKind.IMPLEMENTATION: ImplementationReport,
    ReportKind.PLAN: PlanReport,
    ReportKind.CONSULT: ConsultAnswer,
    ReportKind.VERDICT: Verdict,
    ReportKind.FEEDBACK_ANALYSIS: FeedbackAnalysis,
    ReportKind.FEEDBACK_REWORK: FeedbackRework,
    ReportKind.FEEDBACK_VERDICT: FeedbackVerdict,
}


def model_for(kind: ReportKind) -> type[Report]:
    """Return the pydantic model that validates reports of `kind`."""
    return _MODELS_BY_KIND[kind]


def schema_for(kind: ReportKind) -> dict[str, Any]:
    """Return the JSON Schema for `kind`'s report model, written as `report.schema.json`."""
    return model_for(kind).model_json_schema()
