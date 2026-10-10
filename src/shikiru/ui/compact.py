"""Compact shared readouts for usage, resources and task activity."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from rich.cells import cell_len
from rich.console import Group, RenderableType
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from shikiru.agents.contracts import (
    BILLING_MODE_API,
    BILLING_MODE_SUBSCRIPTION,
    BILLING_MODE_UNKNOWN,
    BILLING_SOURCE_OPERATOR_CONFIG,
    BILLING_SOURCE_PROVIDER_REPORTED,
    BILLING_SOURCE_UNKNOWN,
    COST_BASIS_API_EQUIVALENT,
    COST_BASIS_BILLED,
    COST_BASIS_UNKNOWN,
)
from shikiru.domain.escalation import escalation_reason_label
from shikiru.domain.tasks import EscalationReason, Role, Status
from shikiru.ui.glyphs import GLYPH_CURRENT_STAGE
from shikiru.ui.strip import PanelState
from shikiru.ui.tokens import (
    ALERT_COLOR,
    AMBER,
    NEUTRAL_BORDER_COLOR,
    PRIMARY_TEXT_COLOR,
    SAGE,
    SECONDARY_TEXT_COLOR,
    STEEL_BLUE,
    STYLE_BOLD,
)

if TYPE_CHECKING:
    from datetime import datetime, timedelta

    from rich.console import Console, ConsoleOptions, RenderResult

    from shikiru.ui.strip import TaskStripData

_BOLD_PRIMARY = f"{STYLE_BOLD} {PRIMARY_TEXT_COLOR}"
GIB_FORMAT = ".1f"
BYTES_PER_GIB = 1024**3
SESSION_COST_FORMAT = ".2f"
COUNT_SHORT_SCALE = 1_000
COUNT_SHORT_MILLION = COUNT_SHORT_SCALE * COUNT_SHORT_SCALE
COUNT_SHORT_PRECISION = 1
SECONDS_PER_MINUTE = 60
MINUTES_PER_HOUR = 60
SECONDS_PER_HOUR = SECONDS_PER_MINUTE * MINUTES_PER_HOUR
HOURS_PER_DAY = 24
FOCUS_ATTENTION_MAX_HEIGHT = 2
RESOURCE_GROUP_SEPARATOR = " · "
RESOURCE_FAILED = "failed"
RESOURCE_UNAVAILABLE = "unavailable"
RESOURCE_STALE = "stale"
RESOURCE_EMPTY = "empty"
RESOURCE_AVAILABLE = "available"
RESOURCE_STATE_UNKNOWN = "sample state unknown"
RESOURCE_GROUP_MIN_GAP = 3
STATUS_ALIGNMENT_MIN_GAP = 1
ROLE_LABELS = {
    Role.LEADER.value: "Leader",
    Role.IMPLEMENTER.value: "Implementer",
    Role.HELPER.value: "Helper",
}
MILESTONE_DONE = "done"
MILESTONE_CURRENT = "current"
MILESTONE_LATER = "later"
MILESTONE_SKIPPED = "skipped"
MILESTONE_UNKNOWN = "unknown"
MILESTONE_REWORK = "rework"
NO_ACTIVE_STAGE_TEXT = {
    Status.DONE: "Stage · complete",
    Status.QUEUED: "Stage · not started",
    Status.RUNNING: "Stage · setup",
    Status.INTEGRATING: "Stage · integration setup",
    Status.ESCALATED: "Stage · no active stage · decision held",
    Status.CANCELLED: "Stage · no active stage · cancelled",
}


class _RecordedUsage(Protocol):
    @property
    def session_count(self) -> int: ...

    @property
    def input_tokens(self) -> int: ...

    @property
    def output_tokens(self) -> int: ...

    @property
    def cache_read_tokens(self) -> int: ...

    @property
    def cache_write_tokens(self) -> int: ...

    @property
    def reported_sessions(self) -> int: ...

    @property
    def legacy_token_sessions(self) -> int: ...

    @property
    def unknown_token_sessions(self) -> int: ...

    @property
    def billed_cost_usd(self) -> float | None: ...

    @property
    def billed_cost_sessions(self) -> int: ...

    @property
    def api_billed_cost_usd(self) -> float | None: ...

    @property
    def api_billed_cost_sessions(self) -> int: ...

    @property
    def api_cost_unreported_sessions(self) -> int: ...

    @property
    def api_equivalent_cost_usd(self) -> float | None: ...

    @property
    def api_equivalent_cost_sessions(self) -> int: ...

    @property
    def unknown_billing_sessions(self) -> int: ...

    @property
    def unknown_billing_operator_config_sessions(self) -> int: ...

    @property
    def unknown_billing_provider_reported_sessions(self) -> int: ...

    @property
    def unknown_billing_source_unknown_sessions(self) -> int: ...

    @property
    def subscription_sessions(self) -> int: ...

    @property
    def subscription_agents(self) -> tuple[str, ...]: ...


class _AgentUsageSummary(Protocol):
    @property
    def agent(self) -> str: ...

    @property
    def input_tokens(self) -> int: ...

    @property
    def output_tokens(self) -> int: ...

    @property
    def cache_read_tokens(self) -> int: ...

    @property
    def cache_write_tokens(self) -> int: ...

    @property
    def run_count(self) -> int: ...

    @property
    def usage_reported_runs(self) -> int: ...

    @property
    def legacy_token_runs(self) -> int: ...

    @property
    def legacy_unknown_token_runs(self) -> int: ...

    @property
    def billed_cost_usd(self) -> float | None: ...

    @property
    def billed_cost_runs(self) -> int: ...

    @property
    def legacy_billed_cost_runs(self) -> int: ...

    @property
    def api_equivalent_cost_usd(self) -> float | None: ...

    @property
    def api_equivalent_cost_runs(self) -> int: ...

    @property
    def legacy_api_equivalent_cost_runs(self) -> int: ...

    @property
    def unknown_cost_runs(self) -> int: ...


class _ResourceSample(Protocol):
    @property
    def state(self) -> str | None: ...

    @property
    def host_cpu_pct(self) -> float: ...

    @property
    def host_ram_used_pct(self) -> float: ...

    @property
    def free_ram_gb(self) -> float: ...

    @property
    def host_ram_used_bytes(self) -> int | None: ...

    @property
    def host_ram_total_bytes(self) -> int | None: ...

    @property
    def instance_cpu_pct(self) -> float | None: ...

    @property
    def instance_rss_gb(self) -> float | None: ...

    @property
    def shikiru_cpu_pct(self) -> float | None: ...

    @property
    def shikiru_rss_gb(self) -> float | None: ...

    @property
    def agents_cpu_pct(self) -> float | None: ...

    @property
    def agents_rss_gb(self) -> float | None: ...

    @property
    def shikiru_process_tree_empty(self) -> bool | None: ...

    @property
    def agents_process_tree_empty(self) -> bool | None: ...

    @property
    def sampled_ago(self) -> str | None: ...

    @property
    def error(self) -> str | None: ...


class _AttentionDecision(Protocol):
    @property
    def escalation_id(self) -> str: ...

    @property
    def headline(self) -> str: ...

    @property
    def reason(self) -> EscalationReason | None: ...


class _AttentionGroup(Protocol):
    @property
    def task_id(self) -> str | None: ...

    @property
    def reasons(self) -> tuple[str, ...]: ...

    @property
    def escalations(self) -> tuple[_AttentionDecision, ...]: ...


class _HeldTaskState(Protocol):
    @property
    def task_id(self) -> str: ...

    @property
    def holds(self) -> tuple[str, ...]: ...

    @property
    def operator_owned_roles(self) -> tuple[str, ...]: ...


class _AttentionSummary(Protocol):
    @property
    def engine_up(self) -> bool | None: ...

    @property
    def held_tasks(self) -> int | None: ...

    @property
    def holds(self) -> tuple[str, ...] | None: ...

    @property
    def operator_owned(self) -> tuple[str, ...] | None: ...

    @property
    def held_task_states(self) -> tuple[_HeldTaskState, ...] | None: ...


class _Attention(Protocol):
    @property
    def groups(self) -> tuple[_AttentionGroup, ...]: ...

    @property
    def decisions(self) -> int: ...


class _TaskSession(Protocol):
    @property
    def role(self) -> str: ...

    @property
    def agent(self) -> str: ...

    @property
    def model(self) -> str: ...

    @property
    def effort(self) -> str | None: ...

    @property
    def current_lineage(self) -> bool: ...

    @property
    def input_tokens(self) -> int: ...

    @property
    def output_tokens(self) -> int: ...

    @property
    def cache_read_tokens(self) -> int: ...

    @property
    def cache_write_tokens(self) -> int: ...

    @property
    def usage_reported(self) -> bool | None: ...

    @property
    def legacy_token_totals(self) -> bool: ...

    @property
    def billing_mode(self) -> str: ...

    @property
    def cost_usd(self) -> float | None: ...

    @property
    def cost_basis(self) -> str: ...

    @property
    def legacy_cost_recorded(self) -> bool: ...


class _TaskDetail(Protocol):
    @property
    def actual_stage(self) -> str | None: ...

    @property
    def status(self) -> str: ...

    @property
    def acting_role(self) -> str: ...

    @property
    def actual_stage_started_at(self) -> datetime | None: ...

    @property
    def stage_ended_at(self) -> datetime | None: ...

    @property
    def milestones(self) -> tuple[_TaskMilestone, ...]: ...

    @property
    def progress(self) -> TaskStripData: ...


class _TaskMilestone(Protocol):
    @property
    def stage(self) -> str: ...

    @property
    def state(self) -> str: ...


class _TaskUsage(Protocol):
    @property
    def reported_sessions(self) -> int: ...

    @property
    def legacy_token_sessions(self) -> int: ...

    @property
    def unknown_token_sessions(self) -> int: ...

    @property
    def input_tokens(self) -> int: ...

    @property
    def output_tokens(self) -> int: ...

    @property
    def billed_cost_usd(self) -> float | None: ...

    @property
    def billed_cost_sessions(self) -> int: ...

    @property
    def api_billed_cost_usd(self) -> float | None: ...

    @property
    def api_billed_cost_sessions(self) -> int: ...

    @property
    def api_cost_unreported_sessions(self) -> int: ...

    @property
    def api_equivalent_cost_usd(self) -> float | None: ...

    @property
    def api_equivalent_cost_sessions(self) -> int: ...

    @property
    def unknown_billing_sessions(self) -> int: ...

    @property
    def unknown_billing_operator_config_sessions(self) -> int: ...

    @property
    def unknown_billing_provider_reported_sessions(self) -> int: ...

    @property
    def unknown_billing_source_unknown_sessions(self) -> int: ...

    @property
    def subscription_sessions(self) -> int: ...

    @property
    def session_count(self) -> int: ...


class _TaskSignal(Protocol):
    @property
    def latest_signal(self) -> str | None: ...

    @property
    def latest_signal_at(self) -> datetime | None: ...

    @property
    def latest_signal_role(self) -> str | None: ...

    @property
    def latest_signal_turn(self) -> str | None: ...

    @property
    def latest_signal_uncertain(self) -> bool: ...

    @property
    def latest_signal_error(self) -> str | None: ...

    @property
    def latest_signal_incomplete(self) -> bool: ...


@dataclass(frozen=True)
class ProjectUsageMetric:
    """Render compact recorded token and billing coverage."""

    usage: _RecordedUsage
    scope: str

    def render(self, *, home: bool = False) -> Group | Text:
        if home:
            return _home_project_usage_text(self.usage)
        usage = self.usage
        known_token_sessions = usage.reported_sessions + usage.legacy_token_sessions
        tokens = (
            f"{_compact_count(usage.input_tokens + usage.output_tokens)} input+output"
            if known_token_sessions
            else "unknown"
        )
        token_coverage = _nonzero_coverage(
            (usage.reported_sessions, "reported"),
            (usage.legacy_token_sessions, "legacy"),
            (usage.unknown_token_sessions, "unknown"),
        )
        token_suffix = f" · {token_coverage}" if token_coverage else ""
        lines = [Text.assemble((f"{self.scope} · Tokens ", "bold"), (tokens, STEEL_BLUE))]
        if token_suffix:
            lines[0].append(token_suffix, style=SECONDARY_TEXT_COLOR)

        billing = _primary_billing_summary(usage)
        if billing:
            lines.append(Text(billing, style=SECONDARY_TEXT_COLOR))
        return Group(*lines)


@dataclass(frozen=True)
class TaskUsageMetric:
    """Render the selected task's compact recorded token and charge summary."""

    usage: _TaskUsage | None

    def render(self) -> Text:
        usage = self.usage
        if usage is None:
            return Text("Task usage unknown", style=SECONDARY_TEXT_COLOR)
        return _join_text([_task_usage_tokens(usage), *_task_usage_charges(usage)])


