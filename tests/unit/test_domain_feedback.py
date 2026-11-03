from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from shikiru.domain.feedback import (
    FeedbackContractError,
    FeedbackInput,
    FeedbackMessage,
    FeedbackSnapshot,
    FeedbackSource,
    FeedbackSourceKind,
    FeedbackStep,
    check_feedback_report,
    next_feedback_step,
    operator_questions,
    publishable_replies,
)
from shikiru.domain.reports import FeedbackAnalysis, FeedbackVerdict

REVIEW = "review-1"
THREAD = "thread-1"
DUPLICATE = "thread-2"
SOURCE_IDS = [REVIEW, THREAD, DUPLICATE]


def _source(source_id: str, kind: FeedbackSourceKind) -> FeedbackSource:
    return FeedbackSource(
        id=source_id,
        kind=kind,
        url=f"https://github.test/{source_id}",
        messages=[FeedbackMessage(author="reviewer", body="Please change this", url="u")],
    )


def _snapshot(*source_ids: str) -> FeedbackSnapshot:
    return FeedbackSnapshot(
        pull_request_url="https://github.test/pr/1",
        head_sha="abc",
        sources=[
            _source(
                source_id,
                FeedbackSourceKind.REVIEW_BODY
                if source_id == REVIEW
                else FeedbackSourceKind.DISCUSSION,
            )
            for source_id in source_ids
        ],
    )


def _decision(source_id: str, action: str, **overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "source_id": source_id,
        "action": action,
        "points": ["the point"],
        "detail": "the detail",
        "reply": "the reply",
    }
    if action == "address":
        fields["work_id"] = "w1"
    if action == "needs_operator":
        fields["reply"] = ""
    fields.update(overrides)
    return fields


def _analysis(*decisions: dict[str, Any], work: bool | None = None) -> FeedbackAnalysis:
    needs_work = any(d["action"] == "address" for d in decisions) if work is None else work
    return FeedbackAnalysis.model_validate(
        {
            "summary": "analysed",
            "work": [{"id": "w1", "instruction": "do it"}] if needs_work else [],
            "decisions": list(decisions),
        }
    )


def _verdict(decision: str, *reply_ids: str) -> FeedbackVerdict:
    fields: dict[str, Any] = {
        "decision": decision,
        "summary": "checked",
        "replies": [{"source_id": source_id, "reply": "final"} for source_id in reply_ids],
    }
    if decision == "changes_requested":
        fields["issues"] = [{"severity": "major", "description": "missing"}]
    return FeedbackVerdict.model_validate(fields)


def _check(snapshot: FeedbackSnapshot, analysis: FeedbackAnalysis) -> None:
    check_feedback_report(FeedbackInput(snapshot=snapshot), analysis)


def test_a_decision_for_every_source_exactly_once_is_complete() -> None:
    analysis = _analysis(
        _decision(REVIEW, "disagree"),
        _decision(THREAD, "address"),
        _decision(DUPLICATE, "address"),
    )

    _check(_snapshot(*SOURCE_IDS), analysis)

    assert [work.id for work in analysis.work] == ["w1"]


def test_a_body_only_review_is_complete_with_one_decision() -> None:
    _check(_snapshot(REVIEW), _analysis(_decision(REVIEW, "already_addressed")))


@pytest.mark.parametrize(
    ("decided", "expected"),
    [
        ([REVIEW, "thread-9"], "unknown"),
        ([REVIEW], "without a decision"),
    ],
    ids=["unknown-id", "missing-item"],
)
def test_decisions_must_name_exactly_the_snapshot_sources(
    decided: list[str], expected: str
) -> None:
    analysis = _analysis(*(_decision(source_id, "disagree") for source_id in decided))

    with pytest.raises(FeedbackContractError, match=expected):
        _check(_snapshot(REVIEW, THREAD), analysis)


@pytest.mark.parametrize(
    "decisions",
    [
        [_decision(REVIEW, "disagree"), _decision(REVIEW, "disagree")],
        [_decision(REVIEW, "address", work_id="")],
        [_decision(REVIEW, "address", work_id="w2")],
        [_decision(REVIEW, "disagree", reply="")],
        [_decision(REVIEW, "disagree", points=[])],
        [_decision(REVIEW, "needs_operator", reply="answer anyway")],
        [_decision(REVIEW, "disagree", work_id="w1")],
    ],
    ids=[
        "duplicate-decision",
        "address-without-work",
        "address-unknown-work",
        "no-reply",
        "no-points",
        "operator-reply",
        "work-without-address",
    ],
)
def test_malformed_decisions_are_rejected(decisions: list[dict[str, Any]]) -> None:
    with pytest.raises(ValidationError):
        _analysis(*decisions, work=True)


