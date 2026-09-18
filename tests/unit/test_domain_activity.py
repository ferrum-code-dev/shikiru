from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from shikiru.config.settings import TimeoutsConfig
from shikiru.domain.activity import is_operator_prompt, is_stalled

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_TIMEOUTS = TimeoutsConfig(
    launch=90, stall=1200, tool=7200, max_quota_wait=28800, integration_wait=1800, helper=300
)


@pytest.mark.parametrize(
    ("elapsed_seconds", "tool_running", "expected"),
    [
        (_TIMEOUTS.stall - 1, False, False),
        (_TIMEOUTS.stall, False, True),
        (_TIMEOUTS.tool - 1, True, False),
        (_TIMEOUTS.tool, True, True),
        (_TIMEOUTS.stall, True, False),
    ],
    ids=[
        "below-stall-timeout",
        "at-stall-timeout",
        "below-tool-timeout",
        "at-tool-timeout",
        "stall-timeout-does-not-apply-to-a-running-tool",
    ],
)
def test_is_stalled_uses_the_timeout_matching_the_activity_kind(
    elapsed_seconds: int, tool_running: bool, expected: bool
) -> None:
    last_activity = _NOW - timedelta(seconds=elapsed_seconds)

    assert is_stalled(last_activity, tool_running, _NOW, _TIMEOUTS) is expected


def test_a_line_with_a_run_dir_path_is_not_an_operator_prompt() -> None:
    run_dir = Path("/project/.shikiru/runs/T-1")
    text = f"Read {run_dir}/turns/01-implement-i1/prompt.md and follow it exactly."

    assert is_operator_prompt(text, run_dir, pending_lines=()) is False


def test_a_pending_before_turn_line_is_not_an_operator_prompt() -> None:
    run_dir = Path("/project/.shikiru/runs/T-1")
    line = "/compact"

    assert is_operator_prompt(line, run_dir, pending_lines=(line,)) is False


def test_typed_text_with_no_run_dir_path_is_an_operator_prompt() -> None:
    run_dir = Path("/project/.shikiru/runs/T-1")

    assert is_operator_prompt("please use snake_case", run_dir, pending_lines=()) is True
