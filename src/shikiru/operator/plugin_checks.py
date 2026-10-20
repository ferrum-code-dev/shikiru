"""Create configured agent plugins and report their health through one boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from shikiru.agents.contracts import (
    AgentContext,
    CheckResult,
    ModelCatalog,
    assess_model_choice,
    model_catalog_for,
)
from shikiru.errors import ShikiruError

_MODEL_CATALOG_UNSUPPORTED_DETAIL = "agent does not expose an authoritative model catalog"

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from typing import Any

    from shikiru.agents.contracts import Agent
    from shikiru.agents.registry import AgentRegistry
    from shikiru.clock import Clock
    from shikiru.process import SubprocessRunner


@dataclass(frozen=True)
class PluginCheckResult:
    """One plugin's doctor checks, or the expected error that prevented them."""

    name: str
    checks: tuple[CheckResult, ...] = ()
    error: str | None = None
    catalog: ModelCatalog | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.checks) and all(check.ok for check in self.checks)

    @property
    def detail(self) -> str:
        if self.error is not None:
            return self.error
        if not self.checks:
            return "plugin returned no checks"
        failures = [f"{check.name}: {check.detail}" for check in self.checks if not check.ok]
        return "; ".join(failures) if failures else "all checks passed"


class PluginChecks:
    """Run `Agent.doctor()` through the same registry for init, start and doctor."""

    def __init__(self, registry: AgentRegistry, runner: SubprocessRunner, clock: Clock) -> None:
        """Use the shared registry and runtime boundaries for plugin checks."""
        self._registry = registry
        self._runner = runner
        self._clock = clock

    def names(self) -> tuple[str, ...]:
        """Return names available from the injected agent registry."""
        return self._registry.names()

    async def check(
        self,
        name: str,
        options: Mapping[str, Any],
        models: Sequence[str] = (),
        *,
        include_model_catalog: bool = False,
    ) -> PluginCheckResult:
        """Create `name` with `options` and run its doctor for `models`, reporting failures."""
        try:
            agent = self._registry.create(
                name,
                AgentContext(options=options, runner=self._runner, clock=self._clock),
            )
        except (ShikiruError, OSError, TimeoutError) as error:
            detail = str(error) or type(error).__name__
            return PluginCheckResult(name=name, error=detail)
        catalog = await _agent_model_catalog(agent) if include_model_catalog else None
        if include_model_catalog and catalog is None:
            catalog = ModelCatalog(error=_MODEL_CATALOG_UNSUPPORTED_DETAIL)
        checked_models = tuple(models)
        if catalog is not None and catalog.error is None and catalog.complete:
            checked_models = tuple(
                model
                for model in checked_models
                if assess_model_choice(model, None, catalog).supported is not False
            )
        try:
            checks = await agent.doctor(checked_models)
        except (ShikiruError, OSError, TimeoutError) as error:
            detail = str(error) or type(error).__name__
            return PluginCheckResult(name=name, error=detail, catalog=catalog)
        return PluginCheckResult(name=name, checks=tuple(checks), catalog=catalog)

    async def model_catalog(self, name: str, options: Mapping[str, Any]) -> ModelCatalog | None:
        """Return authoritative choices when the plugin offers them, or an explicit failure."""
        try:
            agent = self._registry.create(
                name,
                AgentContext(options=options, runner=self._runner, clock=self._clock),
            )
            return await _agent_model_catalog(agent)
        except (ShikiruError, OSError, TimeoutError) as error:
            detail = str(error) or type(error).__name__
            return ModelCatalog(error=detail)


async def _agent_model_catalog(agent: Agent) -> ModelCatalog | None:
    try:
        return await model_catalog_for(agent)
    except (ShikiruError, OSError, TimeoutError) as error:
        detail = str(error) or type(error).__name__
        return ModelCatalog(error=detail)