def _task_usage_tokens(usage: _TaskUsage) -> Text:
    known_token_sessions = usage.reported_sessions + usage.legacy_token_sessions
    token_label = (
        f"{_compact_count(usage.input_tokens + usage.output_tokens)} tokens"
        if known_token_sessions
        else "tokens unknown"
    )
    text = Text(
        token_label,
        style=STEEL_BLUE if known_token_sessions else SECONDARY_TEXT_COLOR,
    )
    if known_token_sessions and usage.legacy_token_sessions:
        text.append(f" · {usage.legacy_token_sessions} legacy", style=SECONDARY_TEXT_COLOR)
    if known_token_sessions and usage.unknown_token_sessions:
        text.append(f" · {usage.unknown_token_sessions} tokens unknown")
    return text


def _task_usage_charges(usage: _TaskUsage) -> list[Text]:
    charges: list[Text] = []
    if usage.api_billed_cost_sessions and usage.api_billed_cost_usd is not None:
        charges.append(Text(f"API ${usage.api_billed_cost_usd:{SESSION_COST_FORMAT}}", style=SAGE))
    if (
        usage.billed_cost_sessions
        and usage.billed_cost_usd is not None
        and usage.billed_cost_usd != usage.api_billed_cost_usd
    ):
        charges.append(Text(f"billed ${usage.billed_cost_usd:{SESSION_COST_FORMAT}}", style=SAGE))
    if usage.api_equivalent_cost_sessions and usage.api_equivalent_cost_usd is not None:
        charges.append(
            Text(
                f"API est. ${usage.api_equivalent_cost_usd:{SESSION_COST_FORMAT}}",
                style=AMBER,
            )
        )
    if usage.api_cost_unreported_sessions:
        charges.append(Text(f"API unreported {usage.api_cost_unreported_sessions}"))
    if usage.unknown_billing_sessions:
        charges.append(
            Text(
                f"billing unknown {usage.unknown_billing_sessions}",
                style=SECONDARY_TEXT_COLOR,
            )
        )
    return charges


