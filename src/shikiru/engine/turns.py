"""`TurnRunner`: runs one agent turn to a `TurnResult`, never escalating (03 §4.3).

Turn dir, `before_turn` lines, pointer injection, submission confirmation, awaiting the
outcome, nudges and the Helper rebuild all live here. `TurnRunner` keeps no per-turn state of
its own: the caller passes the session's own `InjectedLines` (from `SessionManager`), and this
runner records each line it injects there for as long as it stays pending, so `SessionMonitor`
never mistakes it for an operator prompt (03 §9). A single `TurnRunner` is safe to share across
concurrently running turns of different tasks.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, cast

from shikiru.agents.contracts import Compacted, PromptSubmitted, TurnEnded, TurnFailed, role_dir
from shikiru.domain.recovery import Completed, Failed, PaneDied, ProtocolError, Stalled
from shikiru.domain.reports import (
    CURRENT_TURN_LINK_NAME,
    FACTS_FILENAME,
    HELPER_REPORT_FILENAME,
    PROMPT_FILENAME,
    REPORT_FILENAME,
    REPORT_KIND_FILENAME,
    REPORT_SCHEMA_FILENAME,
    ReportKind,
    schema_for,
)
from shikiru.engine.monitor import wait_for_signal
from shikiru.errors import CommandFailed
from shikiru.helper.models import Unrecoverable
from shikiru.tmux.pane import ENTER_KEY
from shikiru.workspace.reports import InvalidTurnReportError, load_turn_report

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from datetime import datetime
    from pathlib import Path

    from shikiru.agents.contracts import AgentSession
    from shikiru.clock import Clock
    from shikiru.domain.recovery import TurnResult
    from shikiru.domain.reports import Report
    from shikiru.domain.tasks import Role, Step
    from shikiru.engine.monitor import InjectedLines, MonitorSignal
    from shikiru.helper.helper import Helper
    from shikiru.prompts.renderer import PromptName, PromptRenderer
    from shikiru.tmux.pane import TmuxPane

TURNS_DIR_NAME = "turns"
_LOGGER = logging.getLogger(__name__)

SUBMIT_CONFIRM_SECONDS = 15
BEFORE_TURN_WAIT_SECONDS = 600
NUDGE_LIMIT = 2

_TURN_NUMBER_WIDTH = 2

_NUDGE_MESSAGE = (
    "Your turn ended without a valid report. Submit it with `shikiru report` as described "
    "at the end of {prompt_path}."
)

type _Outcome = TurnEnded | TurnFailed | PaneDied | Stalled

_OUTCOME_TYPES = (TurnEnded, TurnFailed, PaneDied, Stalled)
_BEFORE_TURN_TYPES = (Compacted, TurnEnded, TurnFailed, PaneDied, Stalled)
_CONFIRM_TYPES = (*_OUTCOME_TYPES, PromptSubmitted)


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


def read_turn_report(turn_dir: Path) -> Report | None:
    """Read `turn_dir`'s submitted report or Helper rebuild; invalid files raise explicitly."""
    loaded = load_turn_report(turn_dir)
    return loaded.report if loaded is not None else None


