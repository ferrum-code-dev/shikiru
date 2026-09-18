"""Calls a rung's `HeadlessAgent` to rebuild a report or classify a stalled screen (04 §6)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ValidationError

from shikiru.domain.fencing import fence
from shikiru.domain.recovery import ScreenClassification
from shikiru.domain.reports import ReportKind, model_for, schema_for
from shikiru.errors import ConfigError
from shikiru.helper.models import Unrecoverable

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
    "## Last message or screen\n\n{last_message}\n\n"
    "## Facts\n\n{facts}\n"
)

_CLASSIFY_PROMPT = (
    "An agent session has stalled. Classify its visible screen as one of `working` (it is "
    "still doing something), `dialog` (a prompt only these keys can answer: {safe_keys}), "
    "`question` (it is asking the operator something), `quota` (a usage limit) or `error` "
    "(anything else blocking progress). For `dialog`, `keys` must be a non-empty subset of "
    "the safe keys above. Include `text` for `question` or `error`, and `resets_at` for "
    "`quota` when known. If you cannot classify it with confidence, respond instead with "
    '{{"unrecoverable": "<reason>"}}.\n\n'
    "## Screen\n\n{screen}\n"
)


class Helper:
    """Calls the helper rung's `HeadlessAgent`, validating every answer with pydantic (04 §6).

    The caller resolves the helper rung (`domain.ladder.pick_rung` with `Role.HELPER`) into
    `model`/`effort`, and the call `timeout`; `Helper` itself only talks to the agent it is
    given.
    """

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
        prompt = _REBUILD_PROMPT.format(
            kind=kind.value,
            last_message=fence(last_message, "text"),
            facts=fence(facts, "text"),
        )
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
        """Classify a stalled `screen`, telling the agent which keys count as `safe` (04 §6).

        Whether a `dialog`'s `keys` are safe to send is a recovery decision, not the
        Helper's: `domain.recovery.decide_stall` is the one place that checks them against
        `safe_keys`.
        """
        classification_schema = ScreenClassification.model_json_schema()
        schema = {"oneOf": [classification_schema, Unrecoverable.model_json_schema()]}
        prompt = _CLASSIFY_PROMPT.format(
            screen=fence(screen, "text"), safe_keys=", ".join(safe_keys)
        )
        raw = await self._headless.ask(prompt, schema, model, effort, cwd, timeout)
        return self._validate(raw, ScreenClassification)

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
