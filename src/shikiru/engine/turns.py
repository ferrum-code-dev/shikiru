"""`TurnRunner`: runs one agent turn to a `TurnResult`, never escalating (03 §4.3).

Turn dir, `before_turn` lines, pointer injection, submission confirmation, awaiting the
outcome, nudges and the Helper rebuild all live here. `pending_lines()` reports whichever
line this runner is currently injecting, so `SessionMonitor` (wired through
`SessionManager.set_pending_lines`) never mistakes it for an operator prompt (03 §9).
"""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from pydantic import ValidationError

from shikiru.agents.contracts import Compacted, PromptSubmitted, TurnEnded, TurnFailed, role_dir
from shikiru.domain.recovery import Completed, Failed, PaneDied, ProtocolError, Stalled
from shikiru.domain.reports import (
    CURRENT_TURN_LINK_NAME,
    HELPER_REPORT_FILENAME,
    PROMPT_FILENAME,
    REPORT_FILENAME,
    REPORT_KIND_FILENAME,
    REPORT_SCHEMA_FILENAME,
    model_for,
    schema_for,
)
from shikiru.helper.models import Unrecoverable
from shikiru.tmux.pane import ENTER_KEY

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping
    from datetime import datetime
    from pathlib import Path

    from shikiru.agents.contracts import AgentSession
    from shikiru.clock import Clock
    from shikiru.domain.recovery import TurnResult
    from shikiru.domain.reports import Report, ReportKind
    from shikiru.domain.tasks import Role, Step
    from shikiru.engine.monitor import MonitorSignal
    from shikiru.helper.helper import Helper
    from shikiru.prompts.renderer import PromptName, PromptRenderer
    from shikiru.tmux.pane import TmuxPane

TURNS_DIR_NAME = "turns"

SUBMIT_CONFIRM_SECONDS = 15
BEFORE_TURN_WAIT_SECONDS = 600
NUDGE_LIMIT = 2

_TURN_NUMBER_WIDTH = 2

_NUDGE_MESSAGE = (
    "Your turn ended without a valid report. Submit it with `shikiru report` as described "
    "at the end of {prompt_path}."
)

type _Outcome = TurnEnded | TurnFailed | PaneDied | Stalled
type _BeforeTurnSignal = Compacted | TurnEnded | TurnFailed | PaneDied | Stalled

_OUTCOME_TYPES = (TurnEnded, TurnFailed, PaneDied, Stalled)
_BEFORE_TURN_TYPES = (Compacted, TurnEnded, TurnFailed, PaneDied, Stalled)


@dataclass(frozen=True)
class TurnSpec:
    """Everything `TurnRunner.run` needs beyond the live session and pane (03 §4.3)."""

    step: Step
    iteration: int
    kind: ReportKind
    template: PromptName
    context: Mapping[str, object]
    prompt_prefix: str
    facts: str
    cwd: Path
    helper_model: str
    helper_effort: str | None
    helper_timeout: timedelta


def _load_report(turn_dir: Path, kind: ReportKind) -> Report | None:
    report_path = turn_dir / REPORT_FILENAME
    if not report_path.is_file():
        return None
    try:
        data = json.loads(report_path.read_text())
    except OSError, json.JSONDecodeError:
        return None
    try:
        return model_for(kind).model_validate(data)
    except ValidationError:
        return None


def _terminal_result(signal: TurnFailed | PaneDied | Stalled) -> TurnResult:
    if isinstance(signal, TurnFailed):
        return Failed(kind=signal.kind, resets_at=signal.resets_at, message=signal.message)
    return signal


