from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING, Any, override

import pytest
from fake_agent.agent import create

from shikiru.agents.contracts import AgentContext, FailureKind, HeadlessAgent, Rung
from shikiru.config.loader import load_settings
from shikiru.domain.reports import ReportKind, TriageReport
from shikiru.errors import ConfigError
from shikiru.helper.helper import FailureClassification, Helper, HelperEntry
from shikiru.helper.models import Unrecoverable
from shikiru.process import SubprocessRunner
from tests.support.clock import FakeClock
from tests.support.config import MINIMAL_PROJECT_CONFIG
from tests.support.factories import HELPER_RUNG, TASK_CREATED_AT

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from shikiru.agents.contracts import Agent, CheckResult, Turn, TurnSpec
    from shikiru.clock import Clock
    from shikiru.config.settings import Settings

_TIMEOUT = timedelta(seconds=30)
_LADDER_TIMEOUT_SECONDS = 10
_FIRST_RUNG_SECONDS = 7
_SECOND_RUNG_SECONDS = 3
_VALID_TRIAGE: dict[str, Any] = {
    "title": "Add retries",
    "mode": "implement",
    "complexity": "normal",
    "acceptance_criteria": ["retries on failure"],
    "references": [],
}


def _agent(asks: list[dict[str, Any]], clock: Clock) -> Agent:
    context = AgentContext(options={"asks": asks}, runner=SubprocessRunner(), clock=clock)
    return create(context)


async def test_rebuild_report_accepts_a_valid_answer(tmp_path: Path) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    helper = Helper([HelperEntry(HELPER_RUNG, _agent([_VALID_TRIAGE], clock))], clock)

    result = await helper.rebuild_report(
        ReportKind.TRIAGE,
        "agent's last message",
        "facts",
        cwd=tmp_path,
        timeout=_TIMEOUT,
    )

    assert isinstance(result, TriageReport)
    assert result.title == "Add retries"


async def test_rebuild_report_returns_an_explicit_unrecoverable_result(tmp_path: Path) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    helper = Helper(
        [HelperEntry(HELPER_RUNG, _agent([{"unrecoverable": "the transcript is empty"}], clock))],
        clock,
    )

    result = await helper.rebuild_report(ReportKind.TRIAGE, "", "", cwd=tmp_path, timeout=_TIMEOUT)

    assert isinstance(result, Unrecoverable)
    assert "the transcript is empty" in result.unrecoverable


