"""`doctor --probe` turns: live contract check 1 and the smoke turn for other rungs."""

from __future__ import annotations

import asyncio
import json
import shlex
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
from shikiru.agents.sdk.turn import EMPTY_OUTPUT, stderr_log_path, stream_log_path
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
    from collections.abc import Awaitable, Callable, Mapping, Sequence
    from pathlib import Path
    from typing import Any

    from shikiru.agents.contracts import AgentSignal, Rung, Turn
    from shikiru.agents.registry import AgentRegistry
    from shikiru.clock import Clock
    from shikiru.config.settings import DoctorConfig
    from shikiru.process import SubprocessRunner

GIT_INIT_TIMEOUT_SECONDS = 10.0
GIT_INIT_ARGV = ("git", "init")
PROBE_ROLE = "implementer"
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
    "{report_command}\n"
    "Do not inspect or edit other files or run any other commands. "
    "After the command succeeds, reply exactly REPORT_OK."
)
SMOKE_PROMPT = "Hi"
LIVE_REPORT_PASSED = "valid report submitted"
SMOKE_PASSED = "turn ended"


@dataclass(frozen=True)
class ProbeOutcome:
    """One probe turn's verdict, with the raw stream tail and stderr kept for a failure."""

    passed: str
    problem: str | None
    stream_tail: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.problem is None

    @property
    def detail(self) -> str:
        if self.problem is None:
            return self.passed
        return (
            f"{self.problem}\nstream tail:\n{self.stream_tail or EMPTY_OUTPUT}"
            f"\nstderr:\n{self.stderr or EMPTY_OUTPUT}"
        )


@dataclass(frozen=True)
class _ProbeCheck:
    passed: str
    prepare: Callable[[SubprocessRunner, TurnSpec], Awaitable[str]]
    problem: Callable[[TurnSpec, Sequence[AgentSignal]], str | None]


class AgentProbe:
    """Run one probe turn for a rung in the project's reusable `.shikiru/probe/` scratch."""

    def __init__(self, registry: AgentRegistry, runner: SubprocessRunner, clock: Clock) -> None:
        """Use the shared agent registry and runtime boundaries the engine uses."""
        self._registry = registry
        self._runner = runner
        self._clock = clock

    async def run(
        self, project_root: Path, rung: Rung, options: Mapping[str, Any], config: DoctorConfig
    ) -> ProbeOutcome:
        """Start one real turn on `rung` that must submit a valid report through the CLI."""
        return await self._probe(project_root, rung, options, config, _LIVE_REPORT_CHECK)

    async def smoke(
        self, project_root: Path, rung: Rung, options: Mapping[str, Any], config: DoctorConfig
    ) -> ProbeOutcome:
        """Start one real turn on `rung` with `SMOKE_PROMPT` that must end in `TurnEnded`."""
        return await self._probe(project_root, rung, options, config, _SMOKE_CHECK)

    async def _probe(
        self,
        project_root: Path,
        rung: Rung,
        options: Mapping[str, Any],
        config: DoctorConfig,
        check: _ProbeCheck,
    ) -> ProbeOutcome:
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
            problem = await self._run_turn(spec, options, config.probe_timeout, check)
        except (ShikiruError, OSError) as error:
            problem = str(error) or type(error).__name__
        if problem is None:
            return ProbeOutcome(passed=check.passed, problem=None)
        return await asyncio.to_thread(
            _failed_outcome, spec, check.passed, problem, config.probe_stream_tail_lines
        )

    async def _run_turn(
        self, spec: TurnSpec, options: Mapping[str, Any], timeout: float, check: _ProbeCheck
    ) -> str | None:
        agent = self._registry.create(
            spec.rung.agent, AgentContext(options=options, runner=self._runner, clock=self._clock)
        )
        prompt = await check.prepare(self._runner, spec)
        turn = await agent.start_turn(replace(spec, prompt=prompt))
        try:
            signals = await _collect(turn, timeout)
        except TimeoutError:
            await turn.stop()
            return f"live turn exceeded {timeout:g}s"
        return await asyncio.to_thread(check.problem, spec, signals)


async def _collect(turn: Turn, timeout: float) -> list[AgentSignal]:
    async with asyncio.timeout(timeout):
        return [signal async for signal in turn.signals()]


