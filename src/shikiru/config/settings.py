"""Pydantic settings models for every core configuration key (docs/design/05-data.md §1)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ComplexityName = Literal["low", "normal", "high"]
IntegrationStrategy = Literal["merge", "pr"]
LayoutSplitAxis = Literal["h", "v"]


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


class RungSpec(ConfigModel):
    """A `{agent, model, effort}` ladder entry as it appears in TOML."""

    agent: str
    model: str
    effort: str | None = None


class ComplexityLadder(ConfigModel):
    """One `[roles.implementer.complexity.<name>]` table."""

    ladder: list[RungSpec]


class ComplexityLadders(ConfigModel):
    """`[roles.implementer.complexity]`: per-complexity ladder overrides, all optional."""

    low: ComplexityLadder | None = None
    normal: ComplexityLadder | None = None
    high: ComplexityLadder | None = None

    def for_complexity(self, complexity: ComplexityName) -> ComplexityLadder | None:
        """Return the configured ladder override for `complexity`, if any."""
        return getattr(self, complexity)


class LeaderConfig(ConfigModel):
    """`[roles.leader]`."""

    ladder: list[RungSpec]


class ImplementerConfig(ConfigModel):
    """`[roles.implementer]`."""

    ladder: list[RungSpec]
    escalate_after: int = 2
    complexity: ComplexityLadders = Field(default_factory=ComplexityLadders)


class HelperConfig(ConfigModel):
    """`[roles.helper]`."""

    ladder: list[RungSpec]


class RolesConfig(ConfigModel):
    """`[roles]`."""

    leader: LeaderConfig
    implementer: ImplementerConfig
    helper: HelperConfig


class PaneLeaf(ConfigModel):
    """A dashboard layout leaf naming the pane it holds."""

    pane: str
    size: int | None = None


class LayoutSplit(ConfigModel):
    """A dashboard layout split holding nested panes or further splits."""

    split: LayoutSplitAxis
    panes: list[LayoutSplit | PaneLeaf]


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

    command: str = ""


class Settings(ConfigModel):
    """The full merged and validated Shikiru configuration."""

    project: ProjectConfig
    repos: list[RepoConfig] = Field(default_factory=list)
    orchestration: OrchestrationConfig
    agents: dict[str, AgentConfig] = Field(default_factory=dict)
    roles: RolesConfig
    dashboard: DashboardConfig
    notify: NotifyConfig = Field(default_factory=NotifyConfig)


def implementer_ladder(
    implementer: ImplementerConfig, complexity: ComplexityName
) -> list[RungSpec]:
    """Resolve the implementer's ladder for `complexity`, falling back to its base ladder."""
    override = implementer.complexity.for_complexity(complexity)
    return override.ladder if override is not None else implementer.ladder
