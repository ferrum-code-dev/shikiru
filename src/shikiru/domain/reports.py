"""Report models an agent's turn produces, validated by `shikiru report` (05 §4)."""

from __future__ import annotations

import re
from enum import StrEnum, auto
from typing import Any, Literal, Self

from pydantic import Field, model_validator

from shikiru.config.settings import ComplexityName
from shikiru.domain.tasks import DomainModel, Mode

TRIAGE_TITLE_MAX_LENGTH = 60

_DEPENDS_ON_PATTERN = re.compile(r"^(T-\d+|#\d+)$")


def _require_iff(*, condition: bool, present: bool, field: str, rule: str) -> None:
    """Enforce one "required iff `rule`" field, raising a message naming `field` and `rule`."""
    if condition and not present:
        message = f"{field}: required when {rule}"
        raise ValueError(message)
    if not condition and present:
        message = f"{field}: must be empty unless {rule}"
        raise ValueError(message)


class NewTask(DomainModel):
    """A task to enqueue from a split, plan or verdict report (03 §2, §6; D4)."""

    title: str
    request: str
    priority: int = 0
    depends_on: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _depends_on_refers_to_existing_or_sibling_tasks(self) -> Self:
        invalid = [dep for dep in self.depends_on if not _DEPENDS_ON_PATTERN.match(dep)]
        if invalid:
            message = f"depends_on: {invalid!r} must each be an existing 'T-n' or a sibling '#k'"
            raise ValueError(message)
        return self


class Commit(DomainModel):
    """One commit an agent made during its turn."""

    repo: str
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
    options: list[Option]

    @model_validator(mode="after")
    def _exactly_one_recommended_option(self) -> Self:
        recommended = [option for option in self.options if option.recommended]
        if len(recommended) != 1:
            message = f"options: exactly one option must be recommended, found {len(recommended)}"
            raise ValueError(message)
        return self


class TriageReport(DomainModel):
    """The Leader's `triage` report (03 §2)."""

    title: str
    mode: Mode
    complexity: ComplexityName
    acceptance_criteria: list[str]
    references: list[str]
    subtasks: list[NewTask] = Field(default_factory=list)

    @model_validator(mode="after")
    def _title_is_at_most_60_characters(self) -> Self:
        if len(self.title) > TRIAGE_TITLE_MAX_LENGTH:
            message = f"title: must be at most {TRIAGE_TITLE_MAX_LENGTH} characters"
            raise ValueError(message)
        return self

    @model_validator(mode="after")
    def _subtasks_required_iff_split(self) -> Self:
        _require_iff(
            condition=self.mode is Mode.SPLIT,
            present=bool(self.subtasks),
            field="subtasks",
            rule="mode is 'split'",
        )
        return self


class ImplementationReport(DomainModel):
    """The Implementer's `implement`/`rework`/`conflicts` report (03 §2, §6)."""

    status: Literal["completed", "needs_decision", "blocked"]
    summary: str
    commits: list[Commit]
    tests: list[TestRun]
    trackers_updated: bool
    risks: list[str] = Field(default_factory=list)
    question: str = ""

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


class ConsultAnswer(DomainModel):
    """The Leader's answer to a `consult` turn (03 §2)."""

    decision: Literal["answer", "escalate"]
    answer: str = ""
    escalation: EscalationRequest | None = None

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
    issues: list[Issue] = Field(default_factory=list)
    residual_risks: list[str] = Field(default_factory=list)
    new_tasks: list[NewTask] = Field(default_factory=list)
    scope_changes: list[str] = Field(default_factory=list)
    escalation: EscalationRequest | None = None

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
