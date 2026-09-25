"""Codex plugin options and protocol constants."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

CODEX_HOME_ENV_VAR = "CODEX_HOME"
CODEX_COMMAND = "codex"
RESUME_SUBCOMMAND = "resume"
MODEL_FLAG = "-m"
CONFIG_FLAG = "-c"
REASONING_EFFORT_CONFIG = "model_reasoning_effort"
UNATTENDED_CONFIGS = (
    "check_for_update_on_startup=false",
    "notice.hide_rate_limit_model_nudge=true",
)
CD_FLAG = "-C"
ADD_DIR_FLAG = "--add-dir"
EXEC_SUBCOMMAND = "exec"
EPHEMERAL_FLAG = "--ephemeral"
JSON_FLAG = "--json"
OUTPUT_SCHEMA_FLAG = "--output-schema"
SANDBOX_FLAG = "-s"
READ_ONLY_SANDBOX = "read-only"
WORKSPACE_WRITE_SANDBOX = "workspace-write"
SKIP_GIT_REPO_CHECK_FLAG = "--skip-git-repo-check"
MODEL_AUTO_COMPACT_TOKEN_LIMIT_CONFIG = "model_auto_compact_" + "token_limit"
MODELS_CACHE_FILENAME = "models_cache.json"
VERSION_ARGV = (CODEX_COMMAND, "--version")
LOGIN_STATUS_ARGV = (CODEX_COMMAND, "login", "status")
APPROVE_FOR_ME_FLAG = "--approve-for-me"
DEFAULT_ARGS = (APPROVE_FOR_ME_FLAG,)
DEFAULT_COMPACT_AT_PCT = 80.0
DOCTOR_TIMEOUT_SECONDS = 60.0
GIT_COMMON_DIR_TIMEOUT_SECONDS = 10.0


class CodexOptions(BaseModel):
    """Options accepted by the Codex agent factory."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    args: list[str] = Field(default_factory=lambda: list(DEFAULT_ARGS))
    compact_at_pct: float = Field(default=DEFAULT_COMPACT_AT_PCT, ge=0, le=100)
