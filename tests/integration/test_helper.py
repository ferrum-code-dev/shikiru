from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest
from fake_agent.agent import create

from shikiru.agents.contracts import AgentContext, FailureKind
from shikiru.clock import SystemClock
from shikiru.domain.reports import ReportKind, TriageReport
from shikiru.errors import ConfigError
from shikiru.helper.helper import FailureClassification, Helper
from shikiru.helper.models import Unrecoverable
from shikiru.process import SubprocessRunner

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from shikiru.agents.contracts import Agent, CheckResult, Turn, TurnSpec

_TIMEOUT = timedelta(seconds=30)
_VALID_TRIAGE: dict[str, Any] = {
    "title": "Add retries",
    "mode": "implement",
    "complexity": "normal",
    "acceptance_criteria": ["retries on failure"],
    "references": [],
}


def _agent(asks: list[dict[str, Any]]) -> Agent:
    context = AgentContext(options={"asks": asks}, runner=SubprocessRunner(), clock=SystemClock())
    return create(context)


async def test_rebuild_report_accepts_a_valid_answer(tmp_path: Path) -> None:
    helper = Helper(_agent([_VALID_TRIAGE]))

    result = await helper.rebuild_report(
        ReportKind.TRIAGE,
        "agent's last message",
        "facts",
        model="haiku",
        effort="low",
        cwd=tmp_path,
        timeout=_TIMEOUT,
    )

    assert isinstance(result, TriageReport)
    assert result.title == "Add retries"


async def test_rebuild_report_returns_an_explicit_unrecoverable_result(tmp_path: Path) -> None:
    helper = Helper(_agent([{"unrecoverable": "the transcript is empty"}]))

    result = await helper.rebuild_report(
        ReportKind.TRIAGE, "", "", model="haiku", effort="low", cwd=tmp_path, timeout=_TIMEOUT
    )

    assert result == Unrecoverable(unrecoverable="the transcript is empty")


async def test_classify_failure_validates_and_preserves_the_classification(
    tmp_path: Path,
) -> None:
    helper = Helper(_agent([{"kind": "quota", "confidence": 0.91, "resets_at": None}]))

    result = await helper.classify_failure(
        "rate limit",
        model="haiku",
        effort="low",
        cwd=tmp_path,
        timeout=_TIMEOUT,
    )

    assert result == FailureClassification(kind=FailureKind.QUOTA, confidence=0.91)


async def test_classify_failure_marks_an_ambiguous_answer_unrecoverable(
    tmp_path: Path,
) -> None:
    helper = Helper(_agent([{"kind": "not-a-failure-kind", "confidence": 0.9}]))

    result = await helper.classify_failure(
        "unclear",
        model="haiku",
        effort="low",
        cwd=tmp_path,
        timeout=_TIMEOUT,
    )

    assert isinstance(result, Unrecoverable)


def test_helper_requires_headless_support() -> None:
    with pytest.raises(ConfigError, match="no headless support"):
        Helper(_AgentWithoutHeadless())


class _AgentWithoutHeadless:
    name = "unsupported"

    async def start_turn(self, spec: TurnSpec) -> Turn:
        del spec
        raise AssertionError

    def interactive_command(self, session_id: str, cwd: Path) -> Sequence[str]:
        del session_id, cwd
        return ()

    async def doctor(self) -> list[CheckResult]:
        return []

    def headless(self) -> None:
        return None