def task_signal_text(signal: _TaskSignal, now: datetime) -> Group:
    """Render the latest retained signal and only its recorded observation age."""
    if signal.latest_signal is None:
        if signal.latest_signal_error:
            return Group(
                Text.assemble(("\u203a ", STEEL_BLUE), ("Signal evidence unavailable", AMBER)),
                Text("Last recorded signal · evidence unavailable", style=SECONDARY_TEXT_COLOR),
            )
        return Group(
            Text.assemble(("\u203a ", STEEL_BLUE), ("No retained signal", SECONDARY_TEXT_COLOR)),
            Text("Last recorded signal · none recorded", style=SECONDARY_TEXT_COLOR),
        )
    uncertainty = " · ordering uncertain" if signal.latest_signal_uncertain else ""
    if signal.latest_signal_incomplete:
        uncertainty = " · incomplete row"
    if signal.latest_signal_at is None:
        age = f"time unknown{uncertainty}"
    else:
        age = _recorded_age(signal.latest_signal_at, now) + uncertainty
    return Group(
        Text.assemble(("\u203a ", STEEL_BLUE), (signal.latest_signal, PRIMARY_TEXT_COLOR)),
        Text(
            f"Last recorded signal · {age}",
            style=SECONDARY_TEXT_COLOR,
        ),
    )


def _join_text(parts: list[Text]) -> Text:
    result = Text()
    for index, part in enumerate(parts):
        if index:
            result.append(" · ", style=SECONDARY_TEXT_COLOR)
        result.append_text(part)
    return result


def _home_project_usage_text(usage: _RecordedUsage) -> Text:
    known_token_sessions = usage.reported_sessions + usage.legacy_token_sessions
    tokens = (
        f"{_compact_count(usage.input_tokens + usage.output_tokens)} tokens"
        if known_token_sessions
        else "tokens unknown"
    )
    text = Text(tokens, style=STEEL_BLUE if known_token_sessions else SECONDARY_TEXT_COLOR)
    if usage.session_count == 0:
        session_coverage = "0 sessions"
    elif usage.legacy_token_sessions:
        session_coverage = (
            f"{usage.reported_sessions} reported + {usage.legacy_token_sessions} legacy / "
            f"{usage.session_count} sessions"
        )
    else:
        session_coverage = f"{usage.reported_sessions}/{usage.session_count} sessions"
    text.append(f" · {session_coverage}", style=SECONDARY_TEXT_COLOR)
    billing = _primary_billing_summary(usage, home=True)
    if billing:
        text.append(f" · {billing}", style=SECONDARY_TEXT_COLOR)
    return text


