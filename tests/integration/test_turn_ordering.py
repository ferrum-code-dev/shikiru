"""Turn directories order and resolve by their number, past two digits (03 §4.3)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.agents.contracts import role_dir
from shikiru.domain.reports import (
    CURRENT_TURN_LINK_NAME,
    REPORT_FILENAME,
    REPORT_KIND_FILENAME,
    ImplementationReport,
    ReportKind,
)
from shikiru.domain.tasks import Role, Step
from shikiru.engine.history import ReportHistory
from shikiru.engine.turns import TurnRequest
from shikiru.paths import run_dir, turns_dir
from shikiru.prompts.renderer import PromptName

if TYPE_CHECKING:
    from pathlib import Path

    from tests.support.engine import EngineEnv

_TURNS = 120
_TWO_DIGIT_TURNS = 99


def _turn_name(number: int) -> str:
    return f"{number:02d}-implement-i1"


def _write_turns(project_root: Path, task_id: str) -> None:
    for number in range(1, _TURNS + 1):
        turn_dir = turns_dir(project_root, task_id) / _turn_name(number)
        turn_dir.mkdir(parents=True)
        (turn_dir / REPORT_KIND_FILENAME).write_text(ReportKind.IMPLEMENTATION.value)
        (turn_dir / REPORT_FILENAME).write_text(
            ImplementationReport(
                status="completed",
                summary=str(number),
                commits=[],
                tests=[],
                trackers_updated=True,
            ).model_dump_json()
        )


async def test_reports_load_in_turn_order_past_ninety_nine_turns(engine_env: EngineEnv) -> None:
    task = await engine_env.build_task()
    _write_turns(engine_env.project_root, task.id)

    history = ReportHistory(engine_env.project_root).load(task.id)

    assert [label for label, _ in history] == [_turn_name(n) for n in range(1, _TURNS + 1)]


async def test_the_latest_turn_is_found_past_ninety_nine_turns(engine_env: EngineEnv) -> None:
    task = await engine_env.build_task()
    _write_turns(engine_env.project_root, task.id)
    latest = turns_dir(engine_env.project_root, task.id) / _turn_name(_TURNS)
    link = (
        role_dir(run_dir(engine_env.project_root, task.id), Role.IMPLEMENTER.value)
        / CURRENT_TURN_LINK_NAME
    )
    link.parent.mkdir(parents=True)
    link.symlink_to(latest)
    request = TurnRequest(
        step=Step.IMPLEMENT,
        iteration=1,
        kind=ReportKind.IMPLEMENTATION,
        template=PromptName.IMPLEMENT,
        context={},
        facts="",
    )

    found = engine_env.turn_system.turns.completed_turn(task.id, Role.IMPLEMENTER, request)

    assert found is not None
    assert found[1] == latest
