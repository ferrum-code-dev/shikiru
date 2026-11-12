"""Muse Code session log parsing over the recorded session-log excerpt."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shikiru.agents.contracts import Compacted
from shikiru.plugins.agents.muse.options import (
    PERMISSION_TRANSACTION_FRAME,
    XDG_DATA_HOME_ENV_VAR,
)
from shikiru.plugins.agents.muse.session_log import (
    ModelCompletion,
    SessionLogError,
    compaction_count,
    compactions_after,
    model_completions,
    muse_home,
    session_records,
)

_FIXTURE = Path(__file__).parents[1] / "fixtures" / "agents" / "muse" / "session_compaction.jsonl"
_HOME = Path("/home/operator")
_MODEL_COMPLETED_LINE = json.dumps(
    {
        "payload": {
            "event": {
                "estimated_prompt_tokens": 31082,
                "finish_reason": "tool_calls",
                "kind": "model_completed",
                "model": "muse-ember-1.0",
                "usage": {
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                    "cached_tokens": 0,
                    "input_tokens": 30570,
                    "output_tokens": 273,
                    "reasoning_tokens": 138,
                },
            },
            "kind": "run",
        },
        "payload_type": "runtime.session",
        "record_type": "event",
    }
)


def _fixture_lines() -> list[str]:
    return _FIXTURE.read_text(encoding="utf-8").splitlines()


def test_muse_home_follows_xdg_data_home_or_defaults_under_the_user_home() -> None:
    assert muse_home({XDG_DATA_HOME_ENV_VAR: "/data"}, _HOME) == Path("/data/muse")
    assert muse_home({}, _HOME) == _HOME / ".local" / "share" / "muse"


@pytest.mark.parametrize(
    ("previous_count", "expected"),
    [
        (0, [Compacted(pre_tokens=44662, post_tokens=25159)]),
        (1, []),
    ],
)
def test_only_compactions_installed_after_turn_start_are_reported(
    previous_count: int, expected: list[Compacted]
) -> None:
    records = session_records(_fixture_lines())

    assert compaction_count(records) == 1
    assert compactions_after(records, previous_count) == expected


def test_frame_children_are_read_in_place_of_the_frame() -> None:
    lines = _fixture_lines()
    frame = {
        "retained_frame": PERMISSION_TRANSACTION_FRAME,
        "children": [{"child_index": 0, "record_json": lines[0]}],
    }

    records = session_records([json.dumps(frame), lines[1]])

    assert compaction_count(records) == 1
    assert compactions_after(records, 0) == [Compacted(pre_tokens=44662, post_tokens=25159)]


def test_model_completed_events_carry_exact_call_usage() -> None:
    records = session_records([_MODEL_COMPLETED_LINE])

    assert model_completions(records) == [
        ModelCompletion(
            model="muse-ember-1.0",
            input_tokens=30570,
            output_tokens=273,
            cached_tokens=0,
            estimated_prompt_tokens=31082,
        )
    ]


@pytest.mark.parametrize("line", ["not json", "[1, 2]"])
def test_malformed_records_are_rejected_rather_than_skipped(line: str) -> None:
    with pytest.raises(SessionLogError):
        session_records([line])
