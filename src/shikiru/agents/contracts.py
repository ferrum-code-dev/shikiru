"""The headless agent plugin contract (docs/design/04-agents.md §1)."""

from __future__ import annotations

import shlex
import sys
from dataclasses import dataclass
from enum import StrEnum, auto
from typing import TYPE_CHECKING, Any, ClassVar, Literal, Protocol

from pydantic import ConfigDict

from shikiru.errors import ConfigError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Mapping, Sequence
    from datetime import datetime, timedelta
    from pathlib import Path

    from shikiru.clock import Clock
    from shikiru.process import SubprocessRunner

_RUNG_SEPARATOR = ":"
_VALID_RUNG_PART_COUNTS = (2, 3)

RUN_DIR_ENV_VAR = "SHIKIRU_RUN_DIR"
ROLE_ENV_VAR = "SHIKIRU_ROLE"
PROJECT_ENV_VAR = "SHIKIRU_PROJECT"
TASK_ENV_VAR = "SHIKIRU_TASK"
SLOT_ENV_VAR = "SHIKIRU_SLOT"
SHIKIRU_MODULE = "shikiru"
_SAFE_PATH_FLAG = "-P"
_MODULE_FLAG = "-m"


def shikiru_command(*arguments: str) -> str:
    """Return a shell command running this Shikiru installation, independent of PATH."""
    return shlex.join([sys.executable, _SAFE_PATH_FLAG, _MODULE_FLAG, SHIKIRU_MODULE, *arguments])


def role_dir(run_dir: Path, role: str) -> Path:
    """Return `role`'s directory under `run_dir`, where its per-role files live (02 §5)."""
    return run_dir / role


CostBasis = Literal["billed", "api_equivalent", "unknown"]


class FailureKind(StrEnum):
    """Why a turn failed, as mapped by the agent plugin from its own error format (04 §1)."""

    QUOTA = auto()
    UNAVAILABLE = auto()
    CONTEXT = auto()
    AUTH = auto()
    OTHER = auto()
    UNKNOWN = auto()


@dataclass(frozen=True)
class Rung:
    """One `{agent, model, effort}` entry of a role's ladder (04 §1)."""

    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    agent: str
    model: str
    effort: str | None = None


@dataclass(frozen=True)
class Usage:
    """A session's cumulative token and priced cost usage, as reported by its agent."""

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: float | None
    cost_basis: CostBasis
    context_used_pct: float | None


@dataclass(frozen=True)
class QuotaWindow:
    """One quota window an agent plugin reports (04 §1)."""

    name: str
    used_pct: float
    resets_at: datetime | None
    observed_at: datetime


@dataclass(frozen=True)
class CheckResult:
    """One named check from `Agent.doctor()`, such as binary, version or login."""

    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class SessionStarted:
    """A new or resumed agent session started."""

    session_id: str


@dataclass(frozen=True)
class Message:
    """Text emitted by the agent while it works."""

    text: str


@dataclass(frozen=True)
class ToolStarted:
    """A named agent tool started with a short description."""

    name: str
    detail: str


@dataclass(frozen=True)
class ToolFinished:
    """A named agent tool finished."""

    name: str


@dataclass(frozen=True)
class UsageReported:
    """The agent's cumulative usage for this session."""

    usage: Usage


@dataclass(frozen=True)
class QuotaReported:
    """The quota windows reported by the agent."""

    windows: tuple[QuotaWindow, ...]


@dataclass(frozen=True)
class Compacted:
    """The agent compacted its context, with token counts when reported."""

    pre_tokens: int | None = None
    post_tokens: int | None = None


@dataclass(frozen=True)
class TurnEnded:
    """A turn finished; `last_message` is the agent's final message for it."""

    last_message: str


@dataclass(frozen=True)
class TurnFailed:
    """A turn failed; `resets_at` is set only for a known quota reset."""

    kind: FailureKind
    message: str
    resets_at: datetime | None


type AgentSignal = (
    SessionStarted
    | Message
    | ToolStarted
    | ToolFinished
    | UsageReported
    | QuotaReported
    | Compacted
    | TurnEnded
    | TurnFailed
)


@dataclass(frozen=True)
class TurnSpec:
    """Everything an agent needs to run one headless turn (04 §1)."""

    rung: Rung
    role: str
    prompt: str
    cwd: Path
    run_dir: Path
    turn_dir: Path
    add_dirs: tuple[Path, ...]
    env: Mapping[str, str]
    session_id: str | None
    usage: Usage | None


class Turn(Protocol):
    """One headless agent turn, with one consumer for its normalized signal stream."""

    @property
    def pid(self) -> int:
        """Return the operating system id for the process running now."""
        ...

    def signals(self) -> AsyncIterator[AgentSignal]:
        """Yield this turn's normalized signals to its single reader."""
        ...

    async def stop(self) -> None:
        """Terminate the process while leaving the session resumable."""
        ...


class HeadlessAgent(Protocol):
    """One-shot, schema-constrained calls without a session."""

    async def ask(
        self,
        prompt: str,
        schema: Mapping[str, Any],
        model: str,
        effort: str | None,
        cwd: Path,
        timeout: timedelta,
    ) -> Mapping[str, Any]:
        """Run one schema-constrained prompt and return its structured output."""
        ...


class Agent(Protocol):
    """A headless agent CLI plugin."""

    name: str

    async def start_turn(self, spec: TurnSpec) -> Turn:
        """Start one new or resumed agent turn."""
        ...

    def interactive_command(self, session_id: str, cwd: Path) -> Sequence[str]:
        """Return the command that resumes `session_id` for an operator take-over."""
        ...

    async def doctor(self) -> list[CheckResult]:
        """Run this agent's binary, version and login checks."""
        ...

    def headless(self) -> HeadlessAgent | None:
        """Return this agent's one-shot, schema-constrained caller when available."""
        ...


@dataclass(frozen=True)
class AgentContext:
    """What an agent factory needs to validate options and build its plugin."""

    options: Mapping[str, Any]
    runner: SubprocessRunner
    clock: Clock


type AgentFactory = Callable[[AgentContext], Agent]


def parse_rung(text: str) -> Rung:
    """Parse `agent:model` or `agent:model:effort`, raising `ConfigError` if malformed."""
    parts = text.split(_RUNG_SEPARATOR)
    if len(parts) not in _VALID_RUNG_PART_COUNTS or not all(parts):
        message = f"{text!r} is not a valid rung; expected agent:model or agent:model:effort"
        raise ConfigError(message)
    effort = parts[2] if len(parts) == _VALID_RUNG_PART_COUNTS[1] else None
    return Rung(agent=parts[0], model=parts[1], effort=effort)