@dataclass(frozen=True)
class ProjectUsageDetails:
    """Render detailed token, cost-basis and billing-mode evidence."""

    usage: _RecordedUsage

    def render(self) -> Group:
        usage = self.usage
        token_known = usage.reported_sessions + usage.legacy_token_sessions > 0
        if token_known:
            tokens = Text.assemble(
                ("Tokens · ", SECONDARY_TEXT_COLOR),
                f"input {usage.input_tokens:,} · output {usage.output_tokens:,} · "
                f"cache read {usage.cache_read_tokens:,} · "
                f"cache write {usage.cache_write_tokens:,}",
            )
        else:
            tokens = Text("Token values unknown")
        coverage = Text(
            f"Coverage · {usage.reported_sessions} reported · "
            f"{usage.legacy_token_sessions} legacy positive · "
            f"{usage.unknown_token_sessions} unknown / {_session_count(usage.session_count)}",
            style=SECONDARY_TEXT_COLOR,
        )
        lines: list[RenderableType] = [tokens, coverage]
        if usage.billed_cost_sessions and usage.billed_cost_usd is not None:
            lines.append(
                Text(
                    f"All-mode billed · ${usage.billed_cost_usd:{SESSION_COST_FORMAT}} · "
                    f"{_session_count(usage.billed_cost_sessions)}",
                    style=SAGE,
                )
            )
        if usage.api_billed_cost_sessions and usage.api_billed_cost_usd is not None:
            lines.append(
                Text(
                    f"API billed · ${usage.api_billed_cost_usd:{SESSION_COST_FORMAT}} · "
                    f"{_session_count(usage.api_billed_cost_sessions)}",
                    style=SAGE,
                )
            )
        if usage.api_cost_unreported_sessions:
            lines.append(
                Text(
                    f"API charges unreported · "
                    f"{_session_count(usage.api_cost_unreported_sessions)}",
                    style=SECONDARY_TEXT_COLOR,
                )
            )
        if usage.api_equivalent_cost_sessions and usage.api_equivalent_cost_usd is not None:
            lines.append(
                Text(
                    f"API-equivalent estimate · "
                    f"${usage.api_equivalent_cost_usd:{SESSION_COST_FORMAT}} · "
                    f"{_session_count(usage.api_equivalent_cost_sessions)}",
                    style=AMBER,
                )
            )
        if usage.subscription_sessions:
            agents = ", ".join(usage.subscription_agents)
            lines.append(
                Text(
                    f"Subscription · {_session_count(usage.subscription_sessions)} · "
                    f"{_count_label(len(usage.subscription_agents), 'declared agent')} · {agents}",
                    style=SAGE,
                )
            )
        if usage.unknown_billing_sessions:
            lines.append(
                Text(
                    f"Billing mode unknown · {_session_count(usage.unknown_billing_sessions)}",
                    style=SECONDARY_TEXT_COLOR,
                )
            )
            lines.extend(_unknown_billing_source_lines(usage))
        lines.append(
            Text(
                f'New sessions: set [agents.<name>.options].billing_mode to "{BILLING_MODE_API}", '
                f'"{BILLING_MODE_SUBSCRIPTION}" or "{BILLING_MODE_UNKNOWN}".',
                style=SECONDARY_TEXT_COLOR,
            )
        )
        lines.append(
            Text("Existing sessions keep their recorded declaration.", style=SECONDARY_TEXT_COLOR)
        )
        return Group(*lines)


def _unknown_billing_source_lines(usage: _RecordedUsage) -> list[Text]:
    sources = (
        (
            usage.unknown_billing_operator_config_sessions,
            BILLING_SOURCE_OPERATOR_CONFIG,
            f"explicitly set to {BILLING_MODE_UNKNOWN}",
        ),
        (
            usage.unknown_billing_provider_reported_sessions,
            BILLING_SOURCE_PROVIDER_REPORTED,
            f"provider-reported {BILLING_MODE_UNKNOWN}",
        ),
        (
            usage.unknown_billing_source_unknown_sessions,
            f"{BILLING_SOURCE_UNKNOWN} source",
            "declaration unavailable/not recorded",
        ),
    )
    return [
        Text(
            f"{source} · {_session_count(count)} {description}",
            style=SECONDARY_TEXT_COLOR,
        )
        for count, source, description in sources
        if count
    ]


def _primary_billing_summary(usage: _RecordedUsage, *, home: bool = False) -> str:
    parts: list[str] = []
    all_amount = usage.billed_cost_usd
    api_amount = usage.api_billed_cost_usd
    all_mode_differs = (
        usage.billed_cost_sessions != usage.api_billed_cost_sessions or all_amount != api_amount
    )
    if all_mode_differs and usage.billed_cost_sessions and all_amount is not None:
        parts.append(
            f"All billed ${all_amount:{SESSION_COST_FORMAT}} · "
            f"{_session_count(usage.billed_cost_sessions)}"
        )
    if usage.api_billed_cost_sessions and api_amount is not None:
        parts.append(
            f"API billed ${api_amount:{SESSION_COST_FORMAT}}"
            if home
            else f"API billed ${api_amount:{SESSION_COST_FORMAT}} · "
            f"{_session_count(usage.api_billed_cost_sessions)}"
        )
    if usage.api_cost_unreported_sessions:
        parts.append(
            f"API unreported {usage.api_cost_unreported_sessions}"
            if home
            else f"{_count_label(usage.api_cost_unreported_sessions, 'API charge')} unreported"
        )
    if usage.subscription_agents:
        parts.append(f"{_count_label(len(usage.subscription_agents), 'subscription agent')}")
    if usage.unknown_billing_sessions:
        parts.append(f"{usage.unknown_billing_sessions} billing modes unknown")
    return " · ".join(parts)


