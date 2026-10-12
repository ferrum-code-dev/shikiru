"""Pydantic settings models for every core configuration key (docs/design/05-data.md §1)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shikiru.agents.contracts import Rung

ComplexityName = Literal["low", "normal", "high"]
MERGE_INTEGRATION = "merge"
PULL_REQUEST_INTEGRATION = "pr"
IntegrationStrategy = Literal["merge", "pr"]
LayoutSplitAxis = Literal["h", "v"]
DOCKER_SCOPE_ALL = "all"
DockerScope = Literal["project", "all"]
PaneName = Literal["system", "leader", "implementer", "shell"]
GIT_DEFAULT_REMOTE = "origin"
MAX_MOTION_REFRESH_HZ = 4
MAX_TIMELINE_PAGE_SIZE = 49
MAX_TIMELINE_SIGNAL_SCAN_ROWS = 1024
MAX_USAGE_PCT = 100


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
    remote: str = GIT_DEFAULT_REMOTE


class SessionSettings(ConfigModel):
    """`[sessions]`: limits for host-to-host session queries."""

    ssh_timeout_seconds: float = Field(gt=0)


class ResourcesConfig(ConfigModel):
    """`[orchestration.resources]`."""

    max_cpu_pct: int
    min_free_ram_gb: float
    sample_seconds: float = Field(gt=0)
    cpu_window_seconds: float = Field(gt=0)


class TimeoutsConfig(ConfigModel):
    """`[orchestration.timeouts]`, all values in seconds."""

    stall: int
    tool: int
    max_quota_wait: int
    integration_wait: int
    pr_wait: int
    helper: int
    cache_copy: int = Field(gt=0)
    git_command: int = Field(gt=0)


class FaultsConfig(ConfigModel):
    """`[orchestration.faults]`: how the engine retries a failed tick, in seconds and counts."""

    backoff_initial_seconds: float = Field(gt=0)
    backoff_max_seconds: float = Field(gt=0)
    unexpected_fault_limit: int = Field(gt=0)
    stop_notify_seconds: float = Field(gt=0)
    repeat_notify_seconds: float = Field(gt=0)


class NetworkConfig(ConfigModel):
    """`[orchestration.network]`: bounds of the remote calls pull request mode makes."""

    fetch_timeout_seconds: float = Field(gt=0)
    poll_attempts: int = Field(gt=0)
    pr_poll_seconds: float = Field(gt=0)
    pr_close_attempts: int = Field(gt=0)


class OrchestrationConfig(ConfigModel):
    """`[orchestration]`."""

    max_parallel: int
    max_iterations: int
    integration: IntegrationStrategy
    poll_seconds: int
    shutdown_seconds: float = Field(gt=0)
    dispatch_budget_seconds: float = Field(gt=0)
    resources: ResourcesConfig
    timeouts: TimeoutsConfig
    faults: FaultsConfig
    network: NetworkConfig


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
    """`[dashboard.titles]` (field templates and context thresholds: 06 §5.3)."""

    window: str
    leader: str
    implementer: str
    system: str
    shell: str
    context_warn_pct: int = Field(gt=0, le=MAX_USAGE_PCT)
    context_alert_pct: int = Field(gt=0, le=MAX_USAGE_PCT)

    @model_validator(mode="after")
    def _context_warn_below_alert(self) -> TitlesConfig:
        if self.context_warn_pct >= self.context_alert_pct:
            message = (
                "dashboard.titles.context_warn_pct must be below dashboard.titles.context_alert_pct"
            )
            raise ValueError(message)
        return self


class GaugesConfig(ConfigModel):
    """`[dashboard.gauges]`: subscription-limit gauge thresholds (06 §5.3, §6.1)."""

    quota_warn_pct: int = Field(gt=0, le=MAX_USAGE_PCT)
    quota_alert_pct: int = Field(gt=0, le=MAX_USAGE_PCT)

    @model_validator(mode="after")
    def _quota_warn_below_alert(self) -> GaugesConfig:
        if self.quota_warn_pct >= self.quota_alert_pct:
            message = (
                "dashboard.gauges.quota_warn_pct must be below dashboard.gauges.quota_alert_pct"
            )
            raise ValueError(message)
        return self


class DashboardMotionConfig(ConfigModel):
    """Motion cadence and presentation timings for the dashboard."""

    refresh_hz: int = Field(gt=0, le=MAX_MOTION_REFRESH_HZ)
    stream_active_seconds: float | None = Field(
        default=None,
        gt=0,
        description=(
            "Deprecated compatibility setting; signal freshness no longer controls live motion."
        ),
    )
    stamp_flash_milliseconds: int = Field(gt=0)
    escalation_pulse_hz: int = Field(gt=0)
    task_event_limit: int = Field(gt=0)
    merge_event_limit: int = Field(gt=0)


class DashboardConfig(ConfigModel):
    """`[dashboard]`."""

    widgets: list[str]
    layout: LayoutSplit
    titles: TitlesConfig
    gauges: GaugesConfig
    animations: bool
    motion: DashboardMotionConfig
    docker_refresh_seconds: float = Field(gt=0)
    docker_timeout_seconds: float = Field(gt=0)
    docker_scope: DockerScope
    disk_refresh_seconds: float = Field(gt=0)
    disk_warn_free_pct: int = Field(gt=0, le=MAX_USAGE_PCT)
    disk_alert_free_pct: int = Field(gt=0, le=MAX_USAGE_PCT)
    repo_status_cache_seconds: float = Field(gt=0)
    timeline_page_size: int = Field(gt=0, le=MAX_TIMELINE_PAGE_SIZE)
    timeline_scan_rows: int = Field(gt=0, le=MAX_TIMELINE_SIGNAL_SCAN_ROWS)
    timeline_refresh_seconds: float = Field(gt=0)
    recent_task_limit: int = Field(gt=0)
    recent_task_window: int = Field(gt=0)

    @model_validator(mode="after")
    def _recent_limit_covers_window(self) -> DashboardConfig:
        if self.recent_task_limit < self.recent_task_window:
            message = "dashboard.recent_task_limit must be at least dashboard.recent_task_window"
            raise ValueError(message)
        return self

    @model_validator(mode="after")
    def _disk_alert_below_warn(self) -> DashboardConfig:
        if self.disk_alert_free_pct >= self.disk_warn_free_pct:
            message = "dashboard.disk_alert_free_pct must be below dashboard.disk_warn_free_pct"
            raise ValueError(message)
        return self


class NotifyConfig(ConfigModel):
    """`[notify]`: the hook and the seconds it may run before it is killed."""

    command: str
    timeout_seconds: float = Field(gt=0)


class DoctorConfig(ConfigModel):
    """`[doctor]`: `doctor --probe` limits."""

    probe_timeout: float
    probe_stream_tail_lines: int


class LoggingConfig(ConfigModel):
    """`[logging]`: engine log rotation (05 §1)."""

    engine_log_max_bytes: int = Field(gt=0)
    engine_log_backups: int = Field(ge=0)


class RetentionConfig(ConfigModel):
    """`[retention]`: how long finished history is kept (05 §2)."""

    interval_seconds: float = Field(gt=0)
    finished_task_days: float = Field(gt=0)
    event_days: float = Field(gt=0)
    command_days: float = Field(gt=0)


class Settings(ConfigModel):
    """The full merged and validated Shikiru configuration."""

    project: ProjectConfig
    repo: RepoConfig = Field(default_factory=RepoConfig)
    sessions: SessionSettings
    orchestration: OrchestrationConfig
    agents: dict[str, AgentConfig] = Field(default_factory=dict)
    roles: RolesConfig
    dashboard: DashboardConfig
    notify: NotifyConfig
    doctor: DoctorConfig
    logging: LoggingConfig
    retention: RetentionConfig

    def ladder_rungs(self) -> list[Rung]:
        """Return every configured rung: leader, implementer, complexity overrides, helper."""
        rungs = [*self.roles.leader.ladder, *self.roles.implementer.ladder]
        for override in self.roles.implementer.complexity.values():
            rungs.extend(override.ladder)
        rungs.extend(self.roles.helper.ladder)
        return rungs

    def ladder_models(self, agent: str) -> tuple[str, ...]:
        """Return the distinct models the configured ladders select for `agent`."""
        return tuple(sorted({rung.model for rung in self.ladder_rungs() if rung.agent == agent}))

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
