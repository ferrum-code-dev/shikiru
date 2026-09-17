"""Pydantic settings models for every core configuration key (docs/design/05-data.md §1)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from shikiru.agents.contracts import Rung

ComplexityName = Literal["low", "normal", "high"]
IntegrationStrategy = Literal["merge", "pr"]
LayoutSplitAxis = Literal["h", "v"]
PaneName = Literal["system", "leader", "implementer", "shell"]


class ConfigModel(BaseModel):
    """Base for every settings model: unknown keys are rejected, values are immutable."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ProjectConfig(ConfigModel):
    """`[project]`."""

    name: str
    trackers: list[str] = Field(default_factory=list)


class RepoConfig(ConfigModel):
    """One `[[repos]]` entry."""

    path: str = "."
    base: str | None = None
    caches: list[str] = Field(default_factory=list)
    remote: str = "origin"


class ResourcesConfig(ConfigModel):
    """`[orchestration.resources]`."""

    max_cpu_pct: int
    min_free_ram_gb: float


class TimeoutsConfig(ConfigModel):
    """`[orchestration.timeouts]`, all values in seconds."""

    launch: int
    stall: int
    tool: int
    max_quota_wait: int
    integration_wait: int


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

    prompt_prefix: str = ""
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

    animations: bool
    widgets: list[str]
    layout: LayoutSplit
    titles: TitlesConfig


class NotifyConfig(ConfigModel):
    """`[notify]`."""

    command: str


class Settings(ConfigModel):
    """The full merged and validated Shikiru configuration."""

    project: ProjectConfig
    repos: list[RepoConfig] = Field(default_factory=list)
    orchestration: OrchestrationConfig
    agents: dict[str, AgentConfig] = Field(default_factory=dict)
    roles: RolesConfig
    dashboard: DashboardConfig
    notify: NotifyConfig