def _nonzero_coverage(*counts: tuple[int, str]) -> str:
    return " · ".join(f"{count} {label}" for count, label in counts if count)


def _compact_count(count: int) -> str:
    if count >= COUNT_SHORT_MILLION:
        return f"{count / COUNT_SHORT_MILLION:.{COUNT_SHORT_PRECISION}f}M"
    if count >= COUNT_SHORT_SCALE:
        return f"{count / COUNT_SHORT_SCALE:.{COUNT_SHORT_PRECISION}f}k"
    return f"{count:,}"


def _session_count(count: int) -> str:
    noun = "session" if count == 1 else "sessions"
    return f"{count} {noun}"


def _count_label(count: int, noun: str) -> str:
    plural = noun if count == 1 else f"{noun}s"
    return f"{count} {plural}"


@dataclass(frozen=True)
class ResourceMetric:
    """Render one width-wrapping host, Shikiru and agent observation."""

    sample: _ResourceSample | None

    def render(self, *, width: int | None = None) -> Text:
        if self.sample is None:
            return Text("Resources unavailable · no sample recorded")
        state = self.sample.state
        if state == RESOURCE_FAILED:
            return _resource_state_line(RESOURCE_FAILED, self.sample)
        if state == RESOURCE_UNAVAILABLE:
            return _resource_state_line(RESOURCE_UNAVAILABLE, self.sample)
        groups = (
            _host_resource_text(self.sample),
            _process_resource_text(
                "Shikiru",
                self.sample.shikiru_cpu_pct,
                self.sample.shikiru_rss_gb,
                self.sample.host_ram_total_bytes,
                self.sample.shikiru_process_tree_empty,
            ),
            _process_resource_text(
                "Agents",
                self.sample.agents_cpu_pct,
                self.sample.agents_rss_gb,
                self.sample.host_ram_total_bytes,
                self.sample.agents_process_tree_empty,
            ),
        )
        metrics = _wrap_resource_groups(groups, width)
        if state == RESOURCE_STALE:
            age = f" · observed {self.sample.sampled_ago} ago" if self.sample.sampled_ago else ""
            error = f" · {self.sample.error}" if self.sample.error else ""
            return Text.assemble(
                (f"Stale{age}{error}", AMBER),
                "\n",
                metrics,
            )
        if state not in {RESOURCE_AVAILABLE, RESOURCE_EMPTY}:
            return Text.assemble(
                (f"{RESOURCE_STATE_UNKNOWN}\n", SECONDARY_TEXT_COLOR),
                metrics,
            )
        return metrics


def _resource_state_line(state: str, sample: _ResourceSample) -> Text:
    error = f" · {sample.error}" if sample.error else ""
    age = f" · observed {sample.sampled_ago} ago" if sample.sampled_ago else ""
    style = ALERT_COLOR if state == RESOURCE_FAILED else SECONDARY_TEXT_COLOR
    return Text(f"Resources {state}{error}{age}", style=style)


def _process_resource_text(
    label: str,
    cpu_pct: float | None,
    rss_gb: float | None,
    host_ram_total_bytes: int | None,
    process_tree_empty: bool | None,
) -> Text:
    if cpu_pct is None or rss_gb is None:
        if process_tree_empty:
            return Text(f"{label} process tree empty", style=SECONDARY_TEXT_COLOR)
        return Text(f"{label} CPU/RAM unavailable", style=SECONDARY_TEXT_COLOR)
    if host_ram_total_bytes is None:
        ram = "RAM used/total unavailable"
    else:
        ram = f"RAM {rss_gb:{GIB_FORMAT}}/{host_ram_total_bytes / BYTES_PER_GIB:{GIB_FORMAT}} GiB"
    return Text.assemble(
        (f"{label} ", SECONDARY_TEXT_COLOR),
        (f"CPU {cpu_pct:.0f}% ", STEEL_BLUE),
        (ram, SECONDARY_TEXT_COLOR),
    )


def _host_resource_text(sample: _ResourceSample) -> Text:
    return Text.assemble(
        ("Host ", SECONDARY_TEXT_COLOR),
        (f"CPU {sample.host_cpu_pct:.0f}% ", STEEL_BLUE),
        (_host_ram_text(sample), SECONDARY_TEXT_COLOR),
    )


def _host_ram_text(sample: _ResourceSample) -> str:
    used = sample.host_ram_used_bytes
    total = sample.host_ram_total_bytes
    if used is None or total is None:
        return "RAM used/total unavailable"
    return f"RAM {used / BYTES_PER_GIB:{GIB_FORMAT}}/{total / BYTES_PER_GIB:{GIB_FORMAT}} GiB"


def render_resource_metric(sample: _ResourceSample | None, *, width: int | None = None) -> Text:
    """Render `sample` without starting another resource observation."""
    return ResourceMetric(sample).render(width=width)


def _wrap_resource_groups(groups: tuple[Text, ...], width: int | None) -> Text:
    result = Text()
    line_width = 0
    for group in groups:
        separator = RESOURCE_GROUP_SEPARATOR if line_width else ""
        group_width = cell_len(group.plain)
        exceeds_width = line_width + cell_len(separator) + group_width > width if width else False
        if width is not None and line_width and exceeds_width:
            result.append("\n")
            line_width = 0
            separator = ""
        result.append(separator)
        result.append(group)
        line_width += cell_len(separator) + group_width
    return result


