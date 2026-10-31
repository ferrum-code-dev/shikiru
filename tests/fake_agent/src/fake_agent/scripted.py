"""Script state and report submission for the fake agent CLI."""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn, TypedDict, cast

from fake_agent.script import (
    AcceptStep,
    ChangesRequestedStep,
    EscalateStep,
    NeedsDecisionStep,
    NewTaskSpec,
    SupersedeStep,
    TurnStep,
)
from fake_agent.turns import FAKE_SESSION_ID_ENV_VAR, INITIAL_CONTEXT_USAGE_PERCENT
from shikiru.agents.contracts import ROLE_ENV_VAR, RUN_DIR_ENV_VAR, role_dir, shikiru_command
from shikiru.cli.report import resolve_current_turn_dir
from shikiru.domain.reports import (
    REPORT_KIND_FILENAME,
    Commit,
    ConsultAnswer,
    EscalationRequest,
    ImplementationReport,
    Issue,
    NewTask,
    Option,
    PlanReport,
    Report,
    ReportKind,
    TriageReport,
    Verdict,
)
from shikiru.domain.tasks import Mode
from shikiru.process import SubprocessRunner

SCRIPT_ENV_VAR = "SHIKIRU_FAKE_SCRIPT"
TASK_SCRIPT_DIR_ENV_VAR = "SHIKIRU_FAKE_TASK_DIR"
TASK_ENV_VAR = "SHIKIRU_TASK"
TICK_A1_ENV_VAR = "SHIKIRU_FAKE_TICK_A1"
TICK_A1_ENABLED_VALUE = "1"
_STATE_DIRNAME = "fake_state"
FAKE_CHANGE_FILENAME = "fake-agent-changes.txt"
_TASK_CHANGE_PREFIX = "fake-agent-"
_REPORT_FILENAME_PREFIX = "fake-agent-report-"
_TODO_FILENAME = "todo.md"
_A1_UNCHECKED = "- [ ] A1"
_A1_CHECKED = "- [x] A1"
_GIT_TIMEOUT_SECONDS = 10.0
_REPORT_TIMEOUT_SECONDS = 10.0
_MAX_COMMIT_SUBJECT_LENGTH = 72
_COMMIT_IDENTITY_ARGS = ("-c", "user.name=Fake Agent", "-c", "user.email=fake-agent@shikiru.test")
_OTHER_BRANCH = "feature/agent-chose-this"
_KINDS_WITH_COMMITS = (ReportKind.IMPLEMENTATION, ReportKind.PLAN)


def role_script_env_var(role: str) -> str:
    """Return the environment variable scripting one role's turns."""
    return f"{SCRIPT_ENV_VAR}_{role.upper()}"


class SessionState(TypedDict):
    """One fake session's remembered prompts, usage totals and context fill."""

    prompts: list[str]
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    context_used_pct: float


@dataclass(frozen=True)
class ScriptContext:
    """The paths and process boundary used by one fake turn."""

    run_dir: Path
    role: str
    session_id: str
    runner: SubprocessRunner
    cwd: Path
    state_path: Path
    script_path: Path


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        message = f"fake agent turn needs {name} set; Shikiru must launch it"
        raise SystemExit(message)
    return value


def build_context() -> ScriptContext:
    """Build fake turn paths from the environment passed by the engine."""
    run_dir = Path(_require_env(RUN_DIR_ENV_VAR))
    role = _require_env(ROLE_ENV_VAR)
    session_id = _require_env(FAKE_SESSION_ID_ENV_VAR)
    state_dir = role_dir(run_dir, role) / _STATE_DIRNAME
    state_dir.mkdir(parents=True, exist_ok=True)
    return ScriptContext(
        run_dir=run_dir,
        role=role,
        session_id=session_id,
        runner=SubprocessRunner(),
        cwd=Path.cwd(),
        state_path=state_dir / f"{session_id}.json",
        script_path=state_dir / "script-index.json",
    )


def load_session_state(state_path: Path) -> SessionState:
    """Return the fake session's persisted state, or a fresh one."""
    if not state_path.is_file():
        return {
            "prompts": [],
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "context_used_pct": INITIAL_CONTEXT_USAGE_PERCENT,
        }
    return cast("SessionState", json.loads(state_path.read_text()))


def save_session_state(state_path: Path, state: SessionState) -> None:
    """Persist the fake session's state for its next resumed turn."""
    state_path.write_text(json.dumps(state))


def script_index(script_path: Path) -> int:
    """Return the index of the next fake behavior."""
    if not script_path.is_file():
        return 0
    return json.loads(script_path.read_text())["index"]