async def test_classify_failure_validates_and_preserves_the_classification(
    tmp_path: Path,
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    helper = Helper(
        [
            HelperEntry(
                HELPER_RUNG,
                _agent([{"kind": "quota", "confidence": 0.91, "resets_at": None}], clock),
            )
        ],
        clock,
    )

    result = await helper.classify_failure(
        "rate limit",
        cwd=tmp_path,
        timeout=_TIMEOUT,
    )

    assert result == FailureClassification(kind=FailureKind.QUOTA, confidence=0.91)


async def test_classify_failure_marks_an_ambiguous_answer_unrecoverable(
    tmp_path: Path,
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    helper = Helper(
        [
            HelperEntry(
                HELPER_RUNG,
                _agent([{"kind": "not-a-failure-kind", "confidence": 0.9}], clock),
            )
        ],
        clock,
    )

    result = await helper.classify_failure(
        "unclear",
        cwd=tmp_path,
        timeout=_TIMEOUT,
    )

    assert isinstance(result, Unrecoverable)


@pytest.mark.parametrize(
    "first_answer",
    [
        pytest.param(TimeoutError("provider failed"), id="failed-call"),
        pytest.param({"unrecoverable": "not enough evidence"}, id="explicit-unrecoverable"),
        pytest.param({"title": "incomplete"}, id="invalid-answer"),
    ],
)
async def test_rebuild_falls_through_to_the_next_configured_provider(
    tmp_path: Path, first_answer: Mapping[str, Any] | Exception
) -> None:
    settings = _settings_with_helper_ladder(tmp_path)
    first = _ScriptedHeadless([first_answer])
    second = _ScriptedHeadless([_VALID_TRIAGE])
    helper = _helper(settings, [first, second], FakeClock(TASK_CREATED_AT))

    result = await helper.rebuild_report(
        ReportKind.TRIAGE, "last message", "facts", cwd=tmp_path, timeout=_TIMEOUT
    )

    assert isinstance(result, TriageReport)
    assert [call[:2] for call in first.calls + second.calls] == [
        ("haiku", "low"),
        ("sol", "high"),
    ]


async def test_classification_stops_on_a_valid_unknown_answer(tmp_path: Path) -> None:
    settings = _settings_with_helper_ladder(tmp_path)
    first = _ScriptedHeadless([{"kind": "unknown", "confidence": 0.1}])
    second = _ScriptedHeadless([{"kind": "quota", "confidence": 0.99}])
    helper = _helper(settings, [first, second], FakeClock(TASK_CREATED_AT))

    result = await helper.classify_failure("unclear", cwd=tmp_path, timeout=_TIMEOUT)

    assert result == FailureClassification(kind=FailureKind.UNKNOWN, confidence=0.1)
    assert len(first.calls) == 1
    assert second.calls == []


async def test_classification_falls_through_an_unrecoverable_answer(tmp_path: Path) -> None:
    settings = _settings_with_helper_ladder(tmp_path)
    first = _ScriptedHeadless([{"unrecoverable": "failure text is incomplete"}])
    second = _ScriptedHeadless([{"kind": "quota", "confidence": 0.91}])
    helper = _helper(settings, [first, second], FakeClock(TASK_CREATED_AT))

    result = await helper.classify_failure("rate limit", cwd=tmp_path, timeout=_TIMEOUT)

    assert result == FailureClassification(kind=FailureKind.QUOTA, confidence=0.91)
    assert len(first.calls) == 1
    assert len(second.calls) == 1


async def test_exhaustion_returns_unrecoverable_after_every_entry_fails(tmp_path: Path) -> None:
    settings = _settings_with_helper_ladder(tmp_path)
    first = _ScriptedHeadless([TimeoutError("provider failed")])
    second = _ScriptedHeadless([{"title": "incomplete"}])
    helper = _helper(settings, [first, second], FakeClock(TASK_CREATED_AT))

    result = await helper.rebuild_report(
        ReportKind.TRIAGE, "last message", "facts", cwd=tmp_path, timeout=_TIMEOUT
    )

    assert isinstance(result, Unrecoverable)
    assert "provider failed" in result.unrecoverable
    assert "incomplete" in result.unrecoverable
    assert len(first.calls) == 1
    assert len(second.calls) == 1


@pytest.mark.parametrize(
    ("elapsed_seconds", "expected_second_timeout"),
    [
        pytest.param(_FIRST_RUNG_SECONDS, _SECOND_RUNG_SECONDS, id="remaining-budget"),
        pytest.param(_LADDER_TIMEOUT_SECONDS, None, id="budget-exhausted"),
    ],
)
async def test_helper_timeout_caps_the_complete_ladder(
    tmp_path: Path, elapsed_seconds: int, expected_second_timeout: int | None
) -> None:
    settings = _settings_with_helper_ladder(tmp_path)
    clock = FakeClock(TASK_CREATED_AT)
    first = _AdvancingHeadless(
        [{"unrecoverable": "not enough evidence"}],
        clock,
        elapsed_seconds,
    )
    second = _ScriptedHeadless([_VALID_TRIAGE])
    helper = _helper(settings, [first, second], clock)

    result = await helper.rebuild_report(
        ReportKind.TRIAGE,
        "last message",
        "facts",
        cwd=tmp_path,
        timeout=timedelta(seconds=_LADDER_TIMEOUT_SECONDS),
    )

    assert first.calls[0][2] == timedelta(seconds=_LADDER_TIMEOUT_SECONDS)
    if expected_second_timeout is None:
        assert isinstance(result, Unrecoverable)
        assert "not enough evidence" in result.unrecoverable
        assert "timeout exhausted before call" in result.unrecoverable
        assert second.calls == []
    else:
        assert isinstance(result, TriageReport)
        assert second.calls[0][2] == timedelta(seconds=expected_second_timeout)


async def test_cancellation_does_not_start_the_next_provider(tmp_path: Path) -> None:
    settings = _settings_with_helper_ladder(tmp_path)
    first = _BlockingHeadless()
    second = _ScriptedHeadless([_VALID_TRIAGE])
    helper = _helper(settings, [first, second], FakeClock(TASK_CREATED_AT))
    task = asyncio.create_task(
        helper.rebuild_report(
            ReportKind.TRIAGE, "last message", "facts", cwd=tmp_path, timeout=_TIMEOUT
        )
    )

    await first.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert second.calls == []


def test_helper_requires_headless_support() -> None:
    with pytest.raises(ConfigError):
        Helper(
            [
                HelperEntry(HELPER_RUNG, _AgentWithHeadless(_ScriptedHeadless([_VALID_TRIAGE]))),
                HelperEntry(
                    Rung(agent="codex", model="sol", effort="high"), _AgentWithoutHeadless()
                ),
            ],
            FakeClock(TASK_CREATED_AT),
        )


def _settings_with_helper_ladder(tmp_path: Path) -> Settings:
    config = MINIMAL_PROJECT_CONFIG.replace(
        'ladder = [ { agent = "claude", model = "haiku", effort = "low" } ]',
        'ladder = [ { agent = "claude", model = "haiku", effort = "low" }, '
        '{ agent = "codex", model = "sol", effort = "high" } ]',
    )
    config_dir = tmp_path / ".shikiru"
    config_dir.mkdir(exist_ok=True)
    (config_dir / "config.toml").write_text(config)
    return load_settings(tmp_path)


def _helper(settings: Settings, headless: Sequence[HeadlessAgent], clock: Clock) -> Helper:
    entries = tuple(
        HelperEntry(rung, _AgentWithHeadless(provider))
        for rung, provider in zip(settings.roles.helper.ladder, headless, strict=True)
    )
    return Helper(entries, clock)


class _ScriptedHeadless(HeadlessAgent):
    def __init__(self, answers: Sequence[Mapping[str, Any] | Exception]) -> None:
        self._answers = list(answers)
        self.calls: list[tuple[str, str | None, timedelta]] = []

    @override
    async def ask(
        self,
        prompt: str,
        schema: Mapping[str, Any],
        model: str,
        effort: str | None,
        cwd: Path,
        timeout: timedelta,
    ) -> Mapping[str, Any]:
        del prompt, schema, cwd
        self.calls.append((model, effort, timeout))
        answer = self._answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


class _AdvancingHeadless(_ScriptedHeadless):
    def __init__(
        self,
        answers: Sequence[Mapping[str, Any] | Exception],
        clock: FakeClock,
        elapsed_seconds: int,
    ) -> None:
        super().__init__(answers)
        self._clock = clock
        self._elapsed_seconds = elapsed_seconds

    @override
    async def ask(
        self,
        prompt: str,
        schema: Mapping[str, Any],
        model: str,
        effort: str | None,
        cwd: Path,
        timeout: timedelta,
    ) -> Mapping[str, Any]:
        answer = await super().ask(prompt, schema, model, effort, cwd, timeout)
        self._clock.advance(self._elapsed_seconds)
        return answer


class _BlockingHeadless(HeadlessAgent):
    def __init__(self) -> None:
        self.started = asyncio.Event()

    @override
    async def ask(
        self,
        prompt: str,
        schema: Mapping[str, Any],
        model: str,
        effort: str | None,
        cwd: Path,
        timeout: timedelta,
    ) -> Mapping[str, Any]:
        del prompt, schema, model, effort, cwd, timeout
        self.started.set()
        await asyncio.Event().wait()
        return {}


class _AgentWithoutHeadless:
    name = "unsupported"

    async def start_turn(self, spec: TurnSpec) -> Turn:
        del spec
        raise AssertionError

    def interactive_command(self, session_id: str, run_dir: Path, rung: Rung) -> Sequence[str]:
        del session_id, run_dir, rung
        return ()

    async def doctor(self, models: Sequence[str]) -> list[CheckResult]:
        del models
        return []

    def headless(self) -> HeadlessAgent | None:
        return None


class _AgentWithHeadless(_AgentWithoutHeadless):
    def __init__(self, headless: HeadlessAgent) -> None:
        self._headless = headless

    def headless(self) -> HeadlessAgent:
        return self._headless