def attention_status_text(
    attention: _Attention,
    summary: _AttentionSummary,
    *,
    action_hint: str,
    selected_group_index: int = 0,
    selected_escalation_id: str | None = None,
    not_escalated_task_id: str | None = None,
    width: int | None = None,
) -> Text:
    """Render the Home status strip from current attention and observed workspace state."""
    if not_escalated_task_id is not None:
        status = _not_escalated_status(
            not_escalated_task_id, attention.decisions, action_hint, width
        )
        if attention.groups:
            _append_engine_state(status, summary)
            _append_held_task_states(status, summary)
        else:
            _append_held_task_states(status, summary, inline=True)
        return status
    if attention.groups:
        group = attention.groups[selected_group_index]
        status = _attention_group_status(
            group,
            action_hint=action_hint,
            selected_escalation_id=selected_escalation_id,
            width=width,
        )
        _append_engine_state(status, summary)
        _append_held_task_states(status, summary)
        return status
    status = Text("✓ No decisions needed", style=SAGE)
    _append_held_task_states(status, summary, inline=True)
    return status


def display_escalation_headline(
    task_id: str | None,
    headline: str,
    reason: EscalationReason | None = None,
) -> str:
    """Drop an already-present leading task identity and name the reason in words.

    The stored headline is never changed; only its exact `step · reason:` segment is shown
    with the reason's Operator-facing label.
    """
    shown = headline if task_id is None else headline.removeprefix(f"{task_id} · ")
    if reason is None:
        return shown
    return shown.replace(f" · {reason.value}:", f" · {escalation_reason_label(reason)}:", 1)


@dataclass(frozen=True)
class FocusAttentionStatus:
    """Render a cached Home attention projection against the current terminal width."""

    attention: _Attention
    summary: _AttentionSummary
    action_hint: str
    selected_group_index: int = 0
    selected_escalation_id: str | None = None
    not_escalated_task_id: str | None = None

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        """Align the existing status and action hint during Rich's current-width render."""
        del console
        yield attention_status_text(
            self.attention,
            self.summary,
            action_hint=self.action_hint,
            selected_group_index=self.selected_group_index,
            selected_escalation_id=self.selected_escalation_id,
            not_escalated_task_id=self.not_escalated_task_id,
            width=options.max_width,
        )


def _attention_group_status(
    group: _AttentionGroup,
    *,
    action_hint: str,
    selected_escalation_id: str | None,
    width: int | None,
) -> Text:
    if group.escalations:
        decision = next(
            (item for item in group.escalations if item.escalation_id == selected_escalation_id),
            group.escalations[0],
        )
        headline = display_escalation_headline(group.task_id, decision.headline, decision.reason)
        status = Text.assemble(
            ("! ", AMBER),
            (decision.escalation_id, f"{STYLE_BOLD} {AMBER}"),
            (" · ", PRIMARY_TEXT_COLOR),
            (group.task_id, _BOLD_PRIMARY) if group.task_id else ("Project", PRIMARY_TEXT_COLOR),
            (f" · {headline}", PRIMARY_TEXT_COLOR),
        )
        _append_aligned_hint(status, action_hint, width)
    else:
        fault = next(
            (reason for reason in group.reasons if reason.startswith("engine fault")),
            None,
        )
        if fault is not None:
            status = Text.assemble(
                ("! ", ALERT_COLOR),
                (f"{group.task_id or 'Project'} · {fault} · Ctrl+P actions", ALERT_COLOR),
            )
        else:
            status = Text.assemble(
                ("! ", AMBER),
                (
                    f"{group.task_id or 'Project'} · {' · '.join(group.reasons)} · {action_hint}",
                    AMBER,
                ),
            )
    return status


def _not_escalated_status(
    task_id: str,
    other_decisions: int,
    action_hint: str,
    width: int | None,
) -> Text:
    status = Text.assemble((task_id, _BOLD_PRIMARY), (" is not escalated", PRIMARY_TEXT_COLOR))
    if other_decisions:
        noun = "decision" if other_decisions == 1 else "decisions"
        status.append(f" · {other_decisions} {noun} on other tasks", style=SECONDARY_TEXT_COLOR)
    _append_aligned_hint(status, action_hint, width)
    return status


def _append_aligned_hint(status: Text, action_hint: str, width: int | None) -> None:
    gap = max(
        width - cell_len(status.plain) - cell_len(action_hint)
        if width is not None
        else STATUS_ALIGNMENT_MIN_GAP,
        STATUS_ALIGNMENT_MIN_GAP,
    )
    status.append(" " * gap)
    status.append(action_hint, style=SECONDARY_TEXT_COLOR)


def _aligned_line(left: RenderableType, right: RenderableType) -> Table:
    line = Table.grid(padding=(0, 0), expand=True)
    line.add_column(ratio=1)
    line.add_column(justify="right")
    line.add_row(left, right)
    return line


def _append_held_task_states(
    status: Text,
    summary: _AttentionSummary,
    *,
    inline: bool = False,
) -> None:
    held_task_states = summary.held_task_states
    if held_task_states is None:
        if summary.operator_owned:
            _append_status_line(
                status,
                f"@ Operator owns {', '.join(summary.operator_owned)}",
                STEEL_BLUE,
                inline=inline,
            )
        if summary.held_tasks:
            _append_status_line(
                status,
                f"= Hold details unavailable for {summary.held_tasks} tasks",
                AMBER,
                inline=inline,
            )
        return
    if summary.held_tasks and not held_task_states:
        _append_status_line(status, "= Hold details unavailable", AMBER, inline=inline)
    if summary.operator_owned and not held_task_states:
        _append_status_line(
            status,
            "@ Operator-owned task details unavailable",
            STEEL_BLUE,
            inline=inline,
        )
    for held_task in held_task_states:
        _append_task_hold_state(status, held_task, inline=inline)


def _append_task_hold_state(
    status: Text,
    held_task: _HeldTaskState,
    *,
    inline: bool,
) -> None:
    owner = (
        f"Operator owns {', '.join(role.title() for role in held_task.operator_owned_roles)}"
        if held_task.operator_owned_roles
        else "held"
    )
    holds = ", ".join(held_task.holds) if held_task.holds else "hold details unavailable"
    _append_status_line(
        status,
        f"{'@' if held_task.operator_owned_roles else '='} {held_task.task_id} · {owner} · {holds}",
        STEEL_BLUE if held_task.operator_owned_roles else AMBER,
        inline=inline,
    )


