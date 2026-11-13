"""Muse Code schema-constrained one-shot calls."""

from __future__ import annotations

import json
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from shikiru.agents.contracts import RUNTIME_DEFAULT_MODEL
from shikiru.agents.sdk.schema import strict_schema
from shikiru.errors import HeadlessOutputError
from shikiru.plugins.agents.muse.options import (
    EXEC_SUBCOMMAND,
    HEADLESS_FLAGS,
    JSON_FLAG,
    MODEL_FLAG,
    MUSE_COMMAND,
    NO_AUTO_UPDATE_ENV_VAR,
    NO_AUTO_UPDATE_VALUE,
    OUTPUT_SCHEMA_FLAG,
    PROMPT_FILE_FLAG,
    PROMPT_FILENAME,
    REASONING_EFFORT_FLAG,
    RECORD_PAYLOAD_KEY,
    RECORD_PAYLOAD_TYPE_KEY,
    RUN_TERMINAL_COMPLETED_TYPE,
)

if TYPE_CHECKING:
    from datetime import timedelta

    from shikiru.process import SubprocessRunner

_SCHEMA_FILENAME = "output-schema.json"
_TEXT_KEY = "text"
_TEMP_DIRECTORY_PREFIX = "shikiru-muse-"
_MALFORMED_RECORD_MESSAGE = "Muse headless output was not a JSON record per line"
_MISSING_RESULT_MESSAGE = "Muse headless output had no completed result"
_INVALID_JSON_MESSAGE = "Muse headless output was not valid JSON"
_INVALID_OBJECT_MESSAGE = "Muse headless output was not an object"
_TEXT_ENCODING = "utf-8"


class MuseHeadless:
    """Runs Muse Code's `exec --json` with a structured output schema."""

    def __init__(self, runner: SubprocessRunner, environment: Mapping[str, str]) -> None:
        """Use `runner` and the operator's inherited Muse environment."""
        self._runner = runner
        self._environment = dict(environment)

    async def ask(
        self,
        prompt: str,
        schema: Mapping[str, Any],
        model: str,
        effort: str | None,
        cwd: Path,
        timeout: timedelta,
    ) -> Mapping[str, Any]:
        """Return Muse's schema-constrained final result object."""
        with tempfile.TemporaryDirectory(prefix=_TEMP_DIRECTORY_PREFIX) as directory:
            prompt_path = Path(directory) / PROMPT_FILENAME
            schema_path = Path(directory) / _SCHEMA_FILENAME
            prompt_path.write_text(prompt, encoding=_TEXT_ENCODING)
            schema_path.write_text(
                json.dumps(strict_schema(schema), separators=(",", ":")), encoding=_TEXT_ENCODING
            )
            argv = [
                MUSE_COMMAND,
                EXEC_SUBCOMMAND,
                JSON_FLAG,
                PROMPT_FILE_FLAG,
                str(prompt_path),
                OUTPUT_SCHEMA_FLAG,
                str(schema_path),
                *HEADLESS_FLAGS,
            ]
            if model != RUNTIME_DEFAULT_MODEL:
                argv.extend((MODEL_FLAG, model))
            if effort is not None:
                argv.extend((REASONING_EFFORT_FLAG, effort))
            result = await self._runner.run(
                argv,
                timeout=timeout.total_seconds(),
                cwd=cwd,
                env={**self._environment, NO_AUTO_UPDATE_ENV_VAR: NO_AUTO_UPDATE_VALUE},
                check=True,
            )
        return _parse_final_object(result.stdout)


def _parse_final_object(stdout: str) -> Mapping[str, Any]:
    completed = [
        record
        for record in _records(stdout)
        if record.get(RECORD_PAYLOAD_TYPE_KEY) == RUN_TERMINAL_COMPLETED_TYPE
    ]
    if not completed:
        raise HeadlessOutputError(_MISSING_RESULT_MESSAGE)
    try:
        decoded = json.loads(_completed_text(completed[-1]))
    except json.JSONDecodeError as error:
        raise HeadlessOutputError(_INVALID_JSON_MESSAGE) from error
    if not isinstance(decoded, Mapping):
        raise HeadlessOutputError(_INVALID_OBJECT_MESSAGE)
    return cast("Mapping[str, Any]", decoded)


def _completed_text(record: Mapping[str, Any]) -> str:
    payload = record.get(RECORD_PAYLOAD_KEY)
    if isinstance(payload, Mapping):
        text = cast("Mapping[str, Any]", payload).get(_TEXT_KEY)
        if isinstance(text, str):
            return text
    raise HeadlessOutputError(_MISSING_RESULT_MESSAGE)


def _records(stdout: str) -> list[Mapping[str, Any]]:
    records: list[Mapping[str, Any]] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as error:
            raise HeadlessOutputError(_MALFORMED_RECORD_MESSAGE) from error
        if not isinstance(raw, Mapping):
            raise HeadlessOutputError(_MALFORMED_RECORD_MESSAGE)
        records.append(cast("Mapping[str, Any]", raw))
    return records
