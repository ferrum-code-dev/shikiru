"""Codex plugin options and protocol constants."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AUTH_SUBSCRIPTION = "subscription"
AUTH_API_KEY = "api_key"
DEFAULT_API_KEY_ENV = "OPENAI_API_KEY"
CODEX_API_KEY_ENV = "CODEX_API_KEY"
CODEX_HOME_ENV_VAR = "CODEX_HOME"
HOME_ENV_VAR = "HOME"
SHELL_ENV_VAR = "SHELL"
PATH_ENV_VAR = "PATH"
OPENAI_API_KEY_ENV = "OPENAI_API_KEY"
CODEX_COMMAND = "codex"
HOOK_COMMAND = "shikiru hook"
RESUME_SUBCOMMAND = "resume"
MODEL_FLAG = "-m"
CONFIG_FLAG = "-c"
REASONING_EFFORT_CONFIG = "model_reasoning_effort"
CHECK_FOR_UPDATE_CONFIG = "check_for_update_on_startup=false"
BYPASS_HOOK_TRUST_FLAG = "--dangerously-bypass-hook-trust"
CD_FLAG = "-C"
ADD_DIR_FLAG = "--add-dir"
EXEC_SUBCOMMAND = "exec"
EPHEMERAL_FLAG = "--ephemeral"
JSON_FLAG = "--json"
OUTPUT_SCHEMA_FLAG = "--output-schema"
SANDBOX_FLAG = "-s"
READ_ONLY_SANDBOX = "read-only"
SKIP_GIT_REPO_CHECK_FLAG = "--skip-git-repo-check"
VERSION_ARGV = (CODEX_COMMAND, "--version")
LOGIN_STATUS_ARGV = (CODEX_COMMAND, "login", "status")
HOOK_EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "Stop",
    "PermissionRequest",
    "PostCompact",
    "SessionEnd",
)
DEFAULT_ARGS = ("--approve-for-me",)
DEFAULT_COMPACT_BELOW_FREE_PCT = 60
DOCTOR_TIMEOUT_SECONDS = 60.0
GIT_COMMON_DIR_TIMEOUT_SECONDS = 10.0


class CodexOptions(BaseModel):
    """Options accepted by the Codex agent factory."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    auth: Literal["subscription", "api_key"] = AUTH_SUBSCRIPTION
    api_key_env: str = DEFAULT_API_KEY_ENV
    args: list[str] = Field(default_factory=lambda: list(DEFAULT_ARGS))
    compact_below_free_pct: int = Field(default=DEFAULT_COMPACT_BELOW_FREE_PCT, ge=0, le=100)
