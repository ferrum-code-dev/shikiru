"""Claude Code plugin options and protocol constants."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from shikiru.agents.contracts import PERCENT, BillingMode
from shikiru.plugins.agents.shared_settings import (
    billing_declaration_setting,
    cli_args_setting,
    compact_threshold_setting,
)

if TYPE_CHECKING:
    from shikiru.agents.settings import AgentSetting

CLAUDE_COMMAND = "claude"
PRINT_FLAG = "-p"
INPUT_FORMAT_FLAG = "--input-format"
OUTPUT_FORMAT_FLAG = "--output-format"
STREAM_JSON_FORMAT = "stream-json"
VERBOSE_FLAG = "--verbose"
NO_SESSION_PERSISTENCE_FLAG = "--no-session-persistence"
SAFE_MODE_FLAG = "--safe-mode"
STRICT_MCP_CONFIG_FLAG = "--strict-mcp-config"
MCP_CONFIG_FLAG = "--mcp-config"
EMPTY_MCP_CONFIG = '{"mcpServers":{}}'
TOOLS_FLAG = "--tools"
EMPTY_TOOLS = ""
MODEL_CATALOG_REQUEST_ID = "shikiru-model-catalog"
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
ADD_DIR_FLAG = "--add-dir"
SETTINGS_FLAG = "--settings"


class ClaudeOptions(BaseModel):
    """Options accepted by the Claude Code agent factory."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    args: list[str] = Field(default_factory=lambda: list(DEFAULT_ARGS))
    compact_at_pct: float = Field(default=DEFAULT_COMPACT_AT_PCT, ge=0, le=PERCENT)
    billing_mode: BillingMode | None = None


CLAUDE_SETTINGS: tuple[AgentSetting, ...] = (
    compact_threshold_setting(DEFAULT_COMPACT_AT_PCT),
    billing_declaration_setting(),
    cli_args_setting(DEFAULT_ARGS),
)
