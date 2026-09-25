"""Live contract check 1 (04 §3): one cheap headless turn that must submit a valid report."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

from pydantic import ValidationError

from shikiru.agents.contracts import (
    SessionStarted,
    ToolStarted,
    TurnEnded,
    TurnFailed,
    role_dir,
    shikiru_command,
)
from shikiru.domain.reports import (
    CURRENT_TURN_LINK_NAME,
    REPORT_FILENAME,
    REPORT_KIND_FILENAME,
    REPORT_SCHEMA_FILENAME,
    ReportKind,
    model_for,
    schema_for,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from shikiru.agents.contracts import AgentSignal, TurnSpec
    from shikiru.process import SubprocessRunner

GIT_INIT_TIMEOUT_SECONDS = 10.0
REPORT_INPUT_FILENAME = "contract-report-input.json"
REPORT_PROMPT_FILENAME = "contract-prompt.md"
REPORT_TOOL_NAME = "shikiru report"
REPORT_COMMAND = shikiru_command("report", "--file")
LIVE_REPORT_KIND = ReportKind.IMPLEMENTATION
LIVE_REPORT_DATA: dict[str, object] = {
    "status": "completed",
    "summary": "Isolated live report contract.",
    "commits": [],
    "tests": [],
    "trackers_updated": False,
    "risks": [],
}
LIVE_REPORT_PROMPT = (
    "Use Bash to run exactly this report command once:\n"
    f'{REPORT_COMMAND} "$SHIKIRU_RUN_DIR/{REPORT_INPUT_FILENAME}"\n'
    "Do not inspect or edit other files or run any other commands. "
    "After the command succeeds, reply exactly REPORT_OK."
)


async def init_scratch_repo(runner: SubprocessRunner, path: Path) -> None:
    """Create `path` as a git repository, reusing one that already exists."""
    await asyncio.to_thread(path.mkdir, parents=True, exist_ok=True)
    await runner.run(["git", "init"], timeout=GIT_INIT_TIMEOUT_SECONDS, cwd=path, check=True)


async def prepare_live_report(runner: SubprocessRunner, spec: TurnSpec) -> Path:
    """Lay out `spec`'s repository, run and turn dirs for the report check; return the prompt."""
    await init_scratch_repo(runner, spec.cwd)
    await asyncio.to_thread(_prepare_report_turn, spec)
    await asyncio.to_thread(
        (spec.run_dir / REPORT_INPUT_FILENAME).write_text, json.dumps(LIVE_REPORT_DATA)
    )
    prompt_path = spec.cwd / REPORT_PROMPT_FILENAME
    await asyncio.to_thread(prompt_path.write_text, LIVE_REPORT_PROMPT)
    return prompt_path


def live_report_problem(spec: TurnSpec, signals: Sequence[AgentSignal]) -> str | None:
    """Return why a finished report-check turn failed, or `None` when it passed."""
    return _signals_problem(signals) or _report_problem(spec.turn_dir)


def _signals_problem(signals: Sequence[AgentSignal]) -> str | None:
    if not signals or not isinstance(signals[0], SessionStarted):
        return "live turn omitted SessionStarted"
    terminal = signals[-1]
    if isinstance(terminal, TurnFailed):
        return f"live turn failed ({terminal.kind.value}): {terminal.message}"
    if not isinstance(terminal, TurnEnded):
        return "live turn omitted TurnEnded"
    if not any(_is_report_command(signal) for signal in signals):
        return "live turn did not invoke shikiru report"
    return None


def _report_problem(turn_dir: Path) -> str | None:
    report_path = turn_dir / REPORT_FILENAME
    if not report_path.is_file():
        return "live turn wrote no report"
    kind = ReportKind((turn_dir / REPORT_KIND_FILENAME).read_text().strip())
    try:
        model_for(kind).model_validate_json(report_path.read_text())
    except ValidationError as error:
        return f"live turn wrote an invalid report: {error}"
    return None


def _is_report_command(signal: AgentSignal) -> bool:
    return isinstance(signal, ToolStarted) and (
        signal.name == REPORT_TOOL_NAME
        or (REPORT_COMMAND in signal.detail and REPORT_INPUT_FILENAME in signal.detail)
    )


def _prepare_report_turn(spec: TurnSpec) -> None:
    spec.turn_dir.mkdir(parents=True, exist_ok=True)
    (spec.turn_dir / REPORT_KIND_FILENAME).write_text(LIVE_REPORT_KIND.value)
    (spec.turn_dir / REPORT_SCHEMA_FILENAME).write_text(json.dumps(schema_for(LIVE_REPORT_KIND)))
    link = role_dir(spec.run_dir, spec.role) / CURRENT_TURN_LINK_NAME
    link.parent.mkdir(parents=True, exist_ok=True)
    link.unlink(missing_ok=True)
    link.symlink_to(spec.turn_dir)
