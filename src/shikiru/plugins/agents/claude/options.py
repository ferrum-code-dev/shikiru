"""Claude Code plugin options and protocol constants."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

CLAUDE_COMMAND = "claude"
DEFAULT_COMPACT_AT_PCT = 80.0
STATUS_PAYMENT_REQUIRED = 402
STATUS_TOO_MANY_REQUESTS = 429
STATUS_UNAUTHORIZED = 401
STATUS_FORBIDDEN = 403
PERMISSION_MODE_FLAG = "--permission-mode"
DEFAULT_ARGS = (PERMISSION_MODE_FLAG, "bypassPermissions")
SESSION_ID_FLAG = "--session-id"
RESUME_FLAG = "--resume"
MODEL_FLAG = "--model"
EFFORT_FLAG = "--effort"
PRINT_FLAG = "-p"
OUTPUT_FORMAT_FLAG = "--output-format"
ADD_DIR_FLAG = "--add-dir"
SETTINGS_FLAG = "--settings"


class ClaudeOptions(BaseModel):
    """Options accepted by the Claude Code agent factory."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    args: list[str] = Field(default_factory=lambda: list(DEFAULT_ARGS))
    compact_at_pct: float = Field(default=DEFAULT_COMPACT_AT_PCT, ge=0, le=100)
