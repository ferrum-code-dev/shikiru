"""Option definitions and validation shared by the built-in agent plugins."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.agents.contracts import BILLING_MODE_API, BILLING_MODE_SUBSCRIPTION, PERCENT
from shikiru.agents.settings import AgentOptionsError, AgentSetting, SettingKind

if TYPE_CHECKING:
    from pydantic import ValidationError

COMPACT_AT_PCT_KEY = "compact_at_pct"
BILLING_MODE_KEY = "billing_mode"
ARGS_KEY = "args"


def compact_threshold_setting(default: float) -> AgentSetting:
    """Return the context-compaction threshold setting with the plugin's `default`."""
    return AgentSetting(
        key=COMPACT_AT_PCT_KEY,
        label="Compact at",
        help="Context use at which the agent is asked to compact its session; 0 compacts at once.",
        kind=SettingKind.NUMBER,
        default=default,
        unit="percent",
        minimum=0,
        maximum=PERCENT,
    )


def billing_declaration_setting() -> AgentSetting:
    """Return the billing declaration setting; unset declares nothing."""
    return AgentSetting(
        key=BILLING_MODE_KEY,
        label="Billing mode",
        help=(
            "How this agent is billed, used to label cost as billed or API-equivalent; "
            "leaving it blank declares nothing."
        ),
        kind=SettingKind.CHOICE,
        default=None,
        choices=(BILLING_MODE_API, BILLING_MODE_SUBSCRIPTION),
    )


def cli_args_setting(default: tuple[str, ...]) -> AgentSetting:
    """Return the extra CLI arguments setting with the plugin's `default`."""
    return AgentSetting(
        key=ARGS_KEY,
        label="CLI arguments",
        help="Arguments added to every agent CLI call, split like a shell command line.",
        kind=SettingKind.ARGS,
        default=default,
    )


def options_error(agent: str, error: ValidationError) -> AgentOptionsError:
    """Return the options rejection for `agent`, naming the first option pydantic refused."""
    first = error.errors()[0]
    key = str(first["loc"][0]) if first["loc"] else None
    return AgentOptionsError(f"invalid options for the {agent} agent: {error}", key)