def save_script_index(script_path: Path, index: int) -> None:
    """Persist the index of the next fake behavior."""
    script_path.write_text(json.dumps({"index": index}))


def task_script_path(directory: str, task_id: str) -> Path:
    """Return the file scripting one task's turns: a JSON object of step lists by role."""
    return Path(directory) / f"{task_id}.json"


def task_change_filename(task_id: str) -> str:
    """Return the file a task commits to when it has its own (`TASK_SCRIPT_DIR_ENV_VAR` set)."""
    return f"{_TASK_CHANGE_PREFIX}{task_id}.txt"


def _task_script_for(role: str) -> str | None:
    directory = os.environ.get(TASK_SCRIPT_DIR_ENV_VAR)
    if not directory:
        return None
    path = task_script_path(directory, os.environ[TASK_ENV_VAR])
    if not path.is_file():
        return None
    steps = json.loads(path.read_text()).get(role)
    return None if steps is None else json.dumps(steps)


def script_raw_for(role: str) -> str | None:
    """Return the task's script for `role`, else the role's, else the shared fake script."""
    return (
        _task_script_for(role)
        or os.environ.get(role_script_env_var(role))
        or os.environ.get(SCRIPT_ENV_VAR)
    )


def _tick_a1(cwd: Path) -> None:
    tracker = cwd / "tasks" / _TODO_FILENAME
    contents = tracker.read_text()
    if _A1_CHECKED in contents:
        return
    if _A1_UNCHECKED not in contents:
        message = f"fake agent could not find the A1 checkbox in {tracker}"
        raise RuntimeError(message)
    tracker.write_text(contents.replace(_A1_UNCHECKED, _A1_CHECKED, 1))


def _repository_directories(cwd: Path) -> tuple[Path, ...]:
    if (cwd / ".git").exists():
        return (cwd,)
    repositories = tuple(
        path for path in sorted(cwd.iterdir()) if path.is_dir() and (path / ".git").exists()
    )
    if not repositories:
        message = f"fake agent cwd {cwd} contains no git repository"
        raise RuntimeError(message)
    return repositories


def _change_filename() -> str:
    if os.environ.get(TASK_SCRIPT_DIR_ENV_VAR):
        return task_change_filename(os.environ[TASK_ENV_VAR])
    return FAKE_CHANGE_FILENAME


async def _commit_changes(runner: SubprocessRunner, cwd: Path, prompt_text: str) -> list[Commit]:
    subject = f"fake: {prompt_text}"[:_MAX_COMMIT_SUBJECT_LENGTH]
    commits: list[Commit] = []
    for repository in await asyncio.to_thread(_repository_directories, cwd):
        marker = repository / _change_filename()
        previous = marker.read_text() if marker.is_file() else ""
        marker.write_text(previous + prompt_text + "\n")
        await runner.run(["git", "add", "-A"], timeout=_GIT_TIMEOUT_SECONDS, cwd=repository)
        await runner.run(
            ["git", *_COMMIT_IDENTITY_ARGS, "commit", "-m", subject],
            timeout=_GIT_TIMEOUT_SECONDS,
            cwd=repository,
        )
        result = await runner.run(
            ["git", "rev-parse", "HEAD"], timeout=_GIT_TIMEOUT_SECONDS, cwd=repository
        )
        commits.append(Commit(sha=result.stdout.strip(), subject=subject))
    return commits


async def _leave_branch(ctx: ScriptContext, how: str) -> None:
    arguments = ["--detach"] if how == "detach" else ["-c", _OTHER_BRANCH]
    await ctx.runner.run(
        ["git", "switch", *arguments], timeout=_GIT_TIMEOUT_SECONDS, cwd=ctx.cwd, check=True
    )


def _unsupported(kind: ReportKind, step: TurnStep) -> NoReturn:
    message = f"fake agent step {step.kind!r} is not supported for report kind {kind.value!r}"
    raise ValueError(message)


def _new_tasks(specs: list[NewTaskSpec]) -> list[NewTask]:
    return [
        NewTask(title=spec.title, request=spec.request, depends_on=spec.depends_on)
        for spec in specs
    ]


def _triage_report(step: TurnStep, prompt_text: str) -> Report:
    if not isinstance(step, AcceptStep):
        _unsupported(ReportKind.TRIAGE, step)
    return TriageReport(
        title=step.triage_title,
        mode=Mode.IMPLEMENT,
        complexity=step.triage_complexity,
        acceptance_criteria=[prompt_text],
        references=[],
    )


def _plan_report(step: TurnStep, prompt_text: str, commits: list[Commit]) -> Report:
    if not isinstance(step, AcceptStep):
        _unsupported(ReportKind.PLAN, step)
    return PlanReport(
        summary=f"planned: {prompt_text}", commits=commits, new_tasks=_new_tasks(step.new_tasks)
    )


