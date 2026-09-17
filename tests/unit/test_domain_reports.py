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
    ReportKind,
    TriageReport,
    Verdict,
    model_for,
    schema_for,
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
        "subtasks": [],
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


def _consult(**overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {"decision": "answer", "answer": "Use approach A"}
    fields.update(overrides)
    return fields


def _verdict(**overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {"decision": "accept", "summary": "Looks good"}
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
    (TriageReport, _triage(mode="split", subtasks=[]), "subtasks"),
    (TriageReport, _triage(mode="implement", subtasks=[_new_task()]), "subtasks"),
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


_VALID_CASES: list[tuple[type[BaseModel], dict[str, Any]]] = [
    (TriageReport, _triage(mode="split", subtasks=[_new_task()])),
    (ImplementationReport, _implementation(status="completed", question="")),
    (ConsultAnswer, _consult(decision="answer", answer="Use A")),
    (ConsultAnswer, {"decision": "escalate", "escalation": EscalationRequest(**_escalation())}),
    (Verdict, _verdict(decision="accept")),
    (
        Verdict,
        _verdict(
            decision="changes_requested",
            issues=[{"severity": "major", "description": "x"}],
        ),
    ),
    (Verdict, _verdict(decision="supersede", new_tasks=[_new_task()])),
    (Verdict, {"decision": "escalate", "summary": "Looks good", "escalation": _escalation()}),
]


@pytest.mark.parametrize(
    ("model", "data"),
    _VALID_CASES,
    ids=[
        f"{model.__name__}-{data.get('decision', data.get('mode', 'default'))}"
        for model, data in _VALID_CASES
    ],
)
def test_valid_reports_cover_every_decision_branch(
    model: type[BaseModel], data: dict[str, Any]
) -> None:
    model(**data)


_EXAMPLES: dict[ReportKind, dict[str, Any]] = {
    ReportKind.TRIAGE: _triage(),
    ReportKind.IMPLEMENTATION: _implementation(),
    ReportKind.PLAN: {"summary": "Planned", "commits": []},
    ReportKind.CONSULT: _consult(),
    ReportKind.VERDICT: _verdict(),
}


@pytest.mark.parametrize("kind", list(ReportKind))
def test_every_report_kind_maps_to_a_model_whose_schema_validates_an_example(
    kind: ReportKind,
) -> None:
    schema = schema_for(kind)
    model = model_for(kind)

    assert schema["title"] == model.__name__
    model(**_EXAMPLES[kind])