class TurnRunner:
    """Runs one turn to completion: turn dir → before-turn → pointer → await → nudges."""

    def __init__(self, renderer: PromptRenderer, clock: Clock) -> None:
        """Render prompts with `renderer`, time every wait and delay through `clock`."""
        self._renderer = renderer
        self._clock = clock
        self._pending_line: str | None = None

    def pending_lines(self) -> Collection[str]:
        """Return the line this runner is currently injecting, for `SessionMonitor` (03 §9)."""
        return () if self._pending_line is None else (self._pending_line,)

    async def run(
        self,
        session: AgentSession,
        pane: TmuxPane,
        signals: asyncio.Queue[MonitorSignal],
        *,
        run_dir: Path,
        role: Role,
        spec: TurnSpec,
        helper: Helper,
    ) -> TurnResult:
        """Run one turn for `role` and return its `TurnResult`; never raises for agent behaviour."""
        turn_dir = self._prepare_turn_dir(run_dir, role, spec)
        before_turn_result = await self._run_before_turn(session, pane, signals)
        if before_turn_result is not None:
            return before_turn_result
        prompt_path = turn_dir / PROMPT_FILENAME
        outcome = await self._submit_pointer(pane, signals, spec.prompt_prefix, prompt_path)
        return await self._resolve(
            outcome,
            pane,
            signals,
            turn_dir=turn_dir,
            spec=spec,
            helper=helper,
            prompt_path=prompt_path,
        )

    def _prepare_turn_dir(self, run_dir: Path, role: Role, spec: TurnSpec) -> Path:
        turns_root = run_dir / TURNS_DIR_NAME
        turns_root.mkdir(parents=True, exist_ok=True)
        number = sum(1 for entry in turns_root.iterdir() if entry.is_dir()) + 1
        dirname = f"{number:0{_TURN_NUMBER_WIDTH}d}-{spec.step.value}-i{spec.iteration}"
        turn_dir = turns_root / dirname
        turn_dir.mkdir(parents=True, exist_ok=True)
        (turn_dir / REPORT_FILENAME).unlink(missing_ok=True)
        (turn_dir / PROMPT_FILENAME).write_text(self._renderer.render(spec.template, spec.context))
        (turn_dir / REPORT_SCHEMA_FILENAME).write_text(json.dumps(schema_for(spec.kind), indent=2))
        (turn_dir / REPORT_KIND_FILENAME).write_text(spec.kind.value)
        self._point_current_turn(run_dir, role, turn_dir)
        return turn_dir

    @staticmethod
    def _point_current_turn(run_dir: Path, role: Role, turn_dir: Path) -> None:
        link = role_dir(run_dir, role.value) / CURRENT_TURN_LINK_NAME
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(turn_dir)

    async def _run_before_turn(
        self, session: AgentSession, pane: TmuxPane, signals: asyncio.Queue[MonitorSignal]
    ) -> TurnResult | None:
        for line in await session.before_turn():
            deadline = self._clock.now() + timedelta(seconds=BEFORE_TURN_WAIT_SECONDS)
            await self._inject(pane, line)
            signal = await self._wait_for_before_turn(signals, deadline)
            self._pending_line = None
            if signal is None:
                return Stalled(screen=await pane.capture_visible())
            if isinstance(signal, TurnFailed | PaneDied | Stalled):
                return _terminal_result(signal)
        return None

    async def _submit_pointer(
        self,
        pane: TmuxPane,
        signals: asyncio.Queue[MonitorSignal],
        prompt_prefix: str,
        prompt_path: Path,
    ) -> _Outcome | PromptSubmitted:
        line = f"{prompt_prefix}Read {prompt_path} and follow it exactly."
        confirmed = await self._confirm(pane, signals, line, prompt_path, inject=True)
        if confirmed is not None:
            return confirmed
        confirmed = await self._confirm(pane, signals, line, prompt_path, inject=False)
        if confirmed is not None:
            return confirmed
        return Stalled(screen=await pane.capture_visible())

    async def _confirm(
        self,
        pane: TmuxPane,
        signals: asyncio.Queue[MonitorSignal],
        line: str,
        prompt_path: Path,
        *,
        inject: bool,
    ) -> _Outcome | PromptSubmitted | None:
        self._pending_line = line
        if inject:
            await pane.paste_line(line)
        else:
            await pane.send_keys(ENTER_KEY)
        deadline = self._clock.now() + timedelta(seconds=SUBMIT_CONFIRM_SECONDS)
        try:
            while True:
                remaining = (deadline - self._clock.now()).total_seconds()
                if remaining <= 0:
                    return None
                signal = await self._wait_for(signals, remaining)
                if signal is None:
                    return None
                if isinstance(signal, _OUTCOME_TYPES):
                    return signal
                if isinstance(signal, PromptSubmitted) and str(prompt_path) in signal.text:
                    return signal
        finally:
            self._pending_line = None

    async def _resolve(
        self,
        outcome: _Outcome | PromptSubmitted,
        pane: TmuxPane,
        signals: asyncio.Queue[MonitorSignal],
        *,
        turn_dir: Path,
        spec: TurnSpec,
        helper: Helper,
        prompt_path: Path,
    ) -> TurnResult:
        nudges = 0
        current: _Outcome | PromptSubmitted = outcome
        while True:
            if isinstance(current, PromptSubmitted):
                current = await self._await_outcome(signals)
                continue
            if isinstance(current, TurnFailed | PaneDied | Stalled):
                return _terminal_result(current)
            report = _load_report(turn_dir, spec.kind)
            if report is not None:
                return Completed(report=report)
            if nudges >= NUDGE_LIMIT:
                return await self._rebuild(turn_dir, spec, helper, current.last_message)
            nudges += 1
            await self._inject(pane, _NUDGE_MESSAGE.format(prompt_path=prompt_path))
            current = await self._await_outcome(signals)
            self._pending_line = None

    async def _rebuild(
        self, turn_dir: Path, spec: TurnSpec, helper: Helper, last_message: str
    ) -> TurnResult:
        rebuilt = await helper.rebuild_report(
            spec.kind,
            last_message,
            spec.facts,
            model=spec.helper_model,
            effort=spec.helper_effort,
            cwd=spec.cwd,
            timeout=spec.helper_timeout,
        )
        if isinstance(rebuilt, Unrecoverable):
            return ProtocolError()
        (turn_dir / HELPER_REPORT_FILENAME).write_text(rebuilt.model_dump_json(indent=2))
        return Completed(report=rebuilt, rebuilt=True)

    async def _inject(self, pane: TmuxPane, line: str) -> None:
        self._pending_line = line
        await pane.paste_line(line)

    async def _await_outcome(self, signals: asyncio.Queue[MonitorSignal]) -> _Outcome:
        while True:
            signal = await signals.get()
            if isinstance(signal, _OUTCOME_TYPES):
                return signal

    async def _wait_for_before_turn(
        self, signals: asyncio.Queue[MonitorSignal], deadline: datetime
    ) -> _BeforeTurnSignal | None:
        while True:
            remaining = (deadline - self._clock.now()).total_seconds()
            if remaining <= 0:
                return None
            signal = await self._wait_for(signals, remaining)
            if signal is None:
                return None
            if isinstance(signal, _BEFORE_TURN_TYPES):
                return signal

    async def _wait_for(
        self, signals: asyncio.Queue[MonitorSignal], timeout_seconds: float
    ) -> MonitorSignal | None:
        get_task = asyncio.ensure_future(signals.get())
        sleep_task = asyncio.ensure_future(self._clock.sleep(timeout_seconds))
        try:
            await asyncio.wait({get_task, sleep_task}, return_when=asyncio.FIRST_COMPLETED)
            if get_task.done():
                return get_task.result()
            return None
        finally:
            if not get_task.done():
                get_task.cancel()
                with suppress(asyncio.CancelledError):
                    await get_task
            if not sleep_task.done():
                sleep_task.cancel()
                with suppress(asyncio.CancelledError):
                    await sleep_task
