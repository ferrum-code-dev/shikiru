"""Create configured agent plugins and report their health through one boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from shikiru.agents.contracts import AgentContext, CheckResult
from shikiru.errors import ShikiruError

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import Any

    from shikiru.agents.registry import AgentRegistry
    from shikiru.clock import Clock
    from shikiru.process import SubprocessRunner


@dataclass(frozen=True)
class PluginCheckResult:
    """One plugin's doctor checks, or the expected error that prevented them."""

    name: str
    checks: tuple[CheckResult, ...] = ()
    error: str | None = None

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

    async def check(self, name: str, options: Mapping[str, Any]) -> PluginCheckResult:
        """Create `name` with `options` and run its doctor, reporting expected failures."""
        try:
            agent = self._registry.create(
                name,
                AgentContext(options=options, runner=self._runner, clock=self._clock),
            )
            checks = await agent.doctor()
        except (ShikiruError, OSError, TimeoutError) as error:
            detail = str(error) or type(error).__name__
            return PluginCheckResult(name=name, error=detail)
        return PluginCheckResult(name=name, checks=tuple(checks))