def _append_engine_state(
    status: Text,
    summary: _AttentionSummary,
) -> None:
    if summary.engine_up is False:
        _append_status_line(status, "○ Engine stopped", SECONDARY_TEXT_COLOR)
    elif summary.engine_up is None:
        _append_status_line(
            status,
            "○ Engine status unavailable",
            SECONDARY_TEXT_COLOR,
        )


def _append_status_line(status: Text, line: str, style: str, *, inline: bool = False) -> None:
    status.append(" · " if inline else "\n")
    status.append(line, style=style)


@dataclass(frozen=True)
class AgentUsageRow:
    """Render one compact role and session-usage pair."""

    session: _TaskSession
    role_state: str

    def render(self) -> Group:
        session = self.session
        role = ROLE_LABELS.get(session.role, session.role.title())
        identity = f"{session.agent}/{session.model}"
        tokens = _session_token_summary(session)
        charge, charge_style = _compact_session_charge(session)
        details = Text.assemble(
            (identity, STEEL_BLUE),
            (f": {tokens}", SECONDARY_TEXT_COLOR),
            (f" · {charge}", charge_style),
        )
        return Group(
            _aligned_line(
                Text(role, style=SECONDARY_TEXT_COLOR),
                Text(self.role_state, style=SECONDARY_TEXT_COLOR),
            ),
            details,
        )


def _session_token_summary(session: _TaskSession) -> str:
    if session.usage_reported is False:
        return "session tokens unknown"
    total = _compact_count(session.input_tokens + session.output_tokens)
    if session.usage_reported is True:
        return f"session-wide {total} tok"
    if session.legacy_token_totals:
        return f"session-wide {total} tok legacy"
    return "session tokens unknown"


def _session_cost_text(session: _TaskSession) -> tuple[str, str]:
    if session.cost_usd is None:
        if session.billing_mode == BILLING_MODE_SUBSCRIPTION:
            return "no per-task charge reported", AMBER
        if session.billing_mode == BILLING_MODE_API:
            return "API charge unreported", SECONDARY_TEXT_COLOR
        return "cost unknown", SECONDARY_TEXT_COLOR
    amount = f"${session.cost_usd:{SESSION_COST_FORMAT}}"
    if session.cost_basis == COST_BASIS_BILLED:
        label = f"billed {amount}"
        style = SAGE
    elif session.cost_basis == COST_BASIS_API_EQUIVALENT:
        label = f"estimate {amount}"
        style = AMBER
    else:
        label = f"amount {amount} · basis unknown"
        style = SECONDARY_TEXT_COLOR
    return label, style


def _compact_session_charge(session: _TaskSession) -> tuple[str, str]:
    if session.billing_mode == BILLING_MODE_SUBSCRIPTION:
        charge, style = "Subscription", SAGE
    elif session.billing_mode == BILLING_MODE_API:
        if session.cost_usd is None:
            charge, style = "API charge unreported", SECONDARY_TEXT_COLOR
        elif session.cost_basis == COST_BASIS_BILLED:
            charge, style = f"API ${session.cost_usd:{SESSION_COST_FORMAT}}", SAGE
        elif session.cost_basis == COST_BASIS_API_EQUIVALENT:
            charge, style = f"API estimate ${session.cost_usd:{SESSION_COST_FORMAT}}", AMBER
        else:
            charge, style = "API amount basis unknown", SECONDARY_TEXT_COLOR
    elif session.cost_usd is None:
        charge, style = "billing unknown", SECONDARY_TEXT_COLOR
    elif session.cost_basis == COST_BASIS_BILLED:
        charge, style = f"reported ${session.cost_usd:{SESSION_COST_FORMAT}}", SAGE
    elif session.cost_basis == COST_BASIS_API_EQUIVALENT:
        charge, style = f"API estimate ${session.cost_usd:{SESSION_COST_FORMAT}}", AMBER
    elif session.cost_basis == COST_BASIS_UNKNOWN:
        charge, style = "amount basis unknown", SECONDARY_TEXT_COLOR
    else:
        charge, style = _session_cost_text(session)
    return charge, style


@dataclass(frozen=True)
class ProjectAgentUsageDetails:
    """Render one agent's token and monetary evidence in the detailed usage view."""

    usage: _AgentUsageSummary

    def render(self) -> Group:
        usage = self.usage
        token_runs = usage.usage_reported_runs + usage.legacy_token_runs
        lines: list[RenderableType] = [Text(f"{usage.agent} · tokens unknown", style="bold")]
        if token_runs:
            lines[0] = Text.assemble(
                (f"{usage.agent} · ", "bold"),
                f"input {usage.input_tokens:,} · output {usage.output_tokens:,} · "
                f"cache read {usage.cache_read_tokens:,} · "
                f"cache write {usage.cache_write_tokens:,}",
            )
        unknown_tokens = usage.run_count - usage.usage_reported_runs - usage.legacy_token_runs
        lines.append(
            Text(
                f"Token coverage · {usage.usage_reported_runs} reported · "
                f"{usage.legacy_token_runs} legacy positive · "
                f"{unknown_tokens} unknown / {_count_label(usage.run_count, 'run')}"
                f"{_legacy_unknown_tokens(usage.legacy_unknown_token_runs)}",
                style=SECONDARY_TEXT_COLOR,
            )
        )
        if usage.billed_cost_runs and usage.billed_cost_usd is not None:
            legacy = _legacy_count(usage.legacy_billed_cost_runs)
            lines.append(
                Text(
                    f"Billed ${usage.billed_cost_usd:{SESSION_COST_FORMAT}} · "
                    f"{_count_label(usage.billed_cost_runs, 'run')}{legacy}",
                    style=SAGE,
                )
            )
        if usage.api_equivalent_cost_runs and usage.api_equivalent_cost_usd is not None:
            legacy = _legacy_count(usage.legacy_api_equivalent_cost_runs)
            lines.append(
                Text(
                    f"API-equivalent estimate "
                    f"${usage.api_equivalent_cost_usd:{SESSION_COST_FORMAT}} · "
                    f"{_count_label(usage.api_equivalent_cost_runs, 'run')}{legacy}",
                    style=AMBER,
                )
            )
        if usage.unknown_cost_runs:
            lines.append(
                Text(
                    f"Cost unknown · {usage.unknown_cost_runs}/"
                    f"{_count_label(usage.run_count, 'run')}",
                    style=SECONDARY_TEXT_COLOR,
                )
            )
        return Group(*lines)