def _consult_report(step: TurnStep, prompt_text: str) -> Report:
    if isinstance(step, EscalateStep):
        return ConsultAnswer(
            decision="escalate",
            escalation=EscalationRequest(
                question=step.question or f"question: {prompt_text}",
                options=[Option(label="proceed", consequences="", recommended=True)],
            ),
        )
    if not isinstance(step, AcceptStep):
        _unsupported(ReportKind.CONSULT, step)
    return ConsultAnswer(decision="answer", answer=f"answer: {prompt_text}")


def _verdict_report(step: TurnStep, prompt_text: str) -> Report:
    if isinstance(step, AcceptStep):
        return Verdict(decision="accept", summary=f"verified: {prompt_text}")
    if isinstance(step, ChangesRequestedStep):
        return Verdict(
            decision="changes_requested",
            summary=f"issues found: {prompt_text}",
            issues=[Issue(severity="major", description=f"issue: {prompt_text}")],
        )
    if isinstance(step, SupersedeStep):
        return Verdict(
            decision="supersede",
            summary=f"superseded: {prompt_text}",
            new_tasks=_new_tasks(step.new_tasks),
        )
    _unsupported(ReportKind.VERDICT, step)


def _implementation_report(step: TurnStep, prompt_text: str, commits: list[Commit]) -> Report:
    if isinstance(step, AcceptStep):
        return ImplementationReport(
            status="completed",
            summary=f"implemented: {prompt_text}",
            commits=commits,
            tests=[],
            trackers_updated=True,
        )
    if isinstance(step, NeedsDecisionStep):
        return ImplementationReport(
            status="needs_decision",
            summary=f"blocked: {prompt_text}",
            commits=commits,
            tests=[],
            trackers_updated=False,
            question=step.question or f"question: {prompt_text}",
        )
    _unsupported(ReportKind.IMPLEMENTATION, step)


def _build_report(
    kind: ReportKind, step: TurnStep, prompt_text: str, commits: list[Commit]
) -> Report:
    if kind is ReportKind.TRIAGE:
        return _triage_report(step, prompt_text)
    if kind is ReportKind.PLAN:
        return _plan_report(step, prompt_text, commits)
    if kind is ReportKind.CONSULT:
        return _consult_report(step, prompt_text)
    if kind is ReportKind.VERDICT:
        return _verdict_report(step, prompt_text)
    return _implementation_report(step, prompt_text, commits)


def _current_report_kind(turn_dir: Path) -> ReportKind:
    return ReportKind((turn_dir / REPORT_KIND_FILENAME).read_text().strip())


def _write_report_file(role_path: Path, report: Report) -> Path:
    descriptor, temp_name = tempfile.mkstemp(
        dir=role_path, prefix=_REPORT_FILENAME_PREFIX, suffix=".json"
    )
    report_path = Path(temp_name)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(report.model_dump_json())
    return report_path


async def submit_scripted_report(ctx: ScriptContext, step: TurnStep, prompt_text: str) -> None:
    """Write a report for the fake turn and submit it through the installed command."""
    turn_dir = resolve_current_turn_dir(os.environ)
    kind = await asyncio.to_thread(_current_report_kind, turn_dir)
    if (
        isinstance(step, AcceptStep)
        and kind is ReportKind.IMPLEMENTATION
        and os.environ.get(TICK_A1_ENV_VAR) == TICK_A1_ENABLED_VALUE
    ):
        await asyncio.to_thread(_tick_a1, ctx.cwd)
    if isinstance(step, AcceptStep) and step.leave_branch:
        await _leave_branch(ctx, step.leave_branch)
    commits = (
        await _commit_changes(ctx.runner, ctx.cwd, prompt_text)
        if isinstance(step, AcceptStep) and kind in _KINDS_WITH_COMMITS
        else []
    )
    if isinstance(step, AcceptStep | SupersedeStep) and step.uncommitted:
        await asyncio.to_thread((ctx.cwd / step.uncommitted).write_text, prompt_text)
    report = _build_report(kind, step, prompt_text, commits)
    role_path = role_dir(ctx.run_dir, ctx.role)
    report_path = await asyncio.to_thread(_write_report_file, role_path, report)
    try:
        await ctx.runner.run(
            shlex.split(shikiru_command("report", "--file", str(report_path))),
            timeout=_REPORT_TIMEOUT_SECONDS,
            cwd=ctx.cwd,
        )
    finally:
        await asyncio.to_thread(report_path.unlink, missing_ok=True)
