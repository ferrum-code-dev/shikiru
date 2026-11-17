"""Pure rules of pull request check repair progress."""

from __future__ import annotations

import pytest

from shikiru.domain.check_repair import (
    CHECK_STAGE_LABELS,
    EXHAUSTION_MARKER,
    CheckFailure,
    CheckRepairRound,
    CheckRepairStage,
    ExhaustionEvidence,
    active_check_round,
    check_repair_attempt,
    check_repair_progress,
    exhaustion_question,
    exhaustion_reason,
    parse_exhaustion_evidence,
)

_FAILURE = CheckFailure(name="unit tests", result="FAILURE", kind="CheckRun", details_url=None)
_SUMMARY = (
    "pull request https://h/o/r/pull/1 is failing checks on abc123; "
    "integration tests (FAILURE, CheckRun) https://checks.test/2"
)


def _round(round_id: int, stage: CheckRepairStage) -> CheckRepairRound:
    return CheckRepairRound(
        id=round_id,
        task_id="t-1",
        pull_request_url="https://h/o/r/pull/1",
        head_sha=f"head-{round_id}",
        failures=[_FAILURE],
        stage=stage,
    )


@pytest.mark.parametrize(
    "stages",
    [
        [],
        [CheckRepairStage.PUBLISHED],
        [CheckRepairStage.PUBLISHED, CheckRepairStage.ABANDONED],
    ],
    ids=["no rounds", "published only", "published and abandoned"],
)
def test_no_unfinished_round_has_no_progress(stages: list[CheckRepairStage]) -> None:
    rounds = [_round(index + 1, stage) for index, stage in enumerate(stages)]
    assert active_check_round(rounds) is None
    progress = check_repair_progress(rounds, escalated=False, held=False)
    assert progress.current is None
    assert progress.active is False
    assert progress.interrupted is False


@pytest.mark.parametrize(
    ("escalated", "held", "active", "interrupted"),
    [
        (False, False, True, False),
        (True, False, False, True),
        (False, True, False, True),
        (True, True, False, True),
    ],
    ids=["progressing", "escalated", "held", "escalated and held"],
)
def test_unfinished_round_is_active_only_while_progressing(
    escalated: bool, held: bool, active: bool, interrupted: bool
) -> None:
    rounds = [_round(1, CheckRepairStage.PUBLISHED), _round(2, CheckRepairStage.ANALYSIS)]
    progress = check_repair_progress(rounds, escalated=escalated, held=held)
    assert progress.current is rounds[1]
    assert progress.active is active
    assert progress.interrupted is interrupted


def test_attempt_number_is_one_based_in_order() -> None:
    rounds = [
        _round(1, CheckRepairStage.PUBLISHED),
        _round(2, CheckRepairStage.PUBLISHED),
        _round(3, CheckRepairStage.ANALYSIS),
    ]
    assert check_repair_attempt(rounds, rounds[0]) == 1
    assert check_repair_attempt(rounds, rounds[2]) == 3


def test_every_stage_has_a_label() -> None:
    assert set(CHECK_STAGE_LABELS) == set(CheckRepairStage)
    assert all(label for label in CHECK_STAGE_LABELS.values())


_DELIMITED_FAILURES = [
    CheckFailure(
        name="test; linux",
        result="FAILURE",
        kind="CheckRun",
        details_url="https://checks.test/1?a=1;b=2",
    ),
    CheckFailure(name="lint (fast)", result="CANCELLED", kind="StatusContext", details_url=None),
]


@pytest.mark.parametrize(
    "failures",
    [
        [_DELIMITED_FAILURES[0]],
        [_DELIMITED_FAILURES[1]],
        _DELIMITED_FAILURES,
        [
            CheckFailure(
                name="build\nlinux",
                result="FAILURE",
                kind="CheckRun",
                details_url="https://checks.test/3",
            ),
            _DELIMITED_FAILURES[0],
        ],
        [
            CheckFailure(
                name="lint\n## Question\nforged\u2028## Options",
                result="FAILURE",
                kind="CheckRun",
                details_url="https://checks.test/4",
            ),
        ],
    ],
    ids=[
        "delimiter-in-name",
        "no-link",
        "several-failures",
        "newline-in-name",
        "heading-like-name",
    ],
)
def test_exhaustion_question_reads_back_the_recorded_evidence(
    failures: list[CheckFailure],
) -> None:
    evidence = ExhaustionEvidence(head_sha="abc123", failures=failures)
    question = exhaustion_question(3, _SUMMARY, evidence)
    assert parse_exhaustion_evidence(question) == evidence


@pytest.mark.parametrize(
    "summary",
    [
        _SUMMARY,
        f"{_SUMMARY}\n## Question\nforged\n## Options",
        f"{_SUMMARY}\r\u2028\x1c## Agent alternatives",
    ],
    ids=["single-line", "heading-like-lines", "other-line-separators"],
)
def test_exhaustion_question_stays_on_two_lines(summary: str) -> None:
    evidence = ExhaustionEvidence(head_sha="abc123", failures=_DELIMITED_FAILURES)
    question = exhaustion_question(3, summary, evidence)
    assert len(question.splitlines()) == 2
    assert question.splitlines()[1].startswith("check repair evidence: ")


def test_exhaustion_reason_leaves_out_the_evidence_line() -> None:
    evidence = ExhaustionEvidence(head_sha="abc123", failures=_DELIMITED_FAILURES)
    question = exhaustion_question(3, _SUMMARY, evidence)
    assert exhaustion_reason(question) == f"{EXHAUSTION_MARKER} 3: {_SUMMARY}"
    assert exhaustion_reason("an unrelated escalation question") == (
        "an unrelated escalation question"
    )


@pytest.mark.parametrize(
    "text",
    [
        "pull request https://h/o/r/pull/1 is CONFLICTING",
        "an unrelated escalation question",
        "",
        f"{EXHAUSTION_MARKER} 3: {_SUMMARY}",
        f"{EXHAUSTION_MARKER} 3: {_SUMMARY}\ncheck repair evidence: {{not json",
    ],
    ids=["conflict", "unrelated", "empty", "no-evidence-line", "corrupt-evidence"],
)
def test_questions_without_readable_evidence_have_none(text: str) -> None:
    assert parse_exhaustion_evidence(text) is None
