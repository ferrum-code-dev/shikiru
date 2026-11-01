"""The optional agent capability that exposes operator-editable plugin options."""

from __future__ import annotations

import math
import shlex
from dataclasses import dataclass
from enum import StrEnum, auto
from typing import TYPE_CHECKING, Protocol, cast

from shikiru.agents.contracts import BILLING_MODE_API, BILLING_MODE_SUBSCRIPTION, PERCENT
from shikiru.errors import ConfigError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from shikiru.agents.contracts import Agent

COMPACT_AT_PCT_KEY = "compact_at_pct"
BILLING_MODE_KEY = "billing_mode"
ARGS_KEY = "args"


class SettingKind(StrEnum):
    """How an agent option is edited and written to the project config."""

    NUMBER = auto()
    CHOICE = auto()
    ARGS = auto()


class AgentSettingError(ConfigError):
    """A rejected agent setting; `key` names the plugin option and `input_id` its editor."""

    def __init__(self, key: str, message: str, input_id: str = "") -> None:
        """Explain why the text entered for option `key` cannot be written."""
        super().__init__(message)
        self.key = key
        self.input_id = input_id


@dataclass(frozen=True, slots=True)
class AgentSetting:
    """One option a plugin offers the Operator, with its default, guidance and accepted values."""

    key: str
    label: str
    help: str
    kind: SettingKind
    default: float | str | tuple[str, ...] | None
    unit: str = ""
    choices: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None

    @property
    def optional(self) -> bool:
        """Whether the Operator may leave the option unset, so it is not written."""
        return self.kind is SettingKind.CHOICE and self.default is None

    def display(self) -> str:
        """Return the default as the text the Operator edits."""
        if self.default is None:
            return ""
        if isinstance(self.default, tuple):
            return shlex.join(self.default)
        if isinstance(self.default, str):
            return self.default
        return f"{self.default:g}"

    def parse(self, raw: str) -> float | str | list[str] | None:
        """Return the value to write for `raw`, `None` for an unset option, or raise."""
        if self.kind is SettingKind.NUMBER:
            return self._number(raw)
        if self.kind is SettingKind.CHOICE:
            return self._choice(raw)
        return self._args(raw)

    def _number(self, raw: str) -> float:
        try:
            value = float(raw)
        except ValueError:
            value = math.nan
        below = self.minimum is not None and value < self.minimum
        above = self.maximum is not None and value > self.maximum
        if not math.isfinite(value) or below or above:
            raise AgentSettingError(self.key, f"{self.label} must be {self._number_rule()}.")
        return value

    def _number_rule(self) -> str:
        if self.minimum is not None and self.maximum is not None:
            return f"a number from {self.minimum:g} to {self.maximum:g}"
        if self.minimum is not None:
            return f"a number of at least {self.minimum:g}"
        if self.maximum is not None:
            return f"a number of at most {self.maximum:g}"
        return "a number"

    def _choice(self, raw: str) -> str | None:
        text = raw.strip()
        if not text and self.optional:
            return None
        if text not in self.choices:
            options = ", ".join(self.choices)
            message = f"{self.label} must be one of: {options}."
            raise AgentSettingError(self.key, message)
        return text

    def _args(self, raw: str) -> list[str]:
        try:
            return shlex.split(raw)
        except ValueError as error:
            message = f"{self.label} has invalid quoting: {error}."
            raise AgentSettingError(self.key, message) from error


@dataclass(frozen=True, slots=True)
class AgentSettings:
    """What an agent offers for editing, or why it cannot."""

    agent: str
    settings: tuple[AgentSetting, ...] = ()
    supported: bool = True
    error: str | None = None


class SettingsProvider(Protocol):
    """Optional agent capability listing the options worth writing to the project config."""

    def settings(self) -> Sequence[AgentSetting]: ...


def compact_threshold_setting(default: float) -> AgentSetting:
    """Return the shared context-compaction threshold setting with the plugin's `default`."""
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
    """Return the shared billing declaration setting; unset declares nothing."""
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
    """Return the shared extra CLI arguments setting with the plugin's `default`."""
    return AgentSetting(
        key=ARGS_KEY,
        label="CLI arguments",
        help="Arguments added to every agent CLI call, split like a shell command line.",
        kind=SettingKind.ARGS,
        default=default,
    )


def settings_for(agent: Agent) -> tuple[AgentSetting, ...] | None:
    """Return an agent's validated settings, or `None` when it lacks the capability."""
    provider: object = getattr(agent, "settings", None)
    if provider is None:
        return None
    if not callable(provider):
        message = f"agent {agent.name!r} returned an invalid settings capability"
        raise ConfigError(message)
    value = cast("Sequence[object]", provider())
    offered = tuple(item for item in value if isinstance(item, AgentSetting))
    keys = {item.key for item in offered}
    if len(offered) != len(value) or len(keys) != len(offered) or not all(keys):
        message = f"agent {agent.name!r} returned invalid or duplicate settings"
        raise ConfigError(message)
    return offered
