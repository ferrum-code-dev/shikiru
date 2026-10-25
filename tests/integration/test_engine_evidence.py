"""Escalation evidence read from a task's files."""

from __future__ import annotations

import tracemalloc
from typing import TYPE_CHECKING

from shikiru.domain.tasks import Role, Step
from shikiru.engine.evidence import ESCALATION_PANE_TAIL_LINES
from shikiru.paths import run_dir
from shikiru.view_log import view_log_path
from tests.support.engine import build_evidence
from tests.support.factories import make_task

if TYPE_CHECKING:
    from pathlib import Path

_LINES = 200_000
_LINE_WIDTH = 100
_READ_BUDGET_FRACTION = 10


async def test_the_turn_tail_of_a_huge_view_log_is_read_without_loading_the_log(
    tmp_path: Path,
) -> None:
    task = make_task()
    log = view_log_path(run_dir(tmp_path, task.id), Role.IMPLEMENTER.value)
    log.parent.mkdir(parents=True)
    with log.open("w") as handle:
        for number in range(_LINES):
            handle.write(f"{number:0{_LINE_WIDTH - 1}d}\n")
    evidence = build_evidence(tmp_path)

    tracemalloc.start()
    try:
        collected = await evidence.collect(task, Step.IMPLEMENT)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    lines = collected.view_tail.splitlines()
    assert len(lines) == ESCALATION_PANE_TAIL_LINES
    assert lines[-1] == f"{_LINES - 1:0{_LINE_WIDTH - 1}d}"
    assert lines[0] == f"{_LINES - ESCALATION_PANE_TAIL_LINES:0{_LINE_WIDTH - 1}d}"
    assert peak < log.stat().st_size // _READ_BUDGET_FRACTION
