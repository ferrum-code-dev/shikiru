"""Codex plugin options and protocol constants."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from shikiru.agents.contracts import PERCENT

CODEX_HOME_ENV_VAR = "CODEX_HOME"
CODEX_COMMAND = "codex"
RESUME_SUBCOMMAND = "resume"
NO_DAEMON_FLAG = "--no-daemon"
MODEL_FLAG = "-m"
CONFIG_FLAG = "-c"
REASONING_EFFORT_CONFIG = "model_reasoning_effort"
EXEC_SUBCOMMAND = "exec"
EPHEMERAL_FLAG = "--ephemeral"
JSON_FLAG = "--json"
OUTPUT_SCHEMA_FLAG = "--output-schema"
SANDBOX_FLAG = "-s"
SANDBOX_MODE_CONFIG = "sandbox_mode"
WRITABLE_ROOTS_CONFIG = "sandbox_workspace_write.writable_roots"
READ_ONLY_SANDBOX = "read-only"
WORKSPACE_WRITE_SANDBOX = "workspace-write"
SKIP_GIT_REPO_CHECK_FLAG = "--skip-git-repo-check"
AUTO_COMPACT_LIMIT_CONFIG = "model_auto_compact_token_limit"
MODELS_CACHE_FILENAME = "models_cache.json"
VERSION_ARGV = (CODEX_COMMAND, "--version")
LOGIN_STATUS_ARGV = (CODEX_COMMAND, "login", "status")
DEFAULT_COMPACT_AT_PCT = 80.0
USAGE_EVENT_TYPE = "token_count"
GIT_DIRS_TIMEOUT_SECONDS = 10.0


class CodexOptions(BaseModel):
    """Options accepted by the Codex agent factory."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    args: list[str] = Field(default_factory=list)
    compact_at_pct: float = Field(default=DEFAULT_COMPACT_AT_PCT, ge=0, le=PERCENT)


TESTED_CODEX_VERSION = "0.159.2"
SANDBOX_PROBE_TIMEOUT_SECONDS = 60.0
SANDBOX_PROBE_PARENT = (".cache", "shikiru")
SANDBOX_PROBE_PREFIX = "codex-sandbox-"
GIT_COMMAND = "git"
END_OF_OPTIONS_FLAG = "--"
GIT_DIRS_ARGV = (
    GIT_COMMAND,
    "rev-parse",
    "--path-format=absolute",
    "--absolute-git-dir",
    "--git-common-dir",
)
