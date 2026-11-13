"""Muse Code plugin options and protocol constants."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from shikiru.agents.contracts import BillingMode
from shikiru.plugins.agents.shared_settings import (
    billing_declaration_setting,
    cli_args_setting,
    compact_threshold_setting,
)

if TYPE_CHECKING:
    from shikiru.agents.settings import AgentSetting

MUSE_COMMAND = "muse"
EXEC_SUBCOMMAND = "exec"
RESUME_SUBCOMMAND = "resume"
USAGE_SUBCOMMAND = "usage"
SERVE_SUBCOMMAND = "serve"
VERSION_FLAG = "--version"
JSON_FLAG = "--json"
VERSION_ARGV = (MUSE_COMMAND, VERSION_FLAG)
USAGE_ARGV = (MUSE_COMMAND, USAGE_SUBCOMMAND, JSON_FLAG)
TESTED_MUSE_VERSION = "1.4.15"
API_KEY_ENV_VAR = "META_API_KEY"
SESSION_ID_FLAG = "--session-id"
PROMPT_FILE_FLAG = "--prompt-file"
OUTPUT_SCHEMA_FLAG = "--output-schema"
MODEL_FLAG = "--model"
REASONING_EFFORT_FLAG = "--reasoning-effort"
SOFT_THRESHOLD_FLAG = "--context-compaction-soft-threshold"
HARD_THRESHOLD_FLAG = "--context-compaction-hard-threshold"
TRUST_WORKSPACE_FLAG = "--trust-workspace"
DISABLE_SANDBOX_FLAG = "--disable-sandbox"
DISABLE_SHELL_FLAG = "--disable-shell"
DISABLE_WRITE_FLAG = "--disable-write"
DISABLE_WEB_TOOLS_FLAG = "--disable-web-tools"
DISABLE_REMINDERS_FLAG = "--disable-reminders"
NO_SESSION_LOG_FLAG = "--no-session-log"
DEFAULT_ARGS = (TRUST_WORKSPACE_FLAG, DISABLE_SANDBOX_FLAG)
HEADLESS_FLAGS = (
    DISABLE_SHELL_FLAG,
    NO_SESSION_LOG_FLAG,
    DISABLE_WEB_TOOLS_FLAG,
    DISABLE_REMINDERS_FLAG,
)
DEFAULT_COMPACT_AT_PCT = 80.0
HARD_THRESHOLD_MARGIN = 0.005
MAX_COMPACT_AT_PCT = 99.0
PROMPT_FILENAME = "prompt.md"
NO_AUTO_UPDATE_ENV_VAR = "MUSE_NO_AUTO_UPDATE"
NO_AUTO_UPDATE_VALUE = "1"
SERVE_ARGV = (
    MUSE_COMMAND,
    SERVE_SUBCOMMAND,
    DISABLE_SHELL_FLAG,
    DISABLE_WRITE_FLAG,
    NO_SESSION_LOG_FLAG,
)
MODEL_CATALOG_TIMEOUT_SECONDS = 10.0
STARTUP_PROMPT_ESTIMATE_TOKENS = 22_500
PROVIDER_FLAG = "--provider"
PRESET_FLAG = "--preset"
EFFORT_ALIAS_FLAG = "--effort"
CONFIG_SHORT_FLAG = "-c"
CONFIG_LONG_FLAG = "--config"
MODEL_CATALOG_SELECTOR_FLAGS = (
    MODEL_FLAG,
    REASONING_EFFORT_FLAG,
    EFFORT_ALIAS_FLAG,
    PROVIDER_FLAG,
    PRESET_FLAG,
)
MODEL_CATALOG_ARGS_DETAIL = (
    f"{MUSE_COMMAND} agent args select a model, effort, provider or preset, or override "
    "configuration; its effective model catalog cannot be determined safely"
)

RECORD_STREAM_KEY = "stream"
RECORD_PAYLOAD_KEY = "payload"
RECORD_PAYLOAD_TYPE_KEY = "payload_type"
STREAM_KIND_KEY = "kind"
STREAM_ID_KEY = "id"
STREAM_KIND_SESSION = "session"
RUN_MODEL_CONFIGURED_TYPE = "run.model.configured"
RUN_OUTPUT_DELTA_TYPE = "run.output.delta"
TASK_PROPOSED_TYPE = "task.lifecycle.proposed"
TASK_SCHEDULED_TYPE = "task.lifecycle.scheduled"
TASK_STARTED_TYPE = "task.lifecycle.started"
TASK_OUTPUT_TYPE = "task.lifecycle.output"
TOOL_TASK_KIND_PREFIX = "tool."
TOOL_IDEMPOTENCY_KEY_PREFIX = "tool:"
TOOL_RESULT_TYPE = "tool.result"
RUN_USAGE_TYPE = "run.usage"
RUN_TERMINAL_COMPLETED_TYPE = "run.terminal.completed"
RUN_TERMINAL_FAILED_TYPE = "run.terminal.failed"

XDG_DATA_HOME_ENV_VAR = "XDG_DATA_HOME"
DEFAULT_DATA_HOME_PARTS = (".local", "share")
MUSE_HOME_DIRECTORY = "muse"
SESSIONS_DIRECTORY = "sessions"
SESSION_DAY_GLOB = "*/*/*"
SESSION_LOG_FILENAME = "session.jsonl"
PERMISSION_TRANSACTION_FRAME = "session_permission_transaction"
FRAME_MARKER_KEY = "retained_frame"
FRAME_CHILDREN_KEY = "children"
FRAME_CHILD_RECORD_KEY = "record_json"
SESSION_PAYLOAD_TYPE = "runtime.session"
PAYLOAD_EVENT_KEY = "event"
EVENT_KIND_KEY = "kind"
COMPACTION_INSTALLED_EVENT = "context_compaction_installed"
MODEL_COMPLETED_EVENT = "model_completed"
BUDGET_BEFORE_KEY = "budget_before"
BUDGET_AFTER_KEY = "budget_after"
ESTIMATED_PROMPT_TOKENS_KEY = "estimated_prompt_tokens"
MODEL_KEY = "model"
USAGE_KEY = "usage"
INPUT_TOKENS_KEY = "input_tokens"
OUTPUT_TOKENS_KEY = "output_tokens"
CACHED_TOKENS_KEY = "cached_tokens"
USAGE_ALLOWANCE_KEY = "allowance"
USAGE_WINDOW_KEY = "window"
USAGE_WEEKLY_KEY = "weekly"
USAGE_USED_PERCENT_KEY = "usedPercent"
USAGE_RESETS_AT_KEY = "resetsAtMs"
USAGE_AS_OF_KEY = "asOfMs"


class MuseOptions(BaseModel):
    """Options accepted by the Muse Code agent factory."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    args: list[str] = Field(default_factory=lambda: list(DEFAULT_ARGS))
    compact_at_pct: float = Field(default=DEFAULT_COMPACT_AT_PCT, ge=0, le=MAX_COMPACT_AT_PCT)
    billing_mode: BillingMode | None = None


MUSE_SETTINGS: tuple[AgentSetting, ...] = (
    compact_threshold_setting(DEFAULT_COMPACT_AT_PCT, maximum=MAX_COMPACT_AT_PCT),
    billing_declaration_setting(),
    cli_args_setting(DEFAULT_ARGS),
)
