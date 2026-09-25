"""Pydantic settings models for every core configuration key (docs/design/05-data.md §1)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shikiru.agents.contracts import Rung

ComplexityName = Literal["low", "normal", "high"]
IntegrationStrategy = Literal["merge"]
LayoutSplitAxis = Literal["h", "v"]
PaneName = Literal["system", "leader", "implementer", "shell"]


class ConfigModel(BaseModel):
    """Base for every settings model: unknown keys are rejected, values are immutable."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ProjectConfig(ConfigModel):
    """`[project]`."""

    name: str
    trackers: list[str]


class RepoConfig(ConfigModel):
    """`[repo]`: the project's single git repository, rooted at the project root (D20)."""

    base: str | None = None
    caches: list[str] = Field(default_factory=list)


class ResourcesConfig(ConfigModel):
    """`[orchestration.resources]`."""

    max_cpu_pct: int
    min_free_ram_gb: float


class TimeoutsConfig(ConfigModel):
    """`[orchestration.timeouts]`, all values in seconds."""

    stall: int
    tool: int
    max_quota_wait: int
    integration_wait: int
    helper: int


class OrchestrationConfig(ConfigModel):
    """`[orchestration]`."""

    max_parallel: int
    max_iterations: int
    integration: IntegrationStrategy
    poll_seconds: int
    resources: ResourcesConfig
    timeouts: TimeoutsConfig


class AgentConfig(ConfigModel):
    """`[agents.<name>]`. `options` is validated by that plugin's factory, not here."""

    options: dict[str, Any] = Field(default_factory=dict)


class ComplexityLadder(ConfigModel):
    """One `[roles.implementer.complexity.<name>]` table."""

    ladder: list[Rung]


class RoleConfig(ConfigModel):
    """A role's base ladder: `[roles.leader]` and `[roles.helper]`."""

    ladder: list[Rung]


class ImplementerConfig(RoleConfig):
    """`[roles.implementer]`: a base ladder plus escalation and per-complexity overrides."""

    escalate_after: int
    complexity: dict[ComplexityName, ComplexityLadder] = Field(default_factory=dict)

    def ladder_for(self, complexity: ComplexityName) -> list[Rung]:
        """Resolve the ladder for `complexity`, falling back to the base ladder."""
        override = self.complexity.get(complexity)
        return override.ladder if override is not None else self.ladder


class RolesConfig(ConfigModel):
    """`[roles]`."""

    leader: RoleConfig
    implementer: ImplementerConfig
    helper: RoleConfig


class PaneLeaf(ConfigModel):
    """A dashboard layout leaf naming the pane it holds."""

    pane: PaneName
    size: int | None = None


class LayoutSplit(ConfigModel):
    """A dashboard layout split holding nested panes or further splits."""

    split: LayoutSplitAxis
    panes: list[LayoutNode]


LayoutNode = LayoutSplit | PaneLeaf


class TitlesConfig(ConfigModel):
    """`[dashboard.titles]` (field templates: 06 §5.3)."""

    window: str
    leader: str
    implementer: str
    system: str
    shell: str


class DashboardConfig(ConfigModel):
    """`[dashboard]`."""

    widgets: list[str]
    layout: LayoutSplit
    titles: TitlesConfig


class NotifyConfig(ConfigModel):
    """`[notify]`."""

    command: str


class Settings(ConfigModel):
    """The full merged and validated Shikiru configuration."""

    project: ProjectConfig
    repo: RepoConfig = Field(default_factory=RepoConfig)
    orchestration: OrchestrationConfig
    agents: dict[str, AgentConfig] = Field(default_factory=dict)
    roles: RolesConfig
    dashboard: DashboardConfig
    notify: NotifyConfig

    def ladder_rungs(self) -> list[Rung]:
        """Return every configured rung: leader, implementer, complexity overrides, helper."""
        rungs = [*self.roles.leader.ladder, *self.roles.implementer.ladder]
        for override in self.roles.implementer.complexity.values():
            rungs.extend(override.ladder)
        rungs.extend(self.roles.helper.ladder)
        return rungs

    @model_validator(mode="after")
    def _every_ladder_agent_has_a_table(self) -> Settings:
        wanted = {rung.agent for rung in self.ladder_rungs()}
        missing = sorted(wanted - self.agents.keys())
        if missing:
            names = ", ".join(missing)
            message = f"no [agents.<name>] table for: {names}"
            raise ValueError(message)
        return self

    @model_validator(mode="after")
    def _helper_ladder_has_one_rung(self) -> Settings:
        if len(self.roles.helper.ladder) != 1:
            message = "roles.helper.ladder must contain exactly one rung"
            raise ValueError(message)
        return self
