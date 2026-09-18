"""Calls a rung's `HeadlessAgent` to rebuild a report or classify a stalled screen (04 §6)."""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

from pydantic import BaseModel, ValidationError

from shikiru.domain.reports import ReportKind, model_for, schema_for
from shikiru.errors import ConfigError
from shikiru.helper.models import ScreenClassification, Unrecoverable

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import timedelta
    from pathlib import Path

    from shikiru.agents.contracts import InteractiveAgent
    from shikiru.domain.reports import Report

_REBUILD_PROMPT = (
    "The agent's turn ended without a valid report. Reconstruct the report for this {kind} "
    "turn from its last message (or the visible screen, if the message is empty) and the "
    "facts below. If you cannot determine the report with confidence, respond instead with "
    '{{"unrecoverable": "<reason>"}}.\n\n'
    "## Last message or screen\n\n```text\n{last_message}\n```\n\n"
    "## Facts\n\n```text\n{facts}\n```\n"
)

_CLASSIFY_PROMPT = (
    "An agent session has stalled. Classify its visible screen as one of `working` (it is "
    "still doing something), `dialog` (a prompt only these keys can answer: {safe_keys}), "
    "`question` (it is asking the operator something), `quota` (a usage limit) or `error` "
    "(anything else blocking progress). Include `keys` for `dialog`, `text` for `question` "
    "or `error`, and `resets_at` for `quota` when known. If you cannot classify it with "
    'confidence, respond instead with {{"unrecoverable": "<reason>"}}.\n\n'
    "## Screen\n\n```text\n{screen}\n```\n"
)

_ResultT = TypeVar("_ResultT", bound=BaseModel)


class Helper:
    """Calls the helper rung's `HeadlessAgent`, validating every answer with pydantic (04 §6)."""

    def __init__(self, agent: InteractiveAgent) -> None:
        """Hold `agent`'s `HeadlessAgent`, raising `ConfigError` when it has none (04 §6)."""
        headless = agent.headless()
        if headless is None:
            message = f"agent {agent.name!r} has no headless support required by roles.helper"
            raise ConfigError(message)
        self._headless = headless

    async def rebuild_report(
        self,
        kind: ReportKind,
        last_message: str,
        facts: str,
        *,
        model: str,
        effort: str | None,
        cwd: Path,
        timeout: timedelta,
    ) -> Report | Unrecoverable:
        """Reconstruct the missing report of `kind` from `last_message` and `facts` (04 §6)."""
        schema = {"oneOf": [schema_for(kind), Unrecoverable.model_json_schema()]}
        prompt = _REBUILD_PROMPT.format(kind=kind.value, last_message=last_message, facts=facts)
        raw = await self._headless.ask(prompt, schema, model, effort, cwd, timeout)
        return self._validate(raw, model_for(kind))

    async def classify_screen(
        self,
        screen: str,
        safe_keys: Sequence[str],
        *,
        model: str,
        effort: str | None,
        cwd: Path,
        timeout: timedelta,
    ) -> ScreenClassification | Unrecoverable:
        """Classify a stalled `screen`, telling the agent which keys count as `safe` (04 §6)."""
        classification_schema = ScreenClassification.model_json_schema()
        schema = {"oneOf": [classification_schema, Unrecoverable.model_json_schema()]}
        prompt = _CLASSIFY_PROMPT.format(screen=screen, safe_keys=", ".join(safe_keys))
        raw = await self._headless.ask(prompt, schema, model, effort, cwd, timeout)
        return self._validate(raw, ScreenClassification)

    @staticmethod
    def _validate(raw: Mapping[str, object], model: type[_ResultT]) -> _ResultT | Unrecoverable:
        try:
            return model.model_validate(raw)
        except ValidationError:
            pass
        try:
            return Unrecoverable.model_validate(raw)
        except ValidationError:
            reason = f"invalid helper output for {model.__name__}: {raw!r}"
            return Unrecoverable(unrecoverable=reason)
