"""The headless agent plugin contract (docs/design/04-agents.md §1)."""

from __future__ import annotations

import shlex
import sys
from dataclasses import dataclass
from difflib import get_close_matches
from enum import StrEnum, auto
from typing import TYPE_CHECKING, Any, ClassVar, Final, Literal, Protocol, cast

from pydantic import ConfigDict

from shikiru.errors import ConfigError
from shikiru.process import ignore_started

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
DOCTOR_TIMEOUT_SECONDS = 60.0
PERCENT = 100
MODEL_SUGGESTION_CUTOFF = 0.6
COST_BASIS_UNKNOWN = "unknown"
COST_BASIS_BILLED = "billed"
COST_BASIS_API_EQUIVALENT = "api_equivalent"
_SAFE_PATH_FLAG = "-P"
_MODULE_FLAG = "-m"


def module_argv(module: str, *arguments: str) -> tuple[str, ...]:
    """Return the argv running `module` with this Shikiru installation's interpreter."""
    return (sys.executable, _SAFE_PATH_FLAG, _MODULE_FLAG, module, *arguments)


def module_command(module: str, *arguments: str) -> str:
    """Return a shell command running `module` with this installation's interpreter."""
    return shlex.join(module_argv(module, *arguments))


def shikiru_argv(*arguments: str) -> tuple[str, ...]:
    """Return the argv running this Shikiru installation, independent of PATH."""
    return module_argv(SHIKIRU_MODULE, *arguments)


def shikiru_command(*arguments: str) -> str:
    """Return a shell command running this Shikiru installation, independent of PATH."""
    return module_command(SHIKIRU_MODULE, *arguments)


def role_dir(run_dir: Path, role: str) -> Path:
    """Return `role`'s directory under `run_dir`, where its per-role files live (02 §5)."""
    return run_dir / role


CostBasis = Literal["billed", "api_equivalent", "unknown"]
BillingMode = Literal["api", "subscription", "unknown"]
BillingSource = Literal["provider_reported", "operator_config", "unknown"]

BILLING_MODE_API: Final = "api"
BILLING_MODE_SUBSCRIPTION: Final = "subscription"
BILLING_MODE_UNKNOWN: Final = "unknown"
BILLING_SOURCE_PROVIDER_REPORTED: Final = "provider_reported"
BILLING_SOURCE_OPERATOR_CONFIG: Final = "operator_config"
BILLING_SOURCE_UNKNOWN: Final = "unknown"
BILLING_MODES: Final = (BILLING_MODE_API, BILLING_MODE_SUBSCRIPTION, BILLING_MODE_UNKNOWN)
BILLING_SOURCES: Final = (
    BILLING_SOURCE_PROVIDER_REPORTED,
    BILLING_SOURCE_OPERATOR_CONFIG,
    BILLING_SOURCE_UNKNOWN,
)
_MISSING_BILLING_DECLARATION = object()


class FailureKind(StrEnum):
    """Why a turn failed; `AUTH` includes provider access refusals needing operator action."""

    QUOTA = auto()
    UNAVAILABLE = auto()
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
class BillingDeclaration:
    """A plugin's normalized billing mode and the source that established it (04 §1)."""

    mode: BillingMode
    source: BillingSource


UNKNOWN_BILLING_DECLARATION = BillingDeclaration(
    mode=BILLING_MODE_UNKNOWN,
    source=BILLING_SOURCE_UNKNOWN,
)


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
class ModelChoice:
    """One model and its authoritative explicit effort choices (04 §1)."""

    model: str
    efforts: tuple[str, ...] | None
    offered: bool = True


@dataclass(frozen=True)
class ModelCatalog:
    """Authoritative agent model choices, or the reason discovery is unavailable (04 §1)."""

    models: tuple[ModelChoice, ...] = ()
    error: str | None = None
    complete: bool = False


@dataclass(frozen=True)
class ModelChoiceAssessment:
    """One configured choice; `supported` is `None` when catalog evidence is incomplete."""

    supported: bool | None
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


class ToolOutcome(StrEnum):
    """A tool result the agent can reliably classify as successful or failed."""

    SUCCESS = auto()
    FAILURE = auto()


@dataclass(frozen=True)
class ToolFinished:
    """A named agent tool finished."""

    name: str
    outcome: ToolOutcome | None = None
    exit_code: int | None = None


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
    """Everything an agent needs to run one headless turn (04 §1).

    `on_process` receives the id of every process the turn spawns, a context retry included,
    before that process starts running (`SubprocessRunner.spawn(on_started=...)`).
    """

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
    on_process: Callable[[int], None] = ignore_started


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

    def interactive_command(self, session_id: str, run_dir: Path, rung: Rung) -> Sequence[str]:
        """Return the command that resumes `session_id` on `rung` for an operator take-over.

        `rung.agent` owns `session_id`; `run_dir` is the task's run directory, and the command
        runs in the task root.
        """
        ...

    async def doctor(self, models: Sequence[str]) -> list[CheckResult]:
        """Run this agent's binary, version and login checks, plus checks for `models`.

        `models` are the models the configured ladders select for this agent; empty when
        the agent is only being detected.
        """
        ...

    def headless(self) -> HeadlessAgent | None:
        """Return this agent's one-shot, schema-constrained caller when available."""
        ...


class BillingDeclarationProvider(Protocol):
    """Optional agent capability that supplies an explicit billing declaration."""

    @property
    def billing_declaration(self) -> BillingDeclaration: ...


