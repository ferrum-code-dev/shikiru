"""The fake agent's REPL: reads prompts from stdin, scripted by `SHIKIRU_FAKE_SCRIPT`.

See the `fake_agent` package docstring for the full script format and the resume protocol.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

from fake_agent.agent import EXIT_INPUT, SESSION_ID_ENV_VAR
from fake_agent.script import (
    AcceptStep,
    ChangesRequestedStep,
    CrashStep,
    DialogStep,
    FailStep,
    NeedsDecisionStep,
    NoReportStep,
    Script,
    StallStep,
    TurnStep,
)
from fake_agent.signals import (
    attention_needed_payload,
    prompt_submitted_payload,
    session_ended_payload,
    session_started_payload,
    turn_ended_payload,
    turn_failed_payload,
    turn_tokens,
)
from shikiru.agents.contracts import ROLE_ENV_VAR, RUN_DIR_ENV_VAR, role_dir
from shikiru.agents.sdk.hooks import HookRecorder
from shikiru.agents.testing.resume import RESUME_RECALL_PROMPT
from shikiru.cli.report import resolve_current_turn_dir
from shikiru.domain.reports import (
    REPORT_KIND_FILENAME,
    Commit,
    ConsultAnswer,
    ImplementationReport,
    Issue,
    PlanReport,
    Report,
    ReportKind,
    TriageReport,
    Verdict,
)
from shikiru.domain.tasks import Mode
from shikiru.process import SubprocessRunner

SCRIPT_ENV_VAR = "SHIKIRU_FAKE_SCRIPT"

STATE_DIRNAME = "fake_state"
FAKE_CHANGE_FILENAME = "fake-agent-changes.txt"
REPORT_FILENAME_PREFIX = "fake-agent-report-"

_GIT_TIMEOUT_SECONDS = 10.0
_REPORT_TIMEOUT_SECONDS = 10.0
_MAX_COMMIT_SUBJECT_LENGTH = 72
_COMMIT_IDENTITY_ARGS = ("-c", "user.name=Fake Agent", "-c", "user.email=fake-agent@shikiru.test")

_KINDS_WITH_COMMITS = (ReportKind.IMPLEMENTATION, ReportKind.PLAN)


@dataclass(frozen=True, slots=True)
class ReplContext:
    """Everything the REPL's turn loop needs, resolved once at startup."""

    run_dir: Path
    role: str
    session_id: str
    recorder: HookRecorder
    runner: SubprocessRunner
    cwd: Path
    state_path: Path


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        message = f"fake-agent-repl needs {name} set; it must be launched by a Shikiru session"
        raise SystemExit(message)
    return value


def _build_context() -> ReplContext:
    run_dir = Path(_require_env(RUN_DIR_ENV_VAR))
    role = _require_env(ROLE_ENV_VAR)
    session_id = _require_env(SESSION_ID_ENV_VAR)
    role_path = role_dir(run_dir, role)
    state_dir = role_path / STATE_DIRNAME
    state_dir.mkdir(parents=True, exist_ok=True)
    return ReplContext(
        run_dir=run_dir,
        role=role,
        session_id=session_id,
        recorder=HookRecorder(role_path),
        runner=SubprocessRunner(),
        cwd=Path.cwd(),
        state_path=state_dir / f"{session_id}.json",
    )


def _load_prompts(state_path: Path) -> list[str]:
    if not state_path.is_file():
        return []
    return json.loads(state_path.read_text())["prompts"]


def _remember_prompt(state_path: Path, prompt_text: str) -> None:
    prompts = _load_prompts(state_path)
    prompts.append(prompt_text)
    state_path.write_text(json.dumps({"prompts": prompts}))


def _first_remembered_prompt(state_path: Path) -> str:
    prompts = _load_prompts(state_path)
    return prompts[0] if prompts else ""


def _append_change(marker: Path, prompt_text: str) -> None:
    previous = marker.read_text() if marker.is_file() else ""
    marker.write_text(previous + prompt_text + "\n")


async def _commit_change(runner: SubprocessRunner, cwd: Path, prompt_text: str) -> Commit:
    marker = cwd / FAKE_CHANGE_FILENAME
    await asyncio.to_thread(_append_change, marker, prompt_text)
    await runner.run(["git", "add", "-A"], timeout=_GIT_TIMEOUT_SECONDS, cwd=cwd, check=True)
    subject = f"fake: {prompt_text}"[:_MAX_COMMIT_SUBJECT_LENGTH]
    await runner.run(
        ["git", *_COMMIT_IDENTITY_ARGS, "commit", "-m", subject],
        timeout=_GIT_TIMEOUT_SECONDS,
        cwd=cwd,
        check=True,
    )
    result = await runner.run(
        ["git", "rev-parse", "HEAD"], timeout=_GIT_TIMEOUT_SECONDS, cwd=cwd, check=True
    )
    return Commit(repo=cwd.name, sha=result.stdout.strip(), subject=subject)


