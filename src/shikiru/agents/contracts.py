"""The agent plugin contract (docs/design/04-agents.md §1).

Agents are code plugins. The engine depends only on this module, and each plugin owns
everything agent-specific: CLI argv and auth, hooks and transcripts, prices and context
windows, quota sources, error mapping and compaction policy. The contract doesn't require
hooks: a plugin must deliver `AgentSignal`s by whatever means its agent allows, and prove it
with the contract test kit (`shikiru.agents.testing`, 04 §3).
"""

from __future__ import annotations

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


@dataclass(frozen=True)
class Rung:
    """One `{agent, model, effort}` entry of a role's ladder (04 §1)."""

    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    agent: str
    model: str
    effort: str | None = None


@dataclass(frozen=True)
class SessionKeys:
    """The key sequences an `InteractiveAgent` sends to control its pane (04 §1)."""

    interrupt: tuple[str, ...]
    exit_input: str
    safe: tuple[str, ...]


@dataclass(frozen=True)
class SessionStarted:
    """A session was launched or attached. `session_id` is `None` when it can't be resumed."""

    session_id: str | None


@dataclass(frozen=True)
class PromptSubmitted:
    """A prompt was accepted by the agent, whether injected or typed by the operator."""

    text: str


@dataclass(frozen=True)
class Activity:
    """The agent is working; `tool_running` is true while a tool call is in flight."""

    tool_running: bool


@dataclass(frozen=True)
class TurnEnded:
    """A turn finished; `last_message` is the agent's final message for it."""

    last_message: str


@dataclass(frozen=True)
class TurnFailed:
    """A turn failed. `resets_at` is set only when `kind` is `QUOTA` and it is known."""

    kind: FailureKind
    message: str
    resets_at: datetime | None


@dataclass(frozen=True)
class Compacted:
    """The agent compacted its context."""


@dataclass(frozen=True)
class AttentionNeeded:
    """The agent is waiting on something the operator or Helper must resolve."""

    message: str


@dataclass(frozen=True)
class SessionEnded:
    """The session's process exited."""


type AgentSignal = (
    SessionStarted
    | PromptSubmitted
    | Activity
    | TurnEnded
    | TurnFailed
    | Compacted
    | AttentionNeeded
    | SessionEnded
)


@dataclass(frozen=True)
class Usage:
    """A session's cumulative token and cost usage, as `AgentSession.usage()` reports it (04 §1)."""

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: float | None
    cost_basis: CostBasis
    context_used_pct: float | None


@dataclass(frozen=True)
class QuotaWindow:
    """One quota window an agent plugin's `quota()` reports (04 §1)."""

    name: str
    used_pct: float
    resets_at: datetime | None
    observed_at: datetime


@dataclass(frozen=True)
class CheckResult:
    """One named check from `InteractiveAgent.doctor()`, e.g. binary, version or login."""

    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class LaunchSpec:
    """Everything an `InteractiveAgent` needs to launch or attach a session (04 §1).

    `env` already carries the full launch environment (03 §4.1): `RUN_DIR_ENV_VAR`,
    `ROLE_ENV_VAR`, `PROJECT_ENV_VAR`, `TASK_ENV_VAR` and `SLOT_ENV_VAR`. A plugin's
    `respawn()` call passes it straight through; it is the one source for these variables.
    """

    rung: Rung
    role: str
    cwd: Path
    run_dir: Path
    add_dirs: tuple[Path, ...]
    env: Mapping[str, str]
    resume_session_id: str | None


class PaneHandle(Protocol):
    """The single tmux pane an agent session lives in (04 §1)."""

    async def respawn(self, argv: Sequence[str], env: Mapping[str, str | None], cwd: Path) -> None:
        """Restart the pane's process with `argv`, `env` and `cwd`."""
        ...

    async def capture_visible(self) -> str:
        """Return the pane's currently visible text."""
        ...

    async def is_alive(self) -> bool:
        """Return whether the pane's process is still running."""
        ...


class AgentSession(Protocol):
    """A live interactive agent session. `signals()` must be consumed by exactly one reader."""

    def signals(self) -> AsyncIterator[AgentSignal]:
        """Yield this session's signals, in order, for its single consumer."""
        ...

    async def before_turn(self) -> list[str]:
        """Return lines to inject before the next turn, e.g. a compaction command."""
        ...

    async def usage(self) -> Usage:
        """Return this session's cumulative usage, which must never decrease."""
        ...

    def snapshot(self) -> Mapping[str, Any]:
        """Return opaque state this session's `attach()` call can later resume from."""
        ...


class HeadlessAgent(Protocol):
    """One-shot, schema-constrained, non-interactive calls."""

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


class InteractiveAgent(Protocol):
    """An agent CLI plugin: launches sessions, reports quota and health, resumes and asks."""

    name: str
    keys: SessionKeys
    session_start_requires_prompt: bool

    async def launch(self, spec: LaunchSpec, pane: PaneHandle) -> AgentSession:
        """Start a new session for `spec` in `pane`."""
        ...

    async def attach(
        self,
        spec: LaunchSpec,
        pane: PaneHandle,
        state: Mapping[str, Any],
    ) -> AgentSession:
        """Resume a session already running in `pane`, from a prior `snapshot()`."""
        ...

    async def quota(self) -> list[QuotaWindow]:
        """Return known quota windows, or `[]` when unknown; the plugin owns caching and probing."""
        ...

    async def doctor(self) -> list[CheckResult]:
        """Run this agent's binary, version and login checks."""
        ...

    def resume_command(self, session_id: str, cwd: Path) -> str | None:
        """Return the shell command that resumes `session_id`, or `None` if it can't be resumed."""
        ...

    def headless(self) -> HeadlessAgent | None:
        """Return this agent's `HeadlessAgent`, or `None` when it has no headless support."""
        ...


@dataclass(frozen=True)
class AgentContext:
    """What an `AgentFactory` needs to validate its options and build its `InteractiveAgent`."""

    options: Mapping[str, Any]
    runner: SubprocessRunner
    clock: Clock


type AgentFactory = Callable[[AgentContext], InteractiveAgent]


def parse_rung(text: str) -> Rung:
    """Parse `agent:model` or `agent:model:effort`, raising `ConfigError` on malformed input."""
    parts = text.split(_RUNG_SEPARATOR)
    if len(parts) not in _VALID_RUNG_PART_COUNTS or not all(parts):
        message = f"{text!r} is not a valid rung; expected agent:model or agent:model:effort"
        raise ConfigError(message)
    effort = parts[2] if len(parts) == _VALID_RUNG_PART_COUNTS[1] else None
    return Rung(agent=parts[0], model=parts[1], effort=effort)