def _failed_outcome(spec: TurnSpec, passed: str, problem: str, tail_lines: int) -> ProbeOutcome:
    stream_path = stream_log_path(spec.turn_dir, spec.role)
    stderr_path = stderr_log_path(spec.turn_dir, spec.role)
    stream_lines = stream_path.read_text().splitlines() if stream_path.is_file() else []
    return ProbeOutcome(
        passed=passed,
        problem=problem,
        stream_tail="\n".join(stream_lines[-tail_lines:]),
        stderr=stderr_path.read_text().strip() if stderr_path.is_file() else "",
    )


async def init_scratch_repo(runner: SubprocessRunner, path: Path) -> None:
    """Create `path` as a git repository, reusing one that already exists."""
    await asyncio.to_thread(path.mkdir, parents=True, exist_ok=True)
    await runner.run(GIT_INIT_ARGV, timeout=GIT_INIT_TIMEOUT_SECONDS, cwd=path, check=True)


def live_report_command(report_input: Path) -> str:
    """Return the report command naming `report_input` by absolute path, shell-quoted."""
    return f"{REPORT_COMMAND} {shlex.quote(str(report_input))}"


def live_report_instructions(run_dir: Path) -> str:
    """Return the report check's instructions for the report input under `run_dir`."""
    return LIVE_REPORT_PROMPT.format(
        report_command=live_report_command(run_dir / REPORT_INPUT_FILENAME)
    )


async def prepare_live_report(runner: SubprocessRunner, spec: TurnSpec) -> Path:
    """Lay out `spec`'s repository, run and turn dirs for the report check; return the prompt."""
    await init_scratch_repo(runner, spec.cwd)
    await asyncio.to_thread(_prepare_report_turn, spec)
    await asyncio.to_thread(
        (spec.run_dir / REPORT_INPUT_FILENAME).write_text, json.dumps(LIVE_REPORT_DATA)
    )
    prompt_path = spec.cwd / REPORT_PROMPT_FILENAME
    await asyncio.to_thread(prompt_path.write_text, live_report_instructions(spec.run_dir))
    return prompt_path


async def _prepare_live_report_prompt(runner: SubprocessRunner, spec: TurnSpec) -> str:
    return TURN_PROMPT.format(prompt_path=await prepare_live_report(runner, spec))


async def _prepare_smoke(runner: SubprocessRunner, spec: TurnSpec) -> str:
    await init_scratch_repo(runner, spec.cwd)
    await asyncio.to_thread(_clear_current_turn, spec)
    return SMOKE_PROMPT


def live_report_problem(
    spec: TurnSpec, signals: Sequence[AgentSignal], *, command_signalled: bool = True
) -> str | None:
    """Return why a finished report-check turn failed, or `None` when it passed.

    `command_signalled` is False when the agent's tool-start signal cannot name the command it ran,
    so only the turn outcome and the written report are checked.
    """
    return (
        _turn_problem(signals)
        or (_report_command_problem(signals) if command_signalled else None)
        or _report_problem(spec.turn_dir)
    )


def _smoke_problem(spec: TurnSpec, signals: Sequence[AgentSignal]) -> str | None:
    del spec
    return _turn_problem(signals)


def _turn_problem(signals: Sequence[AgentSignal]) -> str | None:
    if not signals or not isinstance(signals[0], SessionStarted):
        return "live turn omitted SessionStarted"
    terminal = signals[-1]
    if isinstance(terminal, TurnFailed):
        return f"live turn failed ({terminal.kind.value}): {terminal.message}"
    if not isinstance(terminal, TurnEnded):
        return "live turn omitted TurnEnded"
    return None


def _report_command_problem(signals: Sequence[AgentSignal]) -> str | None:
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
    _clear_current_turn(spec).symlink_to(spec.turn_dir)


def _clear_current_turn(spec: TurnSpec) -> Path:
    link = role_dir(spec.run_dir, spec.role) / CURRENT_TURN_LINK_NAME
    link.parent.mkdir(parents=True, exist_ok=True)
    link.unlink(missing_ok=True)
    return link


_LIVE_REPORT_CHECK = _ProbeCheck(
    passed=LIVE_REPORT_PASSED, prepare=_prepare_live_report_prompt, problem=live_report_problem
)
_SMOKE_CHECK = _ProbeCheck(passed=SMOKE_PASSED, prepare=_prepare_smoke, problem=_smoke_problem)
