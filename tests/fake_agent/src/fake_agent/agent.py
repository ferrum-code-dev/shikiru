"""Per-turn fake CLI plugin used by engine integration tests."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from fake_agent.headless import FakeHeadlessAgent
from fake_agent.turns import FakeHeadlessTurnAgent, FakeTurnSequence
from shikiru.agents.contracts import (
    BILLING_SOURCE_OPERATOR_CONFIG,
    UNKNOWN_BILLING_DECLARATION,
    BillingDeclaration,
    BillingMode,
    CheckResult,
    ModelCatalog,
    ModelChoice,
    QuotaReading,
    QuotaWindow,
)
from shikiru.agents.settings import AgentSetting, SettingKind
from shikiru.errors import ConfigError, ShikiruError
from shikiru.plugins.agents.shared_settings import (
    billing_declaration_setting,
    compact_threshold_setting,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from shikiru.agents.contracts import AgentContext, HeadlessAgent, Rung, Turn, TurnSpec
    from shikiru.process import SubprocessRunner

REPL_COMMAND = "fake-agent-repl"
_DOCTOR_CHECK_NAME = "binary"
_FAKE_MODEL_CATALOG = ModelCatalog(
    models=(
        ModelChoice(model="default", efforts=("low", "high")),
        ModelChoice(model="strong", efforts=("high",)),
        ModelChoice(model="smoke", efforts=("low",)),
        ModelChoice(model="compact-visible", efforts=("high",)),
        ModelChoice(model="claude-fable-5-1[1m]", efforts=("high",)),
    ),
    complete=True,
    supports_runtime_default=True,
)


_FAKE_SETTINGS = (
    compact_threshold_setting(80.0),
    billing_declaration_setting(),
    AgentSetting(
        key="auth",
        label="Authentication",
        help="How the fake CLI authenticates; only no authentication is simulated.",
        kind=SettingKind.CHOICE,
        default="none",
        choices=("none",),
    ),
)


class FakeOptions(BaseModel):
    """`agents.fake.options`: canned Helper answers and per-turn fake CLI behavior."""

    model_config = ConfigDict(extra="forbid")

    asks: list[dict[str, Any]] = Field(default_factory=list)
    compact_at_pct: float = 80.0
    auth: Literal["none"] = "none"
    model_catalog_error: str | None = None
    new_session_id: str | None = None
    billing_mode: BillingMode | None = None
    quota_file: str | None = None


class FakeAgent:
    """Run the test-only fake CLI once per turn."""

    name = "fake"

    def __init__(self, runner: SubprocessRunner, options: FakeOptions) -> None:
        self._headless_turns = FakeHeadlessTurnAgent(
            runner, options.asks, options.compact_at_pct, options.new_session_id
        )
        self._asks = options.asks
        self._quota_file = Path(options.quota_file) if options.quota_file is not None else None
        self._model_catalog_error = options.model_catalog_error
        self._billing_declaration = (
            BillingDeclaration(options.billing_mode, BILLING_SOURCE_OPERATOR_CONFIG)
            if options.billing_mode is not None
            else UNKNOWN_BILLING_DECLARATION
        )

    @property
    def billing_declaration(self) -> BillingDeclaration:
        """Return the configured declaration used by engine boundary integrations."""
        return self._billing_declaration

    async def start_turn(self, spec: TurnSpec) -> Turn:
        """Start a scripted per-turn fake CLI process."""
        return await self._headless_turns.start_turn(spec)

    def process_sequence(self, spec: TurnSpec) -> FakeTurnSequence:
        """Build the fake CLI process sequence for contract scenarios."""
        return self._headless_turns.process_sequence(spec)

    def interactive_command(self, session_id: str, run_dir: Path, rung: Rung) -> Sequence[str]:
        """Return the optional fake REPL command for a future operator take-over."""
        return self._headless_turns.interactive_command(session_id, run_dir, rung)

    async def doctor(self, models: Sequence[str]) -> list[CheckResult]:
        """Report the fake CLI as available."""
        del models
        return [CheckResult(name=_DOCTOR_CHECK_NAME, ok=True, detail=REPL_COMMAND)]

    async def model_catalog(self) -> ModelCatalog:
        """Return the model choices supported by the test-only fake CLI."""
        if self._model_catalog_error is not None:
            return ModelCatalog(error=self._model_catalog_error)
        return _FAKE_MODEL_CATALOG

    def read_quota(self, since: datetime) -> QuotaReading | None:
        """Return the scripted quota file's reading when it was observed after `since`."""
        if self._quota_file is None:
            return None
        try:
            raw = self._quota_file.read_text("utf-8")
            data = json.loads(raw)
            observed_at = datetime.fromisoformat(data["observed_at"])
            windows = tuple(
                QuotaWindow(
                    name=item["name"],
                    used_pct=item["used_pct"],
                    resets_at=(
                        datetime.fromisoformat(item["resets_at"]) if item["resets_at"] else None
                    ),
                    observed_at=observed_at,
                )
                for item in data["windows"]
            )
        except (OSError, ValueError, KeyError) as error:
            message = f"fake quota source unreadable: {error}"
            raise ShikiruError(message) from error
        return (
            QuotaReading(windows=windows, observed_at=observed_at) if observed_at > since else None
        )

    def settings(self) -> tuple[AgentSetting, ...]:
        """Return the options the fake plugin offers to the setup wizard."""
        return _FAKE_SETTINGS

    def headless(self) -> HeadlessAgent | None:
        """Return the fake Helper answer provider."""
        return FakeHeadlessAgent(self._asks)


def create(context: AgentContext) -> FakeAgent:
    """Build the `fake` agent from `context.options`, raising `ConfigError` on bad options."""
    try:
        options = FakeOptions.model_validate(context.options)
    except ValidationError as error:
        message = f"invalid options for the fake agent: {error}"
        raise ConfigError(message) from error
    return FakeAgent(context.runner, options)
