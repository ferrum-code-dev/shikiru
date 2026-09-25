"""Claude Code plugin options and protocol constants."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AUTH_SUBSCRIPTION = "subscription"
AUTH_API_KEY = "api_key"
ANTHROPIC_API_KEY_ENV = "ANTHROPIC_API_KEY"
ANTHROPIC_AUTH_TOKEN_ENV = "ANTHROPIC_" + "AUTH_TOKEN"
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
SETTINGS_FLAG = "--settings"
ADD_DIR_FLAG = "--add-dir"


class ClaudeOptions(BaseModel):
    """Options accepted by the Claude Code agent factory."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    auth: Literal["subscription", "api_key"] = AUTH_SUBSCRIPTION
    api_key_env: str = ANTHROPIC_API_KEY_ENV
    args: list[str] = Field(default_factory=lambda: list(DEFAULT_ARGS))
    compact_at_pct: float = Field(default=DEFAULT_COMPACT_AT_PCT, ge=0, le=100)
