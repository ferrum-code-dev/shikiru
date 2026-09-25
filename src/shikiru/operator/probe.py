"""Live contract check 1 (04 §3): one cheap headless turn that must submit a valid report."""

from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from pydantic import ValidationError

from shikiru.agents.contracts import (
    ROLE_ENV_VAR,
    RUN_DIR_ENV_VAR,
    AgentContext,
    SessionStarted,
    ToolStarted,
    TurnEnded,
    TurnFailed,
    TurnSpec,
    role_dir,
    shikiru_command,
)
from shikiru.agents.sdk.turn import stderr_log_path, stream_log_path
from shikiru.domain.reports import (
    CURRENT_TURN_LINK_NAME,
    REPORT_FILENAME,
    REPORT_KIND_FILENAME,
    REPORT_SCHEMA_FILENAME,
    ReportKind,
    model_for,
    schema_for,
)
from shikiru.engine.turns import TURN_PROMPT
from shikiru.errors import ShikiruError
from shikiru.paths import TURNS_DIR_NAME, probe_repo_path, probe_run_dir

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path
    from typing import Any

    from shikiru.agents.contracts import AgentSignal, Rung, Turn
    from shikiru.agents.registry import AgentRegistry
    from shikiru.clock import Clock
    from shikiru.process import SubprocessRunner

GIT_INIT_TIMEOUT_SECONDS = 10.0
PROBE_TIMEOUT_SECONDS = 180.0
PROBE_ROLE = "implementer"
PROBE_STREAM_TAIL_LINES = 20
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


@dataclass(frozen=True)
class ProbeOutcome:
    """One probe turn's verdict, with the raw stream tail and stderr kept for a failure."""

    problem: str | None
    stream_tail: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.problem is None

    @property
    def detail(self) -> str:
        if self.problem is None:
            return "valid report submitted"
        return (
            f"{self.problem}\nstream tail:\n{self.stream_tail or '(empty)'}"
            f"\nstderr:\n{self.stderr or '(empty)'}"
        )


class AgentProbe:
    """Run live check 1 for one rung in the project's reusable `.shikiru/probe/` scratch."""

    def __init__(self, registry: AgentRegistry, runner: SubprocessRunner, clock: Clock) -> None:
        """Use the shared agent registry and runtime boundaries the engine uses."""
        self._registry = registry
        self._runner = runner
        self._clock = clock

    async def run(self, project_root: Path, rung: Rung, options: Mapping[str, Any]) -> ProbeOutcome:
        """Start one real turn on `rung` that must submit a valid report through the CLI."""
        run_dir = probe_run_dir(project_root)
        turn_dir = run_dir / TURNS_DIR_NAME / rung.agent
        await asyncio.to_thread(shutil.rmtree, turn_dir, ignore_errors=True)
        spec = TurnSpec(
            rung=rung,
            role=PROBE_ROLE,
            prompt="",
            cwd=probe_repo_path(project_root),
            run_dir=run_dir,
            turn_dir=turn_dir,
            add_dirs=(),
            env={RUN_DIR_ENV_VAR: str(run_dir), ROLE_ENV_VAR: PROBE_ROLE},
            session_id=None,
            usage=None,
        )
        try:
            problem = await self._run_turn(spec, options)
        except (ShikiruError, OSError) as error:
            problem = str(error) or type(error).__name__
        if problem is None:
            return ProbeOutcome(problem=None)
        return await asyncio.to_thread(_failed_outcome, spec, problem)

    async def _run_turn(self, spec: TurnSpec, options: Mapping[str, Any]) -> str | None:
        agent = self._registry.create(
            spec.rung.agent, AgentContext(options=options, runner=self._runner, clock=self._clock)
        )
        prompt_path = await prepare_live_report(self._runner, spec)
        spec = replace(spec, prompt=TURN_PROMPT.format(prompt_path=prompt_path))
        turn = await agent.start_turn(spec)
        try:
            signals = await _collect(turn)
        except TimeoutError:
            await turn.stop()
            return f"live turn exceeded {PROBE_TIMEOUT_SECONDS:g}s"
        return await asyncio.to_thread(live_report_problem, spec, signals)


async def _collect(turn: Turn) -> list[AgentSignal]:
    async with asyncio.timeout(PROBE_TIMEOUT_SECONDS):
        return [signal async for signal in turn.signals()]


def _failed_outcome(spec: TurnSpec, problem: str) -> ProbeOutcome:
    stream_path = stream_log_path(spec.turn_dir, spec.role)
    stderr_path = stderr_log_path(spec.turn_dir, spec.role)
    stream_lines = stream_path.read_text().splitlines() if stream_path.is_file() else []
    return ProbeOutcome(
        problem=problem,
        stream_tail="\n".join(stream_lines[-PROBE_STREAM_TAIL_LINES:]),
        stderr=stderr_path.read_text().strip() if stderr_path.is_file() else "",
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
