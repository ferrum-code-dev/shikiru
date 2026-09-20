"""Codex interactive session backed by hooks and a rollout."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from shikiru.agents.contracts import AgentSignal, SessionStarted, Usage, role_dir
from shikiru.agents.sdk.hooks import HOOKS_FILENAME, HookRecorder
from shikiru.agents.sdk.jsonl import JsonlTail
from shikiru.plugins.agents.codex.signals import (
    HOOK_EVENT_FIELD,
    ROLLOUT_PATH_FIELD,
    SESSION_ID_FIELD,
    SESSION_START_EVENT,
    hook_signal,
)
from shikiru.plugins.agents.codex.usage import rollout_usage

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from shikiru.agents.contracts import AgentSession, LaunchSpec
    from shikiru.agents.sdk.prices import PriceTable
    from shikiru.agents.sdk.reset_time import ResetTimeParser
    from shikiru.clock import Clock

POLL_INTERVAL_SECONDS = 0.05
COMPACT_COMMAND = "/compact"
_SESSION_ID_STATE_KEY = "session_id"
_HOOK_OFFSET_STATE_KEY = "offset"
_ROLLOUT_PATH_STATE_KEY = "rollout_path"
_INVALID_SESSION_STATE_MESSAGE = "invalid Codex session state"
_INVALID_ROLLOUT_STATE_MESSAGE = "invalid Codex rollout path state"
_INVALID_HOOK_PAYLOAD_MESSAGE = "Codex hook payload must be an object"


class CodexSession:
    """An `AgentSession` that tails Codex hooks and rollout files."""

    def __init__(
        self,
        run_dir: Path,
        role: str,
        session_id: str | None,
        hook_offset: int,
        rollout_path: Path | None,
        *,
        clock: Clock,
        reset_time: ResetTimeParser,
        prices: PriceTable,
        model: str,
        api_key_auth: bool,
        compact_below_free_pct: int,
        codex_home: Path,
        resume_requested: bool,
    ) -> None:
        """Tail one role's Codex files from `hook_offset` and retain attach state."""
        self._role_path = role_dir(run_dir, role)
        self._recorder = HookRecorder(self._role_path)
        self._session_id = session_id
        self._hook_offset = hook_offset
        self._rollout_path = rollout_path
        self._clock = clock
        self._reset_time = reset_time
        self._prices = prices
        self._model = model
        self._api_key_auth = api_key_auth
        self._compact_below_free_pct = compact_below_free_pct
        self._codex_home = codex_home
        self._resume_requested = resume_requested and hook_offset > 0
        self._resume_started_emitted = False

    async def signals(self) -> AsyncGenerator[AgentSignal]:
        """Yield Codex hook signals appended after this session's read offset."""
        if self._resume_requested and not self._resume_started_emitted:
            self._resume_started_emitted = True
            yield SessionStarted(session_id=self._session_id)
        while True:
            advanced = False
            for entry in self._recorder.tail(self._hook_offset):
                self._hook_offset = entry.offset
                advanced = True
                payload = entry.value
                if not isinstance(payload, Mapping):
                    raise TypeError(_INVALID_HOOK_PAYLOAD_MESSAGE)
                typed_payload = cast("Mapping[str, Any]", payload)
                if not self._belongs_to_session(typed_payload):
                    continue
                self._update_session_state(typed_payload)
                signal = hook_signal(
                    typed_payload,
                    rollout=self._rollout_entries(),
                    reset_time=self._reset_time,
                )
                if (
                    typed_payload.get(HOOK_EVENT_FIELD) == SESSION_START_EVENT
                    and self._resume_started_emitted
                ):
                    continue
                if typed_payload.get(HOOK_EVENT_FIELD) == SESSION_START_EVENT:
                    self._resume_started_emitted = True
                yield signal
            if not advanced:
                await self._clock.sleep(POLL_INTERVAL_SECONDS)

    async def before_turn(self) -> list[str]:
        """Request Codex compaction when the latest turn leaves too little free context."""
        usage = await self.usage()
        if usage.context_used_pct is None:
            return []
        free_pct = 100 - usage.context_used_pct
        return [COMPACT_COMMAND] if free_pct < self._compact_below_free_pct else []

    async def usage(self) -> Usage:
        """Return cumulative token, cost and context usage from the rollout."""
        return rollout_usage(
            self._rollout_entries(),
            model=self._model,
            prices=self._prices,
            api_key_auth=self._api_key_auth,
        )

    def snapshot(self) -> Mapping[str, Any]:
        """Return offsets and rollout path needed for `attach()`."""
        return {
            _SESSION_ID_STATE_KEY: self._session_id,
            _HOOK_OFFSET_STATE_KEY: self._hook_offset,
            _ROLLOUT_PATH_STATE_KEY: (
                str(self._rollout_path) if self._rollout_path is not None else None
            ),
        }

    def _belongs_to_session(self, payload: Mapping[str, Any]) -> bool:
        event_session_id = payload.get(SESSION_ID_FIELD)
        if event_session_id is not None and not isinstance(event_session_id, str):
            message = f"Codex hook entry has no string {SESSION_ID_FIELD!r}"
            raise TypeError(message)
        if payload.get(HOOK_EVENT_FIELD) == SESSION_START_EVENT:
            return True
        return (
            self._session_id is None
            or event_session_id is None
            or event_session_id == self._session_id
        )

    def _update_session_state(self, payload: Mapping[str, Any]) -> None:
        event = payload.get(HOOK_EVENT_FIELD)
        event_session_id = payload.get(SESSION_ID_FIELD)
        if event == SESSION_START_EVENT and isinstance(event_session_id, str):
            self._session_id = event_session_id
        rollout_path = payload.get(ROLLOUT_PATH_FIELD)
        if rollout_path is not None and not isinstance(rollout_path, str):
            message = f"Codex hook entry has no string {ROLLOUT_PATH_FIELD!r}"
            raise TypeError(message)
        if isinstance(rollout_path, str) and rollout_path:
            candidate = Path(rollout_path)
            self._rollout_path = (
                candidate if candidate.is_absolute() else self._role_path / candidate
            )
        if self._rollout_path is None and self._session_id is not None:
            self._rollout_path = self._discover_rollout(self._session_id)

    def _rollout_entries(self) -> list[Mapping[str, Any]]:
        if self._rollout_path is None and self._session_id is not None:
            self._rollout_path = self._discover_rollout(self._session_id)
        if self._rollout_path is None:
            return []
        return [
            cast("Mapping[str, Any]", entry.value)
            for entry in JsonlTail(self._rollout_path).read_from(0)
            if isinstance(entry.value, Mapping)
        ]

    def _discover_rollout(self, session_id: str) -> Path | None:
        sessions = self._codex_home / "sessions"
        if not sessions.is_dir():
            return None
        suffix = f"-{session_id}.jsonl"
        paths = [path for path in sessions.rglob("rollout-*.jsonl") if path.name.endswith(suffix)]
        return max(paths, key=lambda path: path.stat().st_mtime_ns) if paths else None


