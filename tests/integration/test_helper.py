"""`Helper` against the fake agent's headless script (04 §6, 07 §3)."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest
from fake_agent.agent import create

from shikiru.agents.contracts import AgentContext, CheckResult, SessionKeys
from shikiru.clock import SystemClock
from shikiru.domain.recovery import ScreenClassification, ScreenState
from shikiru.domain.reports import ReportKind, TriageReport
from shikiru.errors import ConfigError
from shikiru.helper.helper import Helper
from shikiru.helper.models import Unrecoverable
from shikiru.process import SubprocessRunner

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from shikiru.agents.contracts import (
        AgentSession,
        HeadlessAgent,
        InteractiveAgent,
        LaunchSpec,
        PaneHandle,
        QuotaWindow,
    )

_TIMEOUT = timedelta(seconds=30)
_VALID_TRIAGE: dict[str, Any] = {
    "title": "Add retries",
    "mode": "implement",
    "complexity": "normal",
    "acceptance_criteria": ["retries on failure"],
    "references": [],
}


def _agent(asks: list[dict[str, Any]]) -> InteractiveAgent:
    context = AgentContext(options={"asks": asks}, runner=SubprocessRunner(), clock=SystemClock())
    return create(context)


class _NoHeadlessAgent:
    """An `InteractiveAgent` stub whose `headless()` is unsupported, for the rejection test."""

    name = "no-headless"
    keys = SessionKeys(interrupt=(), exit_input="", safe=())

    async def launch(self, spec: LaunchSpec, pane: PaneHandle) -> AgentSession:
        raise NotImplementedError

    async def attach(
        self, spec: LaunchSpec, pane: PaneHandle, state: Mapping[str, Any]
    ) -> AgentSession:
        raise NotImplementedError

    async def quota(self) -> list[QuotaWindow]:
        return []

    async def doctor(self) -> list[CheckResult]:
        return []

    def resume_command(self, session_id: str, cwd: Path) -> str | None:
        del session_id, cwd
        return None

    def headless(self) -> HeadlessAgent | None:
        return None


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


async def test_rebuild_report_rejects_an_invalid_answer(tmp_path: Path) -> None:
    helper = Helper(_agent([{"title": "missing every other required field"}]))

    result = await helper.rebuild_report(
        ReportKind.TRIAGE,
        "agent's last message",
        "facts",
        model="haiku",
        effort="low",
        cwd=tmp_path,
        timeout=_TIMEOUT,
    )

    assert isinstance(result, Unrecoverable)
    assert "mode" in result.unrecoverable


async def test_rebuild_report_passes_through_an_explicit_unrecoverable_answer(
    tmp_path: Path,
) -> None:
    helper = Helper(_agent([{"unrecoverable": "the transcript is empty"}]))

    result = await helper.rebuild_report(
        ReportKind.TRIAGE, "", "", model="haiku", effort="low", cwd=tmp_path, timeout=_TIMEOUT
    )

    assert result == Unrecoverable(unrecoverable="the transcript is empty")


async def test_classify_screen_accepts_a_valid_answer(tmp_path: Path) -> None:
    helper = Helper(_agent([{"state": "working"}]))

    result = await helper.classify_screen(
        "Thinking...",
        ("Enter",),
        model="haiku",
        effort="low",
        cwd=tmp_path,
        timeout=_TIMEOUT,
    )

    assert isinstance(result, ScreenClassification)
    assert result.state is ScreenState.WORKING


async def test_classify_screen_rejects_an_invalid_answer(tmp_path: Path) -> None:
    helper = Helper(_agent([{"state": "not-a-real-state"}]))

    result = await helper.classify_screen(
        "???", ("Enter",), model="haiku", effort="low", cwd=tmp_path, timeout=_TIMEOUT
    )

    assert isinstance(result, Unrecoverable)


async def test_classify_screen_passes_dialog_keys_through_uninterpreted(tmp_path: Path) -> None:
    helper = Helper(_agent([{"state": "dialog", "keys": ["Enter", "y"]}]))

    result = await helper.classify_screen(
        "Overwrite? (y/n)",
        ["Enter"],
        model="haiku",
        effort="low",
        cwd=tmp_path,
        timeout=_TIMEOUT,
    )

    assert isinstance(result, ScreenClassification)
    assert result.keys == ["Enter", "y"]


def test_construction_rejects_an_agent_with_no_headless_support() -> None:
    with pytest.raises(ConfigError):
        Helper(_NoHeadlessAgent())
