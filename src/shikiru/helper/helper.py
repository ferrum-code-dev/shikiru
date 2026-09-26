"""Calls the helper rung's headless agent and validates every answer (04 §6)."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from shikiru.agents.contracts import FailureKind
from shikiru.domain.fencing import fence
from shikiru.domain.reports import ReportKind, model_for, schema_for
from shikiru.errors import ConfigError
from shikiru.helper.models import Unrecoverable

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import timedelta
    from pathlib import Path

    from shikiru.agents.contracts import Agent
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


class FailureClassification(BaseModel):
    """The Helper's structured classification of an unrecognised agent failure."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: FailureKind
    confidence: float = Field(ge=0, le=1)
    resets_at: datetime | None = None


class Helper:
    """Call the one configured headless helper and validate its reports and classifications."""

    def __init__(self, agent: Agent) -> None:
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
        """Reconstruct a missing report from the turn's final message and facts (04 §6)."""
        schema = {"oneOf": [schema_for(kind), Unrecoverable.model_json_schema()]}
        prompt = _REBUILD_PROMPT.format(
            kind=kind.value,
            last_message=fence(last_message, "text"),
            facts=fence(facts, "text"),
        )
        raw = await self._headless.ask(prompt, schema, model, effort, cwd, timeout)
        return self._validate(raw, model_for(kind))

    async def classify_failure(
        self,
        failure: str,
        *,
        model: str,
        effort: str | None,
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
        raw = await self._headless.ask(prompt, schema, model, effort, cwd, timeout)
        return self._validate(raw, FailureClassification)

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
