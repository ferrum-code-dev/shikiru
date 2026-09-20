"""Claude Code interactive session backed by hooks and a transcript."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from shikiru.agents.contracts import AgentSignal, TurnFailed, Usage, role_dir
from shikiru.agents.sdk.hooks import HOOKS_FILENAME, HookRecorder
from shikiru.agents.sdk.jsonl import JsonlTail
from shikiru.plugins.agents.claude.signals import (
    HOOK_EVENT_FIELD,
    SESSION_ID_FIELD,
    SESSION_START_EVENT,
    TRANSCRIPT_PATH_FIELD,
    hook_signal,
)
from shikiru.plugins.agents.claude.usage import transcript_usage

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable

    from shikiru.agents.contracts import AgentSession, LaunchSpec
    from shikiru.agents.sdk.prices import PriceTable
    from shikiru.agents.sdk.reset_time import ResetTimeParser
    from shikiru.clock import Clock

POLL_INTERVAL_SECONDS = 0.05
_SESSION_ID_STATE_KEY = "session_id"
_HOOK_OFFSET_STATE_KEY = "offset"
_TRANSCRIPT_PATH_STATE_KEY = "transcript_path"
_MISSING_TRANSCRIPT_PATH_MESSAGE = "Claude SessionStart payload has no transcript path"
_INVALID_SESSION_STATE_MESSAGE = "invalid Claude session state"
_INVALID_TRANSCRIPT_STATE_MESSAGE = "invalid Claude transcript path state"
_INVALID_HOOK_PAYLOAD_MESSAGE = "Claude hook payload must be an object"


class ClaudeSession:
    """An `AgentSession` that tails Claude Code's hook and transcript files."""

    def __init__(
        self,
        run_dir: Path,
        role: str,
        session_id: str,
        hook_offset: int,
        *,
        clock: Clock,
        reset_time: ResetTimeParser,
        prices: PriceTable,
        model: str,
        api_key_auth: bool,
        invalidate_quota: Callable[[], None],
        transcript_path: str | None = None,
    ) -> None:
        """Tail one role's Claude files from `hook_offset` and keep its transcript state."""
        self._role_path = role_dir(run_dir, role)
        self._recorder = HookRecorder(self._role_path)
        self._session_id = session_id
        self._hook_offset = hook_offset
        self._clock = clock
        self._reset_time = reset_time
        self._prices = prices
        self._model = model
        self._api_key_auth = api_key_auth
        self._invalidate_quota = invalidate_quota
        self._transcript_path = self._resolve_transcript(transcript_path)

    async def signals(self) -> AsyncGenerator[AgentSignal]:
        """Yield Claude hook signals appended after this session's read offset."""
        while True:
            advanced = False
            for entry in self._recorder.tail(self._hook_offset):
                self._hook_offset = entry.offset
                advanced = True
                payload = entry.value
                if not isinstance(payload, Mapping):
                    raise TypeError(_INVALID_HOOK_PAYLOAD_MESSAGE)
                typed_payload = cast("Mapping[str, Any]", payload)
                if typed_payload.get(SESSION_ID_FIELD) != self._session_id:
                    continue
                transcript_path = typed_payload.get(TRANSCRIPT_PATH_FIELD)
                if isinstance(transcript_path, str):
                    self._transcript_path = self._resolve_transcript(transcript_path)
                if typed_payload.get(HOOK_EVENT_FIELD) == SESSION_START_EVENT and not isinstance(
                    transcript_path, str
                ):
                    raise ValueError(_MISSING_TRANSCRIPT_PATH_MESSAGE)
                signal = hook_signal(
                    typed_payload,
                    transcript=self._transcript_entries(),
                    reset_time=self._reset_time,
                )
                if isinstance(signal, TurnFailed):
                    self._invalidate_quota()
                yield signal
            if not advanced:
                await self._clock.sleep(POLL_INTERVAL_SECONDS)

    async def before_turn(self) -> list[str]:
        """Return no pre-turn input because Claude Code auto-compacts its context."""
        return []

    async def usage(self) -> Usage:
        """Return cumulative token, cost and context usage from the transcript."""
        return transcript_usage(
            self._transcript_entries(),
            model=self._model,
            prices=self._prices,
            api_key_auth=self._api_key_auth,
        )

    def snapshot(self) -> Mapping[str, Any]:
        """Return offsets and transcript path needed for `attach()`."""
        return {
            _SESSION_ID_STATE_KEY: self._session_id,
            _HOOK_OFFSET_STATE_KEY: self._hook_offset,
            _TRANSCRIPT_PATH_STATE_KEY: (
                str(self._transcript_path) if self._transcript_path is not None else None
            ),
        }

    def _transcript_entries(self) -> list[Mapping[str, Any]]:
        if self._transcript_path is None:
            return []
        return [
            cast("Mapping[str, Any]", entry.value)
            for entry in JsonlTail(self._transcript_path).read_from(0)
            if isinstance(entry.value, Mapping)
        ]

    def _resolve_transcript(self, path: str | None) -> Path | None:
        if path is None:
            return None
        candidate = Path(path)
        if candidate.is_absolute():
            if candidate.is_file():
                return candidate
            role_candidate = self._role_path / candidate.name
            return role_candidate if role_candidate.is_file() else candidate
        return self._role_path / candidate


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
    invalidate_quota: Callable[[], None],
) -> AgentSession:
    """Build a session from persisted plugin state."""
    session_id = state[_SESSION_ID_STATE_KEY]
    hook_offset = state[_HOOK_OFFSET_STATE_KEY]
    if not isinstance(session_id, str) or not isinstance(hook_offset, int):
        raise TypeError(_INVALID_SESSION_STATE_MESSAGE)
    transcript_path = state.get(_TRANSCRIPT_PATH_STATE_KEY)
    if transcript_path is not None and not isinstance(transcript_path, str):
        raise ValueError(_INVALID_TRANSCRIPT_STATE_MESSAGE)
    return ClaudeSession(
        spec.run_dir,
        spec.role,
        session_id,
        hook_offset,
        clock=clock,
        reset_time=reset_time,
        prices=prices,
        model=spec.rung.model,
        api_key_auth=api_key_auth,
        invalidate_quota=invalidate_quota,
        transcript_path=transcript_path,
    )
