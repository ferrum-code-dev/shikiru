"""Claude Code schema-constrained one-shot calls."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, cast

from shikiru.errors import ConfigError
from shikiru.plugins.agents.claude.options import CLAUDE_COMMAND

if TYPE_CHECKING:
    from datetime import timedelta
    from pathlib import Path

    from shikiru.process import SubprocessRunner

_PRINT_FLAG = "-p"
_OUTPUT_FORMAT_FLAG = "--output-format"
_OUTPUT_FORMAT_JSON = "json"
_JSON_SCHEMA_FLAG = "--json-schema"
_MODEL_FLAG = "--model"
_EFFORT_FLAG = "--effort"
_SAFE_MODE_FLAG = "--safe-mode"
_TOOLS_FLAG = "--tools"
_EMPTY_TOOLS = ""
_NO_SESSION_PERSISTENCE_FLAG = "--no-session-persistence"
_STRUCTURED_OUTPUT_FIELD = "structured_output"
_INVALID_JSON_MESSAGE = "Claude headless output was not valid JSON"
_INVALID_OBJECT_MESSAGE = "Claude headless output was not an object"
_MISSING_STRUCTURED_OUTPUT_MESSAGE = "Claude headless output had no structured_output"


class ClaudeHeadless:
    """Runs Claude Code's JSON structured-output mode."""

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
        """Return Claude's schema-constrained `structured_output` value."""
        argv = [
            CLAUDE_COMMAND,
            _PRINT_FLAG,
            _OUTPUT_FORMAT_FLAG,
            _OUTPUT_FORMAT_JSON,
            _JSON_SCHEMA_FLAG,
            json.dumps(schema, separators=(",", ":")),
            _MODEL_FLAG,
            model,
        ]
        if effort is not None:
            argv.extend([_EFFORT_FLAG, effort])
        argv.extend(
            [
                _SAFE_MODE_FLAG,
                _TOOLS_FLAG,
                _EMPTY_TOOLS,
                _NO_SESSION_PERSISTENCE_FLAG,
            ]
        )
        result = await self._runner.run(
            argv,
            timeout=timeout.total_seconds(),
            cwd=cwd,
            env=self._environment,
            input_text=prompt,
            check=True,
        )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise ConfigError(_INVALID_JSON_MESSAGE) from error
        if not isinstance(payload, Mapping):
            raise ConfigError(_INVALID_OBJECT_MESSAGE)
        typed_payload = cast("Mapping[str, Any]", payload)
        structured = typed_payload.get(_STRUCTURED_OUTPUT_FIELD)
        if not isinstance(structured, Mapping):
            raise ConfigError(_MISSING_STRUCTURED_OUTPUT_MESSAGE)
        return cast("Mapping[str, Any]", structured)
