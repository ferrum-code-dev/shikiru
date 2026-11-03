from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from shikiru.domain.reports import (
    ConsultAnswer,
    EscalationRequest,
    ImplementationReport,
    NewTask,
    Option,
    PlanReport,
    TriageReport,
    Verdict,
)


def _new_task(**overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {"title": "Add retries", "request": "Add retries to the parser"}
    fields.update(overrides)
    return fields


def _triage(**overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "title": "Add retries",
        "mode": "implement",
        "complexity": "normal",
        "acceptance_criteria": ["retries on failure"],
        "references": [],
    }
    fields.update(overrides)
    return fields


def _implementation(**overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "status": "completed",
        "summary": "Done",
        "commits": [],
        "tests": [],
        "trackers_updated": True,
    }
    fields.update(overrides)
    return fields


def _escalation(**overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "question": "Which approach?",
        "options": [
            {"label": "A", "consequences": "slower", "recommended": True},
            {"label": "B", "consequences": "riskier", "recommended": False},
        ],
    }
    fields.update(overrides)
    return fields


def _verdict(**overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {"decision": "accept", "summary": "Looks good"}
    fields.update(overrides)
    if fields["decision"] == "accept":
        fields.setdefault("commit_subject", "feat: looks good")
    return fields


def _plan(**overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {"summary": "P", "commit_subject": "docs: plan", "commits": []}
    fields.update(overrides)
    return fields


_INVALID_CASES: list[tuple[type[BaseModel], dict[str, Any], str]] = [
    (NewTask, _new_task(depends_on=["task-1"]), "depends_on"),
    (
        EscalationRequest,
        {
            "question": "Q",
            "options": [Option(label="A", consequences="x", recommended=False)],
        },
        "options",
    ),
    (
        EscalationRequest,
        {
            "question": "Q",
            "options": [
                Option(label="A", consequences="x", recommended=True),
                Option(label="B", consequences="y", recommended=True),
            ],
        },
        "options",
    ),
    (TriageReport, _triage(title="x" * 61), "title"),
    (PlanReport, _plan(new_tasks=[_new_task(depends_on=["#2"])]), "new_tasks"),
    (PlanReport, _plan(new_tasks=[_new_task(depends_on=["#0"])]), "new_tasks"),
    (PlanReport, _plan(commit_subject="Plan\nthe change"), "commit_subject"),
    (Verdict, _verdict(commit_subject=""), "commit_subject"),
    (Verdict, _verdict(commit_subject="Looks\ngood"), "commit_subject"),
    (
        Verdict,
        _verdict(
            decision="changes_requested",
            issues=[{"severity": "minor", "description": "x"}],
            commit_subject="feat: x",
        ),
        "commit_subject",
    ),
    (
        Verdict,
        _verdict(decision="supersede", new_tasks=[_new_task(depends_on=["#3"])]),
        "new_tasks",
    ),
    (ImplementationReport, _implementation(status="needs_decision", question=""), "question"),
    (ConsultAnswer, {"decision": "answer", "answer": ""}, "answer"),
    (
        ConsultAnswer,
        {
            "decision": "answer",
            "answer": "Use A",
            "escalation": EscalationRequest(**_escalation()),
        },
        "escalation",
    ),
    (ConsultAnswer, {"decision": "escalate"}, "escalation"),
    (
        ConsultAnswer,
        {
            "decision": "escalate",
            "answer": "Use A",
            "escalation": EscalationRequest(**_escalation()),
        },
        "answer",
    ),
    (Verdict, _verdict(decision="changes_requested", issues=[]), "issues"),
    (
        Verdict,
        _verdict(decision="accept", issues=[{"severity": "minor", "description": "x"}]),
        "issues",
    ),
    (Verdict, _verdict(decision="supersede", new_tasks=[]), "new_tasks"),
    (Verdict, _verdict(decision="accept", new_tasks=[_new_task()]), "new_tasks"),
    (Verdict, _verdict(decision="accept", escalation=_escalation()), "escalation"),
    (Verdict, _verdict(decision="escalate"), "escalation"),
]


@pytest.mark.parametrize(
    ("model", "data", "field"),
    _INVALID_CASES,
    ids=[
        f"{model.__name__}-{field}-{index}"
        for index, (model, _, field) in enumerate(_INVALID_CASES)
    ],
)
def test_invalid_reports_fail_on_the_expected_field(
    model: type[BaseModel], data: dict[str, Any], field: str
) -> None:
    with pytest.raises(ValidationError) as excinfo:
        model(**data)

    assert any(
        field in str(error["loc"]) or field in error["msg"] for error in excinfo.value.errors()
    )


_VALID_SUBJECTS = [
    "feat: add retries",
    "fix(parser): handle empty input",
    "refactor!: drop the legacy API",
    "security(parser): reject unsafe input",
    "docs: " + "x" * 66,
    "[PROJ-123] Add retry handling",
]
_INVALID_SUBJECTS = [
    "",
    "  ",
    "feat: first line\nsecond line",
    "feat: " + "x" * 67,
    " feat: leading whitespace",
    "feat: trailing whitespace ",
]


@pytest.mark.parametrize("subject", _VALID_SUBJECTS)
def test_a_project_convention_subject_is_accepted_by_plans_and_accepting_verdicts(
    subject: str,
) -> None:
    assert PlanReport(**_plan(commit_subject=subject)).commit_subject == subject
    assert Verdict(**_verdict(commit_subject=subject)).commit_subject == subject


@pytest.mark.parametrize("subject", _INVALID_SUBJECTS)
def test_an_invalid_subject_is_rejected_by_plans_and_accepting_verdicts(
    subject: str,
) -> None:
    with pytest.raises(ValidationError, match="commit_subject"):
        PlanReport(**_plan(commit_subject=subject))
    with pytest.raises(ValidationError, match="commit_subject"):
        Verdict(**_verdict(commit_subject=subject))


def test_new_plans_and_accepting_verdicts_require_a_subject() -> None:
    plan = _plan()
    plan.pop("commit_subject")
    verdict = _verdict()
    verdict.pop("commit_subject")

    with pytest.raises(ValidationError, match="commit_subject"):
        PlanReport(**plan)
    with pytest.raises(ValidationError, match="commit_subject"):
        Verdict(**verdict)


@pytest.mark.parametrize("reference", ["T-1", "#1"], ids=["task-id", "sibling"])
def test_new_task_depends_on_accepts_task_ids_and_sibling_references(reference: str) -> None:
    task = NewTask(**_new_task(depends_on=[reference]))

    assert task.depends_on == [reference]
