from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from shikiru.domain.reports import (
    DEPENDS_ON_PATTERN,
    TRIAGE_TITLE_MAX_LENGTH,
    ConsultAnswer,
    EscalationRequest,
    ImplementationReport,
    NewTask,
    Option,
    PlanReport,
    Report,
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


@pytest.mark.parametrize("dependency", ["T-1", "#2"])
def test_new_task_accepts_valid_dependency_references(dependency: str) -> None:
    NewTask(**_new_task(depends_on=[dependency]))


def test_new_task_rejects_a_malformed_dependency_reference() -> None:
    with pytest.raises(ValidationError, match="depends_on"):
        NewTask(**_new_task(depends_on=["task-1"]))


def test_escalation_request_accepts_exactly_one_recommended_option() -> None:
    EscalationRequest(**_escalation())


def test_escalation_request_rejects_zero_recommended_options() -> None:
    options = [Option(label="A", consequences="x", recommended=False)]
    with pytest.raises(ValidationError, match="options"):
        EscalationRequest(question="Q", options=options)


def test_escalation_request_rejects_more_than_one_recommended_option() -> None:
    options = [
        Option(label="A", consequences="x", recommended=True),
        Option(label="B", consequences="y", recommended=True),
    ]
    with pytest.raises(ValidationError, match="options"):
        EscalationRequest(question="Q", options=options)


def test_triage_report_accepts_a_title_at_the_length_limit() -> None:
    TriageReport(**_triage(title="x" * 60))


def test_triage_report_rejects_a_title_over_the_length_limit() -> None:
    with pytest.raises(ValidationError, match="title"):
        TriageReport(**_triage(title="x" * 61))


def test_triage_report_split_requires_subtasks() -> None:
    TriageReport(**_triage(mode="split", subtasks=[_new_task()]))


def test_triage_report_split_without_subtasks_is_rejected() -> None:
    with pytest.raises(ValidationError, match="subtasks"):
        TriageReport(**_triage(mode="split", subtasks=[]))


def test_triage_report_non_split_with_subtasks_is_rejected() -> None:
    with pytest.raises(ValidationError, match="subtasks"):
        TriageReport(**_triage(mode="implement", subtasks=[_new_task()]))


def test_implementation_report_completed_needs_no_question() -> None:
    ImplementationReport(**_implementation(status="completed", question=""))


def test_implementation_report_needs_decision_without_question_is_rejected() -> None:
    with pytest.raises(ValidationError, match="question"):
        ImplementationReport(**_implementation(status="needs_decision", question=""))


def test_consult_answer_answer_requires_the_answer_text() -> None:
    ConsultAnswer(**_consult(decision="answer", answer="Use A"))


def test_consult_answer_missing_answer_text_is_rejected() -> None:
    with pytest.raises(ValidationError, match="answer"):
        ConsultAnswer(decision="answer", answer="")


def test_consult_answer_answer_with_escalation_set_is_rejected() -> None:
    escalation = EscalationRequest(**_escalation())
    with pytest.raises(ValidationError, match="escalation"):
        ConsultAnswer(decision="answer", answer="Use A", escalation=escalation)


def test_consult_answer_escalate_requires_an_escalation() -> None:
    ConsultAnswer(decision="escalate", escalation=EscalationRequest(**_escalation()))


def test_consult_answer_escalate_without_an_escalation_is_rejected() -> None:
    with pytest.raises(ValidationError, match="escalation"):
        ConsultAnswer(decision="escalate")


def test_consult_answer_escalate_with_answer_text_is_rejected() -> None:
    escalation = EscalationRequest(**_escalation())
    with pytest.raises(ValidationError, match="answer"):
        ConsultAnswer(decision="escalate", answer="Use A", escalation=escalation)


def test_verdict_changes_requested_requires_issues() -> None:
    issues = [{"severity": "major", "description": "x"}]
    Verdict(**_verdict(decision="changes_requested", issues=issues))


def test_verdict_changes_requested_without_issues_is_rejected() -> None:
    with pytest.raises(ValidationError, match="issues"):
        Verdict(**_verdict(decision="changes_requested", issues=[]))


def test_verdict_accept_with_issues_is_rejected() -> None:
    with pytest.raises(ValidationError, match="issues"):
        Verdict(**_verdict(decision="accept", issues=[{"severity": "minor", "description": "x"}]))


def test_verdict_supersede_requires_new_tasks() -> None:
    Verdict(**_verdict(decision="supersede", new_tasks=[_new_task()]))


def test_verdict_supersede_without_new_tasks_is_rejected() -> None:
    with pytest.raises(ValidationError, match="new_tasks"):
        Verdict(**_verdict(decision="supersede", new_tasks=[]))


def test_verdict_accept_with_new_tasks_is_rejected() -> None:
    with pytest.raises(ValidationError, match="new_tasks"):
        Verdict(**_verdict(decision="accept", new_tasks=[_new_task()]))


def test_verdict_accept_with_escalation_set_is_rejected() -> None:
    with pytest.raises(ValidationError, match="escalation"):
        Verdict(**_verdict(decision="accept", escalation=_escalation()))


def test_verdict_escalate_requires_an_escalation() -> None:
    Verdict(**_verdict(decision="escalate", escalation=_escalation()))


def test_verdict_escalate_without_an_escalation_is_rejected() -> None:
    with pytest.raises(ValidationError, match="escalation"):
        Verdict(**_verdict(decision="escalate"))


_EXAMPLES: dict[ReportKind, dict[str, Any]] = {
    ReportKind.TRIAGE: _triage(),
    ReportKind.IMPLEMENTATION: _implementation(),
    ReportKind.PLAN: {"summary": "Planned", "commits": []},
    ReportKind.CONSULT: _consult(),
    ReportKind.VERDICT: _verdict(),
}


@pytest.mark.parametrize("kind", list(ReportKind))
def test_schema_for_returns_a_json_schema(kind: ReportKind) -> None:
    schema = schema_for(kind)

    assert schema["title"] == model_for(kind).__name__


@pytest.mark.parametrize("kind", list(ReportKind))
def test_report_examples_round_trip_through_their_model(kind: ReportKind) -> None:
    model = model_for(kind)
    instance: Report = model(**_EXAMPLES[kind])

    restored = model(**instance.model_dump(mode="json"))

    assert restored == instance


def test_plan_report_has_no_extra_iff_constraints() -> None:
    new_task = NewTask(**_new_task())
    PlanReport(summary="Planned", commits=[], new_tasks=[new_task], scope_changes=["scope"])


def test_triage_schema_exposes_the_title_length_limit() -> None:
    schema = schema_for(ReportKind.TRIAGE)

    assert schema["properties"]["title"]["maxLength"] == TRIAGE_TITLE_MAX_LENGTH


def test_triage_schema_describes_the_subtasks_iff_rule() -> None:
    schema = schema_for(ReportKind.TRIAGE)

    assert "split" in schema["properties"]["subtasks"]["description"]


def test_new_task_schema_exposes_the_depends_on_pattern() -> None:
    schema = schema_for(ReportKind.TRIAGE)
    new_task_schema = schema["$defs"]["NewTask"]

    assert new_task_schema["properties"]["depends_on"]["items"]["pattern"] == DEPENDS_ON_PATTERN


def test_implementation_schema_describes_the_question_rule() -> None:
    schema = schema_for(ReportKind.IMPLEMENTATION)

    assert "completed" in schema["properties"]["question"]["description"]


def test_consult_schema_describes_the_answer_and_escalation_rules() -> None:
    schema = schema_for(ReportKind.CONSULT)

    assert "answer" in schema["properties"]["answer"]["description"]
    assert "escalate" in schema["properties"]["escalation"]["description"]


def test_verdict_schema_describes_its_iff_rules() -> None:
    schema = schema_for(ReportKind.VERDICT)

    assert "changes_requested" in schema["properties"]["issues"]["description"]
    assert "supersede" in schema["properties"]["new_tasks"]["description"]
    assert "escalate" in schema["properties"]["escalation"]["description"]


def test_escalation_request_schema_describes_the_recommended_option_rule() -> None:
    schema = schema_for(ReportKind.CONSULT)
    escalation_schema = schema["$defs"]["EscalationRequest"]

    assert "recommended" in escalation_schema["properties"]["options"]["description"]
