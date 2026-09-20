"""Claude Code quota probes use the process boundary and a clock-backed cache."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from shikiru.process import SubprocessRunner

from shikiru.agents.sdk.reset_time import ResetTimeParser
from shikiru.plugins.agents.claude.quota import PROBE_ARGV, ClaudeQuotaProbe
from shikiru.process import ProcessResult
from tests.support.clock import FakeClock

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "agents" / "claude" / "quota_probe.jsonl"
_START = datetime(2026, 1, 1, tzinfo=UTC)


class _FixtureRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    async def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        input_text: str | None = None,
        check: bool = False,
    ) -> ProcessResult:
        del timeout, cwd, env, input_text, check
        argv_tuple = tuple(argv)
        self.calls.append(argv_tuple)
        return ProcessResult(argv_tuple, 0, _FIXTURE.read_text(encoding="utf-8"), "")


async def test_quota_probe_caches_for_fifteen_minutes_and_refreshes_after_invalidation() -> None:
    runner = _FixtureRunner()
    clock = FakeClock(_START)
    probe = ClaudeQuotaProbe(cast("SubprocessRunner", runner), clock, ResetTimeParser(clock), dict)

    first = await probe.get()
    second = await probe.get()

    assert first == second
    assert [window.name for window in first] == ["five_hour", "seven_day"]
    assert [window.used_pct for window in first] == [42.0, 12.0]
    assert len(runner.calls) == 1
    assert runner.calls[0] == PROBE_ARGV

    clock.advance(900)
    await probe.get()
    assert len(runner.calls) == 2

    probe.invalidate()
    await probe.get()
    assert len(runner.calls) == 3
