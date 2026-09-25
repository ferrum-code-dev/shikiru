"""Codex schema-constrained one-shot calls."""

from __future__ import annotations

import json
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from shikiru.errors import HeadlessOutputError
from shikiru.plugins.agents.codex.options import (
    CODEX_COMMAND,
    CONFIG_FLAG,
    EPHEMERAL_FLAG,
    EXEC_SUBCOMMAND,
    JSON_FLAG,
    MODEL_FLAG,
    OUTPUT_SCHEMA_FLAG,
    READ_ONLY_SANDBOX,
    REASONING_EFFORT_CONFIG,
    SANDBOX_FLAG,
    SKIP_GIT_REPO_CHECK_FLAG,
)

if TYPE_CHECKING:
    from datetime import timedelta

    from shikiru.process import SubprocessRunner

_AGENT_MESSAGE_TYPE = "agent_message"
_ITEM_COMPLETED_TYPE = "item.completed"
_TYPE_FIELD = "type"
_ITEM_FIELD = "item"
_TEXT_FIELD = "text"
_CONTENT_FIELD = "content"
_SCHEMA_TYPE_FIELD = "type"
_OBJECT_TYPE = "object"
_ADDITIONAL_PROPERTIES_FIELD = "additionalProperties"
_STRICT_SCHEMA_VALUE = False
_INVALID_JSON_MESSAGE = "Codex headless output was not valid JSON"
_INVALID_OBJECT_MESSAGE = "Codex headless output was not an object"
_MISSING_AGENT_MESSAGE = "Codex headless output had no agent_message"
_STDIN_LINE_ENDING = "\n"


class CodexHeadless:
    """Runs Codex's ephemeral JSON structured-output mode."""

    def __init__(self, runner: SubprocessRunner, environment: Mapping[str, str]) -> None:
        """Use `runner` and the plugin's resolved authentication environment."""
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
        """Return Codex's schema-constrained final `agent_message` value."""
        with tempfile.TemporaryDirectory(prefix="shikiru-codex-") as directory:
            schema_path = Path(directory) / "output-schema.json"
            schema_path.write_text(
                json.dumps(_strict_schema(schema), separators=(",", ":")), encoding="utf-8"
            )
            argv = [
                CODEX_COMMAND,
                EXEC_SUBCOMMAND,
                EPHEMERAL_FLAG,
                JSON_FLAG,
                OUTPUT_SCHEMA_FLAG,
                str(schema_path),
                MODEL_FLAG,
                model,
            ]
            if effort is not None:
                argv.extend([CONFIG_FLAG, f'{REASONING_EFFORT_CONFIG}="{effort}"'])
            argv.extend(
                [
                    SANDBOX_FLAG,
                    READ_ONLY_SANDBOX,
                    SKIP_GIT_REPO_CHECK_FLAG,
                ]
            )
            result = await self._runner.run(
                argv,
                timeout=timeout.total_seconds(),
                cwd=cwd,
                env=self._environment,
                input_text=prompt
                if prompt.endswith(_STDIN_LINE_ENDING)
                else prompt + _STDIN_LINE_ENDING,
                check=True,
            )
        return _parse_agent_message(result.stdout)


def _strict_schema(value: object) -> object:
    if isinstance(value, Mapping):
        typed_value = cast("Mapping[str, object]", value)
        schema = {key: _strict_schema(item) for key, item in typed_value.items()}
        if schema.get(_SCHEMA_TYPE_FIELD) == _OBJECT_TYPE:
            schema.setdefault(_ADDITIONAL_PROPERTIES_FIELD, _STRICT_SCHEMA_VALUE)
        return schema
    if isinstance(value, list):
        return [_strict_schema(item) for item in cast("list[object]", value)]
    return value


def _parse_agent_message(stdout: str) -> Mapping[str, Any]:
    final_message: str | None = None
    for line in stdout.splitlines():
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, Mapping):
            continue
        payload = cast("Mapping[str, Any]", raw)
        message = _message_from_payload(payload)
        if message is None:
            continue
        final_message = message
    if final_message is None:
        raise HeadlessOutputError(_MISSING_AGENT_MESSAGE)
    try:
        decoded = json.loads(final_message)
    except json.JSONDecodeError as error:
        raise HeadlessOutputError(_INVALID_JSON_MESSAGE) from error
    if not isinstance(decoded, Mapping):
        raise HeadlessOutputError(_INVALID_OBJECT_MESSAGE)
    return cast("Mapping[str, Any]", decoded)


def _message_from_payload(payload: Mapping[str, Any]) -> str | None:
    if payload.get(_TYPE_FIELD) != _ITEM_COMPLETED_TYPE:
        return None
    item = payload.get(_ITEM_FIELD)
    if isinstance(item, Mapping):
        typed_item = cast("Mapping[str, Any]", item)
        if typed_item.get(_TYPE_FIELD) == _AGENT_MESSAGE_TYPE:
            return _text(typed_item)
    return None


def _text(payload: Mapping[str, Any]) -> str | None:
    value = payload.get(_TEXT_FIELD)
    if isinstance(value, str):
        return value
    content = payload.get(_CONTENT_FIELD)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in cast("list[object]", content):
            if isinstance(item, Mapping):
                part = cast("Mapping[str, Any]", item).get(_TEXT_FIELD)
                if isinstance(part, str):
                    parts.append(part)
        text = "".join(parts)
        return text or None
    return None
