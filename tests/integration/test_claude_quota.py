"""Claude Code quota probes use the process boundary and a clock-backed cache."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from shikiru.agents.sdk.reset_time import ResetTimeParser
from shikiru.plugins.agents.claude.quota import ClaudeQuotaProbe
from shikiru.process import SubprocessRunner
from tests.support.clock import FakeClock

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "agents" / "claude" / "quota_probe.jsonl"
_START = datetime(2026, 1, 1, tzinfo=UTC)


async def test_quota_probe_caches_for_fifteen_minutes_and_refreshes_after_invalidation(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls_path = tmp_path / "calls"
    executable = bin_dir / "claude"
    executable.write_text(
        "#!/bin/sh\n"
        "count=0\n"
        'if [ -f "$SHIKIRU_CLAUDE_CALLS" ]; then count=$(cat "$SHIKIRU_CLAUDE_CALLS"); fi\n'
        "count=$((count + 1))\n"
        'printf "%s\\n" "$count" > "$SHIKIRU_CLAUDE_CALLS"\n'
        'cat "$SHIKIRU_CLAUDE_FIXTURE"\n',
        encoding="utf-8",
    )
    executable.chmod(0o755)
    environment = dict(os.environ)
    environment["PATH"] = f"{bin_dir}{os.pathsep}{environment['PATH']}"
    environment["SHIKIRU_CLAUDE_CALLS"] = str(calls_path)
    environment["SHIKIRU_CLAUDE_FIXTURE"] = str(_FIXTURE)
    clock = FakeClock(_START)
    probe = ClaudeQuotaProbe(SubprocessRunner(), clock, ResetTimeParser(clock), lambda: environment)

    first = await probe.get()
    second = await probe.get()

    assert first == second
    assert [window.name for window in first] == ["five_hour", "seven_day"]
    assert [window.used_pct for window in first] == [42.0, 12.0]
    assert calls_path.read_text(encoding="utf-8").strip() == "1"

    clock.advance(900)
    await probe.get()
    assert calls_path.read_text(encoding="utf-8").strip() == "2"

    probe.invalidate()
    await probe.get()
    assert calls_path.read_text(encoding="utf-8").strip() == "3"
