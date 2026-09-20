"""Claude Code plugin options and protocol constants."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AUTH_SUBSCRIPTION = "subscription"
AUTH_API_KEY = "api_key"
DEFAULT_API_KEY_ENV = "ANTHROPIC_API_KEY"
ANTHROPIC_API_KEY_ENV = "ANTHROPIC_API_KEY"
ANTHROPIC_AUTH_TOKEN_ENV = "ANTHROPIC_" + "AUTH_TOKEN"
CLAUDE_COMMAND = "claude"
HOOK_COMMAND = "shikiru hook"
SESSION_ID_FLAG = "--session-id"
RESUME_FLAG = "--resume"
MODEL_FLAG = "--model"
EFFORT_FLAG = "--effort"
SETTINGS_FLAG = "--settings"
ADD_DIR_FLAG = "--add-dir"
PERMISSION_MODE_FLAG = "--permission-mode"
PERMISSION_MODE_AUTO = "auto"


class ClaudeOptions(BaseModel):
    """Options accepted by the Claude Code agent factory."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    auth: Literal["subscription", "api_key"] = AUTH_SUBSCRIPTION
    api_key_env: str = DEFAULT_API_KEY_ENV
    args: list[str] = Field(default_factory=lambda: [PERMISSION_MODE_FLAG, PERMISSION_MODE_AUTO])
