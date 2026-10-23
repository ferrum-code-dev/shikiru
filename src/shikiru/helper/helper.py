"""Calls configured Helper headless agents in order and validates every answer (04 §6)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from shikiru.agents.contracts import FailureKind, Rung
from shikiru.domain.fencing import fence
from shikiru.domain.reports import ReportKind, model_for, schema_for
from shikiru.errors import (
    CommandFailed,
    CommandTimeoutError,
    ConfigError,
    HeadlessOutputError,
)
from shikiru.helper.models import Unrecoverable

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import timedelta
    from pathlib import Path

    from shikiru.agents.contracts import Agent, HeadlessAgent
    from shikiru.clock import Clock
    from shikiru.domain.reports import Report

_REBUILD_PROMPT = (
    "The agent's turn ended without a valid report. Reconstruct the report for this {kind} "
    "turn from its last message and the facts below. If you cannot determine the report with "
    'confidence, respond instead with \'{{"unrecoverable": "<reason>"}}.\n\n'
    "## Last message\n\n{last_message}\n\n## Facts\n\n{facts}\n"
)
_CLASSIFY_PROMPT = (
    "Classify this unrecognised agent failure into QUOTA, UNAVAILABLE, AUTH or OTHER. "
    "Give confidence from 0 to 1 and a reset time only when known. If the text is ambiguous, "
    "return UNKNOWN with low confidence.\n\n## Failure\n\n{failure}\n"
)
_EXPECTED_CALL_ERRORS = (
    CommandFailed,
    CommandTimeoutError,
    HeadlessOutputError,
    OSError,
    TimeoutError,
)
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class HelperEntry:
    """One configured Helper rung and the agent selected for it."""

    rung: Rung
    agent: Agent


@dataclass(frozen=True)
class _HeadlessEntry:
    rung: Rung
    headless: HeadlessAgent


class FailureClassification(BaseModel):
    """The Helper's structured classification of an unrecognised agent failure."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: FailureKind
    confidence: float = Field(ge=0, le=1)
    resets_at: datetime | None = None


class Helper:
    """Call configured headless helpers in order and validate their reports and classifications."""

    def __init__(self, entries: Sequence[HelperEntry], clock: Clock) -> None:
        if not entries:
            message = "roles.helper.ladder must contain at least one rung"
            raise ConfigError(message)
        self._entries = tuple(self._headless_entry(entry) for entry in entries)
        self._clock = clock

    async def rebuild_report(
        self,
        kind: ReportKind,
        last_message: str,
        facts: str,
        *,
        cwd: Path,
        timeout: timedelta,
    ) -> Report | Unrecoverable:
        """Reconstruct a missing report from the turn's final message and facts (04 §6)."""
        schema = {"oneOf": [schema_for(kind), Unrecoverable.model_json_schema()]}
        prompt = _REBUILD_PROMPT.format(
            kind=kind.value,
            last_message=fence(last_message, "text"),
            facts=fence(facts, "text"),
        )
        return await self._ask(prompt, schema, model_for(kind), cwd, timeout)

    async def classify_failure(
        self,
        failure: str,
        *,
        cwd: Path,
        timeout: timedelta,
    ) -> FailureClassification | Unrecoverable:
        """Classify only the plugin's unrecognised failure text (04 §6)."""
        schema = {
            "oneOf": [
                FailureClassification.model_json_schema(),
                Unrecoverable.model_json_schema(),
            ]
        }
        prompt = _CLASSIFY_PROMPT.format(failure=fence(failure, "text"))
        return await self._ask(prompt, schema, FailureClassification, cwd, timeout)

    def _headless_entry(self, entry: HelperEntry) -> _HeadlessEntry:
        headless = entry.agent.headless()
        if headless is None:
            message = f"agent {entry.rung.agent!r} has no headless support required by roles.helper"
            raise ConfigError(message)
        return _HeadlessEntry(entry.rung, headless)

    async def _ask[ResultT: BaseModel](
        self,
        prompt: str,
        schema: Mapping[str, object],
        model: type[ResultT],
        cwd: Path,
        timeout: timedelta,
    ) -> ResultT | Unrecoverable:
        started_at = self._clock.now()
        failures: list[str] = []
        for entry in self._entries:
            label = f"{entry.rung.agent}:{entry.rung.model}/{entry.rung.effort}"
            remaining = timeout - (self._clock.now() - started_at)
            if remaining.total_seconds() <= 0:
                failures.append(f"{label}: Helper timeout exhausted before call")
                break
            try:
                raw = await entry.headless.ask(
                    prompt,
                    schema,
                    entry.rung.model,
                    entry.rung.effort,
                    cwd,
                    remaining,
                )
            except _EXPECTED_CALL_ERRORS as error:
                failure = f"{label}: {type(error).__name__}: {error}"
                failures.append(failure)
                _LOGGER.warning("Helper rung failed: %s", failure, exc_info=True)
                continue
            result = self._validate(raw, model)
            if not isinstance(result, Unrecoverable):
                return result
            failure = f"{label}: {result.unrecoverable}"
            failures.append(failure)
            _LOGGER.warning("Helper rung returned unusable output: %s", failure)
        return Unrecoverable(unrecoverable=f"Helper ladder exhausted: {'; '.join(failures)}")

    @staticmethod
    def _validate[ResultT: BaseModel](
        raw: Mapping[str, object], model: type[ResultT]
    ) -> ResultT | Unrecoverable:
        try:
            return model.model_validate(raw)
        except ValidationError as model_error:
            try:
                return Unrecoverable.model_validate(raw)
            except ValidationError as unrecoverable_error:
                reason = (
                    f"invalid helper output for {model.__name__}: {raw!r}; "
                    f"{model.__name__} errors: {model_error}; "
                    f"unrecoverable errors: {unrecoverable_error}"
                )
                return Unrecoverable(unrecoverable=reason)
