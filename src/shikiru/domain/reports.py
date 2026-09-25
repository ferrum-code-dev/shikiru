"""Report models an agent's turn produces, validated by `shikiru report` (05 §4)."""

from __future__ import annotations

from enum import StrEnum, auto
from typing import TYPE_CHECKING, Annotated, Any, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from shikiru.config.settings import ComplexityName
from shikiru.domain.tasks import DomainModel, Mode

if TYPE_CHECKING:
    from collections.abc import Sequence

TRIAGE_TITLE_MAX_LENGTH = 60

CURRENT_TURN_LINK_NAME = "current_turn"
PROMPT_FILENAME = "prompt.md"
FACTS_FILENAME = "facts.md"
REPORT_KIND_FILENAME = "report.kind"
REPORT_SCHEMA_FILENAME = "report.schema.json"
REPORT_FILENAME = "report.json"
HELPER_REPORT_FILENAME = "helper.json"

SIBLING_REFERENCE_PREFIX = "#"
DEPENDS_ON_PATTERN = rf"^(T-\d+|{SIBLING_REFERENCE_PREFIX}\d+)$"

_TaskReference = Annotated[str, StringConstraints(pattern=DEPENDS_ON_PATTERN)]


def resolve_dependency(reference: str, sibling_ids: Sequence[str]) -> str:
    """Replace a report-local sibling reference with the assigned task id."""
    if reference.startswith(SIBLING_REFERENCE_PREFIX):
        return sibling_ids[int(reference.removeprefix(SIBLING_REFERENCE_PREFIX)) - 1]
    return reference


def _require_iff(*, condition: bool, present: bool, field: str, rule: str) -> None:
    """Enforce one "required iff `rule`" field, raising a message naming `field` and `rule`."""
    if condition and not present:
        message = f"{field}: required when {rule}"
        raise ValueError(message)
    if not condition and present:
        message = f"{field}: must be empty unless {rule}"
        raise ValueError(message)


def _require_sibling_references_in_range(new_tasks: list[NewTask], field: str) -> None:
    """Reject a `#k` reference that doesn't name one of the report's own `new_tasks`."""
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
    """A task to enqueue from a plan or verdict report (03 §2, §6; D4)."""

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
    """One option offered to the Operator in an escalation (03 §8)."""

    label: str
    consequences: str
    recommended: bool = False


class EscalationRequest(DomainModel):
    """The Leader's escalation packet content: a question and its options (03 §8)."""

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
    """The Leader's `triage` report (03 §2)."""

    title: str = Field(max_length=TRIAGE_TITLE_MAX_LENGTH)
    mode: Mode
    complexity: ComplexityName
    acceptance_criteria: list[str]
    references: list[str]


class ImplementationReport(DomainModel):
    """The Implementer's `implement`/`rework`/`conflicts` report (03 §2, §6)."""

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
        if self.status != "completed" and not self.question:
            message = "question: required unless status is 'completed'"
            raise ValueError(message)
        return self


class PlanReport(DomainModel):
    """The Leader's `plan` report (03 §2)."""

    summary: str
    commits: list[Commit]
    new_tasks: list[NewTask] = Field(default_factory=list)
    scope_changes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _sibling_references_in_range(self) -> Self:
        _require_sibling_references_in_range(self.new_tasks, "new_tasks")
        return self


class ConsultAnswer(DomainModel):
    """The Leader's answer to a `consult` turn (03 §2)."""

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
            condition=self.decision == "answer",
            present=bool(self.answer),
            field="answer",
            rule="decision is 'answer'",
        )
        return self

    @model_validator(mode="after")
    def _escalation_required_iff_decision_is_escalate(self) -> Self:
        _require_iff(
            condition=self.decision == "escalate",
            present=self.escalation is not None,
            field="escalation",
            rule="decision is 'escalate'",
        )
        return self


class Verdict(DomainModel):
    """The Leader's `verify` report (03 §2, §3; post-rebase `conflicts` verify, 03 §6)."""

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
            condition=self.decision == "changes_requested",
            present=bool(self.issues),
            field="issues",
            rule="decision is 'changes_requested'",
        )
        return self

    @model_validator(mode="after")
    def _new_tasks_non_empty_iff_supersede(self) -> Self:
        _require_iff(
            condition=self.decision == "supersede",
            present=bool(self.new_tasks),
            field="new_tasks",
            rule="decision is 'supersede'",
        )
        _require_sibling_references_in_range(self.new_tasks, "new_tasks")
        return self

    @model_validator(mode="after")
    def _escalation_required_iff_decision_is_escalate(self) -> Self:
        _require_iff(
            condition=self.decision == "escalate",
            present=self.escalation is not None,
            field="escalation",
            rule="decision is 'escalate'",
        )
        return self


class ReportKind(StrEnum):
    """The `report.kind` discriminator naming a turn's report model (02 §5, 03 §4.3)."""

    TRIAGE = auto()
    IMPLEMENTATION = auto()
    PLAN = auto()
    CONSULT = auto()
    VERDICT = auto()


Report = TriageReport | ImplementationReport | PlanReport | ConsultAnswer | Verdict

_MODELS_BY_KIND: dict[ReportKind, type[Report]] = {
    ReportKind.TRIAGE: TriageReport,
    ReportKind.IMPLEMENTATION: ImplementationReport,
    ReportKind.PLAN: PlanReport,
    ReportKind.CONSULT: ConsultAnswer,
    ReportKind.VERDICT: Verdict,
}


def model_for(kind: ReportKind) -> type[Report]:
    """Return the pydantic model that validates reports of `kind`."""
    return _MODELS_BY_KIND[kind]


def schema_for(kind: ReportKind) -> dict[str, Any]:
    """Return the JSON Schema for `kind`'s report model, written as `report.schema.json`."""
    return model_for(kind).model_json_schema()