class ModelCatalogProvider(Protocol):
    """Optional agent capability for authoritative model and per-model effort choices."""

    async def model_catalog(self) -> ModelCatalog: ...


class _ModelCatalogMethod(Protocol):
    async def __call__(self) -> object: ...


def billing_declaration_for(agent: Agent) -> BillingDeclaration:
    """Return an agent's valid optional billing declaration, or unknown for old plugins."""
    declaration = getattr(agent, "billing_declaration", _MISSING_BILLING_DECLARATION)
    if declaration is _MISSING_BILLING_DECLARATION:
        return UNKNOWN_BILLING_DECLARATION
    if not isinstance(declaration, BillingDeclaration):
        message = f"agent {agent.name!r} returned an invalid billing declaration"
        raise ConfigError(message)
    if declaration.mode not in BILLING_MODES or declaration.source not in BILLING_SOURCES:
        message = f"agent {agent.name!r} returned an unsupported billing declaration"
        raise ConfigError(message)
    if declaration.mode != BILLING_MODE_UNKNOWN and declaration.source == BILLING_SOURCE_UNKNOWN:
        message = f"agent {agent.name!r} declared a billing mode without its source"
        raise ConfigError(message)
    return declaration


async def model_catalog_for(agent: Agent) -> ModelCatalog | None:
    """Return an optional agent catalog, rejecting malformed plugin capability results."""
    provider: object = getattr(agent, "model_catalog", None)
    if provider is None:
        return None
    if not callable(provider):
        message = f"agent {agent.name!r} returned an invalid model catalog capability"
        raise ConfigError(message)
    catalog_value: object = await cast("_ModelCatalogMethod", provider)()
    if not isinstance(catalog_value, ModelCatalog):
        message = f"agent {agent.name!r} returned an invalid model catalog"
        raise ConfigError(message)
    catalog = catalog_value
    if catalog.error is not None and (
        not catalog.error.strip() or catalog.models or catalog.complete
    ):
        message = f"agent {agent.name!r} returned an inconsistent model catalog"
        raise ConfigError(message)
    model_names: set[str] = set()
    for choice in catalog.models:
        if not choice.model:
            message = f"agent {agent.name!r} returned an invalid model choice"
            raise ConfigError(message)
        if choice.model in model_names:
            message = f"agent {agent.name!r} returned duplicate model choices"
            raise ConfigError(message)
        model_names.add(choice.model)
        if choice.efforts is not None and (
            any(not effort for effort in choice.efforts)
            or len(set(choice.efforts)) != len(choice.efforts)
        ):
            message = f"agent {agent.name!r} returned invalid effort choices"
            raise ConfigError(message)
    return catalog


def assess_model_choice(
    model: str, effort: str | None, catalog: ModelCatalog
) -> ModelChoiceAssessment:
    """Classify a configured choice using only its agent's catalog evidence."""
    if catalog.error is not None:
        supported = None
        detail = f"model catalog unavailable: {catalog.error}"
    else:
        model_choice = next((choice for choice in catalog.models if choice.model == model), None)
        if model_choice is None:
            if not catalog.complete:
                supported = None
                detail = f"model {model!r} could not be verified because the catalog is incomplete"
            else:
                names = tuple(choice.model for choice in catalog.models)
                suggestion = _choice_suggestion(model, names)
                supported = False
                if suggestion is not None:
                    detail = f"unsupported model {model!r}; use {suggestion!r}"
                else:
                    detail = (
                        f"unsupported model {model!r}; available model identifiers: "
                        f"{', '.join(names) or 'none'}"
                    )
        elif effort is None:
            supported = True
            detail = f"model {model_choice.model!r} is listed; native effort default is selected"
        elif model_choice.efforts is None:
            supported = None
            detail = (
                f"effort {effort!r} for model {model_choice.model!r} could not be verified because "
                "the catalog does not report per-model effort choices; leave effort unset"
            )
        elif not model_choice.efforts:
            supported = False
            detail = (
                f"model {model_choice.model!r} has no explicit effort choices; leave effort unset"
            )
        else:
            issue = _unsupported_effort_issue(effort, model_choice.model, model_choice.efforts)
            supported = issue is None
            detail = issue or f"model {model_choice.model!r} supports effort {effort!r}"
    return ModelChoiceAssessment(supported, detail)


def _unsupported_effort_issue(
    effort: str, model: str, supported_efforts: Sequence[str]
) -> str | None:
    if effort in supported_efforts:
        return None
    suggestion = _choice_suggestion(effort, supported_efforts)
    detail = (
        f"unsupported effort {effort!r} for model {model!r}; "
        f"supported efforts: {', '.join(supported_efforts)}"
    )
    if suggestion is not None:
        detail = f"{detail}; use {suggestion!r}"
    return detail


def _choice_suggestion(value: str, choices: Sequence[str]) -> str | None:
    folded = {choice.casefold(): choice for choice in choices}
    exact_casefold = folded.get(value.casefold())
    if exact_casefold is not None:
        return exact_casefold
    matches = get_close_matches(
        value.casefold(),
        [choice.casefold() for choice in choices],
        n=1,
        cutoff=MODEL_SUGGESTION_CUTOFF,
    )
    if not matches:
        return None
    return folded[matches[0]]


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
    agent, model, *effort = parts
    return Rung(agent=agent, model=model, effort=effort[0] if effort else None)