def _read_turn_report_or_none(turn_dir: Path) -> Report | None:
    try:
        return read_turn_report(turn_dir)
    except InvalidTurnReportError:
        _LOGGER.exception("invalid report in %s", turn_dir)
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
        injected: InjectedLines,
        intervention_active: Callable[[], bool] | None = None,
        on_nudge: Callable[[], None] | None = None,
    ) -> TurnResult:
        """Run one turn for `role` and return its `TurnResult`; never raises for agent behaviour."""
        turn_dir = self._prepare_turn_dir(run_dir, role, spec)
        before_turn_result = await self._run_before_turn(session, pane, signals, injected)
        if before_turn_result is not None:
            return before_turn_result
        prompt_path = turn_dir / PROMPT_FILENAME
        outcome = await self._submit_pointer(
            pane, signals, spec.prompt_prefix, prompt_path, injected
        )
        return await self._resolve(
            outcome,
            pane,
            signals,
            turn_dir=turn_dir,
            spec=spec,
            helper=helper,
            prompt_path=prompt_path,
            injected=injected,
            intervention_active=intervention_active,
            on_nudge=on_nudge,
        )

    async def resume(
        self,
        pane: TmuxPane,
        signals: asyncio.Queue[MonitorSignal],
        *,
        turn_dir: Path,
        spec: TurnSpec,
        helper: Helper,
        prompt_path: Path,
        injected: InjectedLines,
        intervention_active: Callable[[], bool] | None = None,
        on_nudge: Callable[[], None] | None = None,
    ) -> TurnResult:
        """Continue an existing turn after recovery input, without creating a new turn dir."""
        outcome = await self._await_outcome(signals)
        return await self._resolve(
            outcome,
            pane,
            signals,
            turn_dir=turn_dir,
            spec=spec,
            helper=helper,
            prompt_path=prompt_path,
            injected=injected,
            intervention_active=intervention_active,
            on_nudge=on_nudge,
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
        if spec.facts:
            (turn_dir / FACTS_FILENAME).write_text(spec.facts)
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
        self,
        session: AgentSession,
        pane: TmuxPane,
        signals: asyncio.Queue[MonitorSignal],
        injected: InjectedLines,
    ) -> TurnResult | None:
        for line in await session.before_turn():
            deadline = self._clock.now() + timedelta(seconds=BEFORE_TURN_WAIT_SECONDS)
            injected.add(line)
            try:
                await pane.paste_line(line)
            except CommandFailed:
                return PaneDied()
            signal = await self._next_signal(signals, _BEFORE_TURN_TYPES, deadline)
            if signal is None:
                try:
                    screen = await pane.capture_visible()
                except CommandFailed:
                    return PaneDied()
                return Stalled(screen=screen)
            if isinstance(signal, TurnFailed | PaneDied | Stalled):
                return _terminal_result(signal)
        return None

    async def _submit_pointer(
        self,
        pane: TmuxPane,
        signals: asyncio.Queue[MonitorSignal],
        prompt_prefix: str,
        prompt_path: Path,
        injected: InjectedLines,
    ) -> _Outcome | PromptSubmitted:
        line = f"{prompt_prefix}Read {prompt_path} and follow it exactly."
        confirmed = await self._confirm(pane, signals, line, prompt_path, injected, inject=True)
        if confirmed is not None:
            return confirmed
        confirmed = await self._confirm(pane, signals, line, prompt_path, injected, inject=False)
        if confirmed is not None:
            return confirmed
        try:
            screen = await pane.capture_visible()
        except CommandFailed:
            return PaneDied()
        return Stalled(screen=screen)

    async def _confirm(
        self,
        pane: TmuxPane,
        signals: asyncio.Queue[MonitorSignal],
        line: str,
        prompt_path: Path,
        injected: InjectedLines,
        *,
        inject: bool,
    ) -> _Outcome | PromptSubmitted | None:
        if inject:
            injected.add(line)
        try:
            if inject:
                await pane.paste_line(line)
            else:
                await pane.send_keys(ENTER_KEY)
        except CommandFailed:
            if inject:
                injected.discard(line)
            return PaneDied()
        deadline = self._clock.now() + timedelta(seconds=SUBMIT_CONFIRM_SECONDS)
        while True:
            signal = await self._next_signal(signals, _CONFIRM_TYPES, deadline)
            if signal is None:
                return None
            if isinstance(signal, PromptSubmitted):
                if str(prompt_path) in signal.text:
                    return signal
                continue
            return cast("_Outcome", signal)

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
        injected: InjectedLines,
        intervention_active: Callable[[], bool] | None,
        on_nudge: Callable[[], None] | None,
    ) -> TurnResult:
        is_intervention_active = intervention_active or (lambda: False)
        nudges = 0
        current: _Outcome | PromptSubmitted = outcome
        while True:
            if isinstance(current, PromptSubmitted):
                current = await self._await_outcome(signals)
                continue
            if isinstance(current, Stalled) and is_intervention_active():
                current = await self._await_outcome(signals)
                continue
            if isinstance(current, TurnFailed | PaneDied | Stalled):
                return _terminal_result(current)
            report = _read_turn_report_or_none(turn_dir)
            if report is not None:
                return Completed(report=report)
            if is_intervention_active():
                current = await self._await_outcome(signals)
                continue
            if nudges >= NUDGE_LIMIT:
                return await self._rebuild(turn_dir, spec, helper, current.last_message)
            nudges += 1
            if on_nudge is not None:
                on_nudge()
            nudge_line = _NUDGE_MESSAGE.format(prompt_path=prompt_path)
            injected.add(nudge_line)
            try:
                await pane.paste_line(nudge_line)
            except CommandFailed:
                return PaneDied()
            current = await self._await_outcome(signals)

    async def _rebuild(
        self, turn_dir: Path, spec: TurnSpec, helper: Helper, last_message: str
    ) -> TurnResult:
        try:
            rebuilt = await helper.rebuild_report(
                spec.kind,
                last_message,
                spec.facts,
                model=spec.helper_model,
                effort=spec.helper_effort,
                cwd=spec.cwd,
                timeout=spec.helper_timeout,
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            return ProtocolError()
        if isinstance(rebuilt, Unrecoverable):
            return ProtocolError()
        (turn_dir / HELPER_REPORT_FILENAME).write_text(rebuilt.model_dump_json(indent=2))
        return Completed(report=rebuilt, rebuilt=True)

    async def _await_outcome(self, signals: asyncio.Queue[MonitorSignal]) -> _Outcome:
        return cast("_Outcome", await self._next_signal(signals, _OUTCOME_TYPES, None))

    async def _next_signal(
        self,
        signals: asyncio.Queue[MonitorSignal],
        accept: tuple[type, ...],
        deadline: datetime | None,
    ) -> MonitorSignal | None:
        """Return the next signal in `signals` matching `accept`, discarding any others.

        With `deadline` set, gives up and returns `None` once it passes; with `deadline` set to
        `None`, waits indefinitely. Shared by the before-turn wait, the pointer confirmation
        wait and the outcome wait, which differ only in their accepted signals and deadline.
        """
        while True:
            if deadline is not None:
                remaining = (deadline - self._clock.now()).total_seconds()
                if remaining <= 0:
                    return None
                signal = await self._wait_for(signals, remaining)
                if signal is None:
                    return None
            else:
                signal = await signals.get()
            if isinstance(signal, accept):
                return signal

    async def _wait_for(
        self, signals: asyncio.Queue[MonitorSignal], timeout_seconds: float
    ) -> MonitorSignal | None:
        return await wait_for_signal(signals, self._clock, timeout_seconds)