def hook_file_size(run_dir: Path, role: str) -> int:
    """Return the current byte size of the role hook file."""
    path = role_dir(run_dir, role) / HOOKS_FILENAME
    return path.stat().st_size if path.is_file() else 0


def restored_session(
    spec: LaunchSpec,
    *,
    state: Mapping[str, Any],
    clock: Clock,
    reset_time: ResetTimeParser,
    prices: PriceTable,
    api_key_auth: bool,
    compact_below_free_pct: int,
    codex_home: Path,
) -> AgentSession:
    """Build a Codex session from persisted plugin state."""
    session_id = state.get(_SESSION_ID_STATE_KEY)
    hook_offset = state.get(_HOOK_OFFSET_STATE_KEY)
    if session_id is not None and not isinstance(session_id, str):
        raise TypeError(_INVALID_SESSION_STATE_MESSAGE)
    if not isinstance(hook_offset, int):
        raise TypeError(_INVALID_SESSION_STATE_MESSAGE)
    rollout_value = state.get(_ROLLOUT_PATH_STATE_KEY)
    if rollout_value is not None and not isinstance(rollout_value, str):
        raise ValueError(_INVALID_ROLLOUT_STATE_MESSAGE)
    if rollout_value is None:
        rollout_path = None
    else:
        candidate = Path(rollout_value)
        rollout_path = (
            candidate if candidate.is_absolute() else role_dir(spec.run_dir, spec.role) / candidate
        )
    return CodexSession(
        spec.run_dir,
        spec.role,
        session_id,
        hook_offset,
        rollout_path,
        clock=clock,
        reset_time=reset_time,
        prices=prices,
        model=spec.rung.model,
        api_key_auth=api_key_auth,
        compact_below_free_pct=compact_below_free_pct,
        codex_home=codex_home,
        resume_requested=False,
    )


def codex_home() -> Path:
    """Return the Codex home used for rollout discovery."""
    configured = os.environ.get("CODEX_HOME")
    return Path(configured) if configured else Path.home() / ".codex"
