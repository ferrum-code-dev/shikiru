"""The orchestration inputs of the installation wizard: units, guidance and accepted values."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from shikiru.config.settings import MAX_USAGE_PCT
from shikiru.errors import ConfigError

if TYPE_CHECKING:
    from collections.abc import Callable

    from shikiru.config.settings import OrchestrationConfig

MINIMUM_PARALLEL = 1
MINIMUM_ITERATIONS = 1
MINIMUM_QUOTA_WAIT = 0
MINIMUM_STALL_TIMEOUT = 1
MINIMUM_TOOL_TIMEOUT = 1
MINIMUM_CPU_PCT = 0
MINIMUM_FREE_RAM_GB = 0


class OrchestrationFieldError(ConfigError):
    """A rejected orchestration value; `input_id` names the wizard input to correct."""

    def __init__(self, input_id: str, message: str) -> None:
        """Explain why the value typed into `input_id` cannot be written."""
        super().__init__(message)
        self.input_id = input_id


@dataclass(frozen=True, slots=True)
class OrchestrationField:
    """One orchestration input: the setting it edits, its unit, guidance and accepted values."""

    input_id: str
    label: str
    summary: str
    unit: str
    explanation: str
    default: Callable[[OrchestrationConfig], int | float]
    minimum: float
    maximum: float | None
    whole_number: bool

    def parse(self, raw: str) -> int | float:
        """Return the number typed into the input, or raise `OrchestrationFieldError` for it."""
        value = _whole(raw) if self.whole_number else _decimal(raw)
        if value is None or value < self.minimum or self._above_maximum(value):
            raise OrchestrationFieldError(self.input_id, self._rule())
        return value

    def _above_maximum(self, value: float) -> bool:
        return self.maximum is not None and value > self.maximum

    def _rule(self) -> str:
        kind = "a whole number" if self.whole_number else "a number"
        if self.maximum is None:
            bounds = f"at least {self.minimum:g}"
        else:
            bounds = f"from {self.minimum:g} to {self.maximum:g}"
        return f"{self.label} must be {kind} {bounds}."


def _whole(raw: str) -> int | None:
    text = raw.strip()
    if not text.isdecimal():
        return None
    return int(text)


def _decimal(raw: str) -> float | None:
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


MAX_PARALLEL = OrchestrationField(
    input_id="max-parallel",
    label="Maximum parallel tasks",
    summary="Max parallel",
    unit="tasks",
    explanation="Task slots the engine fills at once; each running task uses one.",
    default=lambda config: config.max_parallel,
    minimum=MINIMUM_PARALLEL,
    maximum=None,
    whole_number=True,
)
MAX_ITERATIONS = OrchestrationField(
    input_id="max-iterations",
    label="Maximum iterations",
    summary="Max iterations",
    unit="passes",
    explanation=(
        "Implement and verify passes per task, the first included; "
        "the pass that reaches this count is the last."
    ),
    default=lambda config: config.max_iterations,
    minimum=MINIMUM_ITERATIONS,
    maximum=None,
    whole_number=True,
)
MAX_QUOTA_WAIT = OrchestrationField(
    input_id="max-quota-wait",
    label="Maximum quota wait",
    summary="Max quota wait",
    unit="seconds",
    explanation=(
        "The longest wait for an agent's usage quota to reset before the task moves to "
        "another rung or escalates; 0 moves on at once."
    ),
    default=lambda config: config.timeouts.max_quota_wait,
    minimum=MINIMUM_QUOTA_WAIT,
    maximum=None,
    whole_number=True,
)
STALL_TIMEOUT = OrchestrationField(
    input_id="stall-timeout",
    label="Stall timeout",
    summary="Stall timeout",
    unit="seconds",
    explanation=(
        "How long an agent may stay silent while no tool is running before its turn "
        "is stopped as stalled."
    ),
    default=lambda config: config.timeouts.stall,
    minimum=MINIMUM_STALL_TIMEOUT,
    maximum=None,
    whole_number=True,
)
TOOL_TIMEOUT = OrchestrationField(
    input_id="tool-timeout",
    label="Tool timeout",
    summary="Tool timeout",
    unit="seconds",
    explanation=(
        "How long an agent may stay silent while a tool is running before its turn "
        "is stopped as stalled."
    ),
    default=lambda config: config.timeouts.tool,
    minimum=MINIMUM_TOOL_TIMEOUT,
    maximum=None,
    whole_number=True,
)
MAX_CPU = OrchestrationField(
    input_id="max-cpu",
    label="Maximum CPU use",
    summary="Max CPU",
    unit="percent",
    explanation=(
        "New tasks start only while host CPU use is below this; 0 holds every new start, "
        "100 holds only a fully busy host."
    ),
    default=lambda config: config.resources.max_cpu_pct,
    minimum=MINIMUM_CPU_PCT,
    maximum=MAX_USAGE_PCT,
    whole_number=True,
)
MIN_FREE_RAM = OrchestrationField(
    input_id="min-free-ram",
    label="Minimum free RAM",
    summary="Min free RAM",
    unit="GB",
    explanation=(
        "New tasks start only while at least this much RAM is free; 0 turns the RAM check off."
    ),
    default=lambda config: config.resources.min_free_ram_gb,
    minimum=MINIMUM_FREE_RAM_GB,
    maximum=None,
    whole_number=False,
)
ORCHESTRATION_FIELDS = (
    MAX_PARALLEL,
    MAX_ITERATIONS,
    MAX_QUOTA_WAIT,
    STALL_TIMEOUT,
    TOOL_TIMEOUT,
    MAX_CPU,
    MIN_FREE_RAM,
)