def test_work_nobody_asked_for_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _analysis(_decision(REVIEW, "disagree"), work=True)


def test_duplicate_requests_share_work_and_keep_their_own_replies() -> None:
    analysis = _analysis(
        _decision(THREAD, "address", reply="Fixed in the first thread"),
        _decision(DUPLICATE, "address", reply="Same fix, see above"),
    )

    assert len(analysis.work) == 1
    assert {decision.reply for decision in analysis.decisions} == {
        "Fixed in the first thread",
        "Same fix, see above",
    }


_STEPS = {
    "work": (
        [_decision(THREAD, "address"), _decision(REVIEW, "disagree")],
        FeedbackStep.IMPLEMENT,
    ),
    "no-change": (
        [_decision(THREAD, "disagree"), _decision(REVIEW, "already_addressed")],
        FeedbackStep.VERIFY,
    ),
    "operator-beats-work": (
        [_decision(THREAD, "address"), _decision(REVIEW, "needs_operator")],
        FeedbackStep.ESCALATE,
    ),
    "operator-beats-disagreement": (
        [_decision(THREAD, "disagree"), _decision(REVIEW, "needs_operator")],
        FeedbackStep.ESCALATE,
    ),
}


@pytest.mark.parametrize(("decisions", "expected"), _STEPS.values(), ids=_STEPS.keys())
def test_next_step_follows_the_decisions(
    decisions: list[dict[str, Any]], expected: FeedbackStep
) -> None:
    assert next_feedback_step(_analysis(*decisions)) is expected


def test_needs_operator_keeps_the_source_and_question() -> None:
    analysis = _analysis(
        _decision(REVIEW, "needs_operator", detail="Drop the cache or keep it?"),
        _decision(THREAD, "disagree"),
    )

    assert operator_questions(analysis) == [(REVIEW, "Drop the cache or keep it?")]


def _verified_input(*decisions: dict[str, Any]) -> tuple[FeedbackInput, FeedbackAnalysis]:
    analysis = _analysis(*decisions)
    snapshot = _snapshot(*(decision["source_id"] for decision in decisions))
    return FeedbackInput(snapshot=snapshot, analysis=analysis), analysis


def test_an_accepting_verdict_must_verify_the_reply_of_every_decided_source() -> None:
    feedback, _ = _verified_input(_decision(REVIEW, "disagree"), _decision(THREAD, "address"))

    check_feedback_report(feedback, _verdict("accept", REVIEW, THREAD))
    with pytest.raises(FeedbackContractError, match="replies"):
        check_feedback_report(feedback, _verdict("accept", REVIEW))
    with pytest.raises(FeedbackContractError, match="replies"):
        check_feedback_report(feedback, _verdict("accept", REVIEW, THREAD, DUPLICATE))


def test_a_verdict_cannot_accept_while_a_decision_needs_the_operator() -> None:
    feedback, _ = _verified_input(
        _decision(REVIEW, "needs_operator"), _decision(THREAD, "disagree")
    )

    with pytest.raises(FeedbackContractError, match="Operator"):
        check_feedback_report(feedback, _verdict("accept", THREAD))


def test_a_verdict_without_the_analysis_it_verifies_is_rejected() -> None:
    feedback = FeedbackInput(snapshot=_snapshot(REVIEW))

    with pytest.raises(FeedbackContractError, match="analysis"):
        check_feedback_report(feedback, _verdict("accept", REVIEW))


def test_only_an_accepted_round_publishes_its_replies() -> None:
    feedback, analysis = _verified_input(_decision(REVIEW, "disagree"))

    assert publishable_replies(feedback.snapshot, analysis, _verdict("accept", REVIEW)) == {
        REVIEW: "final"
    }
    with pytest.raises(FeedbackContractError, match="publishable"):
        publishable_replies(feedback.snapshot, analysis, _verdict("changes_requested"))


@pytest.mark.parametrize(
    "fields",
    [
        {"decision": "supersede", "new_tasks": [{"title": "t", "request": "r"}]},
        {"decision": "accept", "replies": []},
        {"decision": "changes_requested", "replies": [{"source_id": "a", "reply": "b"}]},
    ],
    ids=["supersede", "accept-without-replies", "replies-without-accept"],
)
def test_a_feedback_verdict_rejects_unavailable_shapes(fields: dict[str, Any]) -> None:
    fields = {"summary": "checked", "issues": [{"severity": "major", "description": "x"}], **fields}
    if fields["decision"] != "changes_requested":
        fields.pop("issues")

    with pytest.raises(ValidationError):
        FeedbackVerdict.model_validate(fields)