def _unsupported(kind: ReportKind, step: TurnStep) -> NoReturn:
    message = f"fake agent step {step.kind!r} is not supported for report kind {kind.value!r}"
    raise ValueError(message)


def _triage_report(step: TurnStep, prompt_text: str) -> Report:
    if not isinstance(step, AcceptStep):
        _unsupported(ReportKind.TRIAGE, step)
    return TriageReport(
        title="fake triage",
        mode=Mode.IMPLEMENT,
        complexity="normal",
        acceptance_criteria=[prompt_text],
        references=[],
    )


def _plan_report(step: TurnStep, prompt_text: str, commits: list[Commit]) -> Report:
    if not isinstance(step, AcceptStep):
        _unsupported(ReportKind.PLAN, step)
    return PlanReport(summary=f"planned: {prompt_text}", commits=commits)


def _consult_report(step: TurnStep, prompt_text: str) -> Report:
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
    kind: ReportKind, step: TurnStep, prompt_text: str, commit: Commit | None
) -> Report:
    commits = [commit] if commit else []
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


def _write_report_file(role_dir: Path, report: Report) -> Path:
    descriptor, temp_name = tempfile.mkstemp(
        dir=role_dir, prefix=REPORT_FILENAME_PREFIX, suffix=".json"
    )
    report_path = Path(temp_name)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(report.model_dump_json())
    return report_path


async def _submit_scripted_report(ctx: ReplContext, step: TurnStep, prompt_text: str) -> None:
    turn_dir = resolve_current_turn_dir(os.environ)
    kind = await asyncio.to_thread(_current_report_kind, turn_dir)
    commit = (
        await _commit_change(ctx.runner, ctx.cwd, prompt_text)
        if isinstance(step, AcceptStep) and kind in _KINDS_WITH_COMMITS
        else None
    )
    report = _build_report(kind, step, prompt_text, commit)
    role_path = role_dir(ctx.run_dir, ctx.role)
    report_path = await asyncio.to_thread(_write_report_file, role_path, report)
    try:
        await ctx.runner.run(
            ["shikiru", "report", "--file", str(report_path)],
            timeout=_REPORT_TIMEOUT_SECONDS,
            cwd=ctx.cwd,
            check=True,
        )
    finally:
        await asyncio.to_thread(report_path.unlink, missing_ok=True)


async def _apply_step(ctx: ReplContext, step: TurnStep, prompt_text: str) -> bool:
    if isinstance(step, StallStep):
        return True
    if isinstance(step, CrashStep):
        return False
    if isinstance(step, DialogStep):
        message = step.message or f"dialog: {prompt_text}"
        ctx.recorder.record(attention_needed_payload(ctx.session_id, message))
        return True
    if isinstance(step, FailStep):
        ctx.recorder.record(
            turn_failed_payload(
                ctx.session_id,
                step.failure_kind,
                message=f"fail: {prompt_text}",
                resets_at=step.resets_at,
            )
        )
        return True
    if isinstance(step, NoReportStep):
        ctx.recorder.record(
            turn_ended_payload(ctx.session_id, f"no_report: {prompt_text}", tokens=turn_tokens())
        )
        return True
    await _submit_scripted_report(ctx, step, prompt_text)
    ctx.recorder.record(
        turn_ended_payload(ctx.session_id, f"{step.kind}: {prompt_text}", tokens=turn_tokens())
    )
    return True


async def _handle_line(ctx: ReplContext, script: Script, prompt_text: str) -> bool:
    ctx.recorder.record(prompt_submitted_payload(ctx.session_id, prompt_text))
    if prompt_text == RESUME_RECALL_PROMPT:
        recalled = _first_remembered_prompt(ctx.state_path)
        ctx.recorder.record(turn_ended_payload(ctx.session_id, recalled, tokens=turn_tokens()))
        _remember_prompt(ctx.state_path, prompt_text)
        return True
    _remember_prompt(ctx.state_path, prompt_text)
    step = script.next_step()
    return await _apply_step(ctx, step, prompt_text)


async def _run() -> None:
    ctx = _build_context()
    script = Script.from_env(os.environ.get(SCRIPT_ENV_VAR))
    ctx.recorder.record(session_started_payload(ctx.session_id))
    loop = asyncio.get_running_loop()
    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:
            break
        prompt_text = line.rstrip("\n")
        if prompt_text == EXIT_INPUT:
            ctx.recorder.record(session_ended_payload(ctx.session_id))
            return
        keep_running = await _handle_line(ctx, script, prompt_text)
        if not keep_running:
            sys.exit(1)
    ctx.recorder.record(session_ended_payload(ctx.session_id))


def main() -> None:
    """Run the fake agent's REPL until stdin closes, `/exit` is read, or a `crash` exits it."""
    asyncio.run(_run())
