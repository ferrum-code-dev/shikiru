from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import TYPE_CHECKING

import pytest

from shikiru.agents.sdk.hooks import HookRecorder
from shikiru.process import SubprocessRunner

if TYPE_CHECKING:
    from pathlib import Path

_ROLE = "leader"
_WRITER_COUNT = 20


async def _write_hook_via_subprocess(run_dir: Path, index: int) -> None:
    runner = SubprocessRunner()
    payload = {"index": index, "hook_event_name": "PreToolUse"}
    env = {**os.environ, "SHIKIRU_RUN_DIR": str(run_dir)}
    result = await runner.run(
        [sys.executable, "-m", "shikiru", "hook", _ROLE],
        timeout=10,
        env=env,
        input_text=json.dumps(payload),
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
async def test_concurrent_hook_writers_each_append_one_well_formed_uncorrupted_line(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "runs" / "T-1"

    await asyncio.gather(*(_write_hook_via_subprocess(run_dir, i) for i in range(_WRITER_COUNT)))

    entries = list(HookRecorder(run_dir / _ROLE).tail())

    assert len(entries) == _WRITER_COUNT
    assert {entry.value["index"] for entry in entries} == set(range(_WRITER_COUNT))


def test_tail_resumes_from_the_offset_of_the_last_read_entry(tmp_path: Path) -> None:
    recorder = HookRecorder(tmp_path)
    recorder.record({"index": 0})
    recorder.record({"index": 1})
    first_batch = list(recorder.tail())
    assert [entry.value["index"] for entry in first_batch] == [0, 1]

    recorder.record({"index": 2})
    second_batch = list(recorder.tail(first_batch[-1].offset))

    assert [entry.value["index"] for entry in second_batch] == [2]


def test_tail_does_not_yield_a_line_that_has_no_trailing_newline_yet(tmp_path: Path) -> None:
    recorder = HookRecorder(tmp_path)
    recorder.record({"index": 0})
    path = tmp_path / "hooks.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"index": 1}))

    entries = list(recorder.tail())

    assert [entry.value["index"] for entry in entries] == [0]
