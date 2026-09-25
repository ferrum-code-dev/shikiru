from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from shutil import copyfile

from shikiru.agents.sdk.reset_time import ResetTimeParser
from shikiru.plugins.agents.codex.quota import CodexQuotaProbe
from tests.support.clock import FakeClock

_FIXTURE = (
    Path(__file__).parent.parent / "fixtures" / "agents" / "codex" / "rollout_full_turn.jsonl"
)
_START = datetime(2026, 1, 1, tzinfo=UTC)
_RESET = datetime.fromtimestamp(1789948365, tz=UTC)
_SECONDARY_RESET = datetime.fromtimestamp(1790414331, tz=UTC)


def test_quota_probe_reads_latest_rollout_windows(tmp_path: Path) -> None:
    home = tmp_path / "codex"
    rollout = home / "sessions" / "2026" / "01" / "rollout-2026-01-01T00-00-00-session.jsonl"
    rollout.parent.mkdir(parents=True)
    copyfile(_FIXTURE, rollout)
    clock = FakeClock(_START)
    probe = CodexQuotaProbe(home, clock, ResetTimeParser(clock))

    windows = probe.get()

    assert [window.name for window in windows] == ["primary", "secondary"]
    assert [window.used_pct for window in windows] == [53.0, 68.0]
    assert [window.resets_at for window in windows] == [_RESET, _SECONDARY_RESET]
    assert all(window.observed_at == _START for window in windows)


def test_quota_probe_drops_old_windows_when_latest_rollout_has_null_limits() -> None:
    clock = FakeClock(_START)
    probe = CodexQuotaProbe(Path(), clock, ResetTimeParser(clock))
    earlier = {
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "rate_limits": {"primary": {"used_percent": 53.0}},
        },
    }
    latest = {"type": "event_msg", "payload": {"type": "token_count", "rate_limits": None}}

    windows = probe.parse(
        f"{json.dumps(earlier)}\n{json.dumps(latest)}\n",
        _START,
    )

    assert windows == []