def _legacy_count(count: int) -> str:
    return f" · {count} legacy" if count else ""


def _legacy_unknown_tokens(count: int) -> str:
    return f" · {count} legacy provenance unknown" if count else ""


@dataclass(frozen=True)
class AgentUsageDetails:
    """Render session token, cost basis and source evidence below a compact row."""

    session: _TaskSession

    def render(self) -> Group:
        session = self.session
        if session.usage_reported is False:
            tokens = "Token values unknown"
        elif session.usage_reported is None and not session.legacy_token_totals:
            tokens = "Token provenance unknown"
        else:
            provenance = "legacy recorded · " if session.legacy_token_totals else "reported · "
            tokens = (
                f"{provenance}input {session.input_tokens:,} · output {session.output_tokens:,} · "
                f"cache read {session.cache_read_tokens:,} · "
                f"cache write {session.cache_write_tokens:,}"
            )
        cost = _session_cost_text(session)[0]
        cost_basis = f"Cost basis · {session.cost_basis} · {cost}"
        if session.legacy_cost_recorded:
            cost_basis = f"{cost_basis} · legacy record"
        lines = [
            Text(f"Tokens · {tokens}", style=SECONDARY_TEXT_COLOR),
            Text(cost_basis, style=SECONDARY_TEXT_COLOR),
        ]
        if session.effort:
            lines.append(Text(f"Effort · {session.effort}", style=SECONDARY_TEXT_COLOR))
        return Group(*lines)


@dataclass(frozen=True)
class StageActivity:
    """Render recorded workflow milestones and actual active-stage elapsed time."""

    task: _TaskDetail

    def render(self, now: datetime) -> Group:
        task = self.task
        milestone_line = Text()
        for index, milestone in enumerate(task.milestones):
            if index:
                milestone_line.append("  ", style=SECONDARY_TEXT_COLOR)
            marker, style = _milestone_marker(milestone.state)
            milestone_line.append(f"{marker} {milestone.stage}", style=style)
            if milestone.state in {
                MILESTONE_SKIPPED,
                MILESTONE_REWORK,
                MILESTONE_UNKNOWN,
            }:
                milestone_line.append(f" {milestone.state}", style=style)
        if not task.milestones:
            milestone_line.append("Workflow milestones unavailable", style=SECONDARY_TEXT_COLOR)
        if task.actual_stage is None:
            stage_line = Text(_no_active_stage_text(task.status), style=SECONDARY_TEXT_COLOR)
        else:
            stage = task.actual_stage.replace("_", " ").title()
            end = task.stage_ended_at or now
            elapsed = (
                _duration_text(end - task.actual_stage_started_at)
                if task.actual_stage_started_at is not None
                else "time unknown"
            )
            stage_line = Text.assemble(
                (f"{task.acting_role.title()} · ", SECONDARY_TEXT_COLOR),
                (f"{stage} · {elapsed}", STEEL_BLUE),
            )
            if any(panel.state is PanelState.STALLED for panel in task.progress.panels):
                stage_line.append(" · stalled", style=AMBER)
        return Group(
            milestone_line,
            Rule(style=NEUTRAL_BORDER_COLOR),
            stage_line,
        )


def _no_active_stage_text(status: str) -> str:
    return NO_ACTIVE_STAGE_TEXT[Status(status)]


def _milestone_marker(state: str) -> tuple[str, str]:
    if state == MILESTONE_DONE:
        return "✓", SAGE
    if state == MILESTONE_CURRENT:
        return GLYPH_CURRENT_STAGE, STEEL_BLUE
    if state == MILESTONE_LATER:
        return "·", SECONDARY_TEXT_COLOR
    if state == MILESTONE_SKIPPED:
        return "—", SECONDARY_TEXT_COLOR
    if state == MILESTONE_REWORK:
        return "!", AMBER
    return "?", AMBER


def _duration_text(duration: timedelta) -> str:
    elapsed = duration.total_seconds()
    if elapsed < 0:
        return "future timestamp"
    total_seconds = int(elapsed)
    if total_seconds < SECONDS_PER_MINUTE:
        return f"{total_seconds}s"
    if total_seconds < SECONDS_PER_HOUR:
        return f"{total_seconds // SECONDS_PER_MINUTE}m"
    hours = total_seconds // SECONDS_PER_HOUR
    minutes = (total_seconds % SECONDS_PER_HOUR) // SECONDS_PER_MINUTE
    return f"{hours}h {minutes}m" if minutes else f"{hours}h"


def _recorded_age(timestamp: datetime, now: datetime) -> str:
    elapsed = (now - timestamp).total_seconds()
    if elapsed < 0:
        return "future timestamp"
    seconds = int(elapsed)
    if seconds < SECONDS_PER_MINUTE:
        return f"{seconds}s ago"
    if seconds < SECONDS_PER_HOUR:
        return f"{seconds // SECONDS_PER_MINUTE}m ago"
    hours = seconds // SECONDS_PER_HOUR
    return f"{hours}h ago" if hours < HOURS_PER_DAY else f"{hours // HOURS_PER_DAY}d ago"
