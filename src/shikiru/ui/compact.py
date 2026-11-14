"""Compact shared readouts for usage, resources and task activity."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from rich.cells import cell_len
from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from shikiru.agents.contracts import (
    BILLING_MODE_API,
    BILLING_MODE_SUBSCRIPTION,
    COST_BASIS_API_EQUIVALENT,
    COST_BASIS_BILLED,
    COST_BASIS_UNKNOWN,
)
from shikiru.domain.escalation import escalation_reason_label
from shikiru.domain.levels import UsageLevel
from shikiru.domain.tasks import EscalationReason, Role
from shikiru.ui.gauge import Gauge
from shikiru.ui.glyphs import GLYPH_CURRENT_STAGE, GLYPH_ESCALATED, GLYPH_STOPPED
from shikiru.ui.keys import append_key_hint
from shikiru.ui.section import Row, grey, labelled_rows, observed_text, wrap_groups
from shikiru.ui.size import format_size
from shikiru.ui.tokens import (
    ALERT_COLOR,
    AMBER,
    PRIMARY_TEXT_COLOR,
    SAGE,
    SECONDARY_TEXT_COLOR,
    STEEL_BLUE,
    STYLE_BOLD,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime, timedelta

    from rich.console import Console, ConsoleOptions, RenderResult


_BOLD_PRIMARY = f"{STYLE_BOLD} {PRIMARY_TEXT_COLOR}"
_BOLD_STEEL = f"{STYLE_BOLD} {STEEL_BLUE}"
_BOLD_SAGE = f"{STYLE_BOLD} {SAGE}"
_BOLD_AMBER = f"{STYLE_BOLD} {AMBER}"
_BOLD_ALERT = f"{STYLE_BOLD} {ALERT_COLOR}"
_FREE_SPACE_STYLES = {UsageLevel.WARN: _BOLD_AMBER, UsageLevel.ALERT: _BOLD_ALERT}
GIB_FORMAT = ".1f"
CPU_DECIMALS_BELOW_PCT = 10
CPU_DECIMAL_FORMAT = ".1f"
CPU_INTEGER_FORMAT = ".0f"
BYTES_PER_GIB = 1024**3
BYTES_PER_MIB = 1024**2
MIB_FORMAT = ".0f"
SESSION_COST_FORMAT = ".2f"
COUNT_SHORT_SCALE = 1_000
COUNT_SHORT_MILLION = COUNT_SHORT_SCALE * COUNT_SHORT_SCALE
COUNT_SHORT_PRECISION = 1
SECONDS_PER_MINUTE = 60
MINUTES_PER_HOUR = 60
SECONDS_PER_HOUR = SECONDS_PER_MINUTE * MINUTES_PER_HOUR
HOURS_PER_DAY = 24
FOCUS_ATTENTION_MAX_HEIGHT = 2
ACTION_HINT = "Ctrl+P actions"
USAGE_TOTALS_LABEL = "Totals"
USAGE_TOKENS_LABEL = "Tokens"
USAGE_COVERAGE_LABEL = "Coverage"
USAGE_BILLING_LABEL = "Billing"
USAGE_LABEL_WIDTH = len(USAGE_COVERAGE_LABEL)
USAGE_BY_AGENT_LABEL = "By agent"
USAGE_LIMITS_LABEL = "Limits"
USAGE_API_TOTAL_WORD = "API"
USAGE_BILLED_WORD = "billed"
USAGE_API_BILLED_WORD = "API billed"
USAGE_API_UNREPORTED_WORD = "API unreported"
USAGE_API_EQUIVALENT_WORD = "API-equivalent"
USAGE_ESTIMATE_PREFIX = "est. "
USAGE_SUBSCRIPTION_WORD = "subscription"
USAGE_UNKNOWN_WORD = "unknown"
USAGE_REPORTED_WORD = "reported"
USAGE_LEGACY_WORD = "legacy"
USAGE_WITHOUT_TOKENS_WORD = "without tokens"
USAGE_TOKENS_WORD = "tokens"
USAGE_TOKENS_IN_LABEL = "in"
USAGE_TOKENS_OUT_LABEL = "out"
USAGE_CACHE_READ_LABEL = "cache r"
USAGE_CACHE_WRITE_LABEL = "w"
USAGE_TOKENS_UNKNOWN = "tokens unknown"
USAGE_COST_UNKNOWN = "cost unknown"
USAGE_RESETS_WORD = "resets"
USAGE_RESET_UNKNOWN = "unknown"
RESOURCE_FAILED = "failed"
RESOURCE_UNAVAILABLE = "unavailable"
RESOURCE_STALE = "stale"
RESOURCE_EMPTY = "empty"
RESOURCE_AVAILABLE = "available"
RESOURCE_STATE_UNKNOWN = "sample state unknown"
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
MILESTONE_STOPPED = "stopped"
MILESTONE_GAP = "  "
MILESTONE_GLYPH_GAP = " "


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
    def billed_cost_usd(self) -> float | None: ...

    @property
    def billed_cost_runs(self) -> int: ...

    @property
    def api_equivalent_cost_usd(self) -> float | None: ...

    @property
    def api_equivalent_cost_runs(self) -> int: ...

    @property
    def unknown_cost_runs(self) -> int: ...


class _QuotaWindow(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def used_pct(self) -> float | None: ...

    @property
    def resets_in(self) -> str: ...

    @property
    def observed_ago(self) -> str: ...


class _AgentQuotas(Protocol):
    @property
    def agent(self) -> str: ...

    @property
    def quotas(self) -> Sequence[_QuotaWindow]: ...


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
    def milestones(self) -> tuple[_TaskMilestone, ...]: ...


class _TaskMilestone(Protocol):
    @property
    def stage(self) -> str: ...

    @property
    def state(self) -> str: ...


@dataclass(frozen=True)
class ProjectUsageMetric:
    """Render the Home token bar."""

    usage: _RecordedUsage

    def render_home(self, width: int | None = None) -> Text:
        """Render the Home token bar, wrapping only between its groups within `width`."""
        return _home_project_usage_text(self.usage, width)


def _known_token_sessions(usage: _RecordedUsage) -> int:
    return usage.reported_sessions + usage.legacy_token_sessions


def _home_project_usage_text(usage: _RecordedUsage, width: int | None) -> Text:
    groups = [*_headline_groups(usage), *_home_coverage_texts(usage)]
    return wrap_groups(tuple(groups), width)


def _headline_groups(usage: _RecordedUsage) -> list[Text]:
    groups = [
        _count_text(usage.session_count, "session", _BOLD_PRIMARY),
        _home_token_text(usage),
    ]
    if usage.api_billed_cost_sessions and usage.api_billed_cost_usd is not None:
        groups.append(_billed_total(usage.api_billed_cost_usd, USAGE_API_TOTAL_WORD))
    return groups


def _billed_total(amount: float, word: str) -> Text:
    return Text.assemble(
        (_cost_text(amount), _BOLD_SAGE),
        (f" {word}", SECONDARY_TEXT_COLOR),
    )


def _cost_text(amount: float) -> str:
    return f"${amount:{SESSION_COST_FORMAT}}"


def _home_token_text(usage: _RecordedUsage) -> Text:
    if not _known_token_sessions(usage):
        return Text("tokens unknown", style=SECONDARY_TEXT_COLOR)
    total = compact_count(
        usage.input_tokens
        + usage.output_tokens
        + usage.cache_read_tokens
        + usage.cache_write_tokens
    )
    return Text.assemble((total, _BOLD_STEEL), (" tokens", SECONDARY_TEXT_COLOR))


def _home_coverage_texts(usage: _RecordedUsage) -> list[Text]:
    labels: list[str] = []
    if _known_token_sessions(usage):
        if usage.legacy_token_sessions:
            labels.append(_count_label(usage.legacy_token_sessions, "legacy session"))
        if usage.unknown_token_sessions:
            labels.append(f"{_count_label(usage.unknown_token_sessions, 'session')} without tokens")
    if usage.api_cost_unreported_sessions:
        labels.append(f"{_count_label(usage.api_cost_unreported_sessions, 'cost')} unreported")
    if usage.unknown_billing_sessions:
        labels.append(f"{_count_label(usage.unknown_billing_sessions, 'billing mode')} unknown")
    return [Text(label, style=SECONDARY_TEXT_COLOR) for label in labels]


@dataclass(frozen=True)
class ProjectUsageTotals:
    """Render recorded usage as labelled Totals, Tokens, Coverage and Billing rows."""

    usage: _RecordedUsage

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        """Wrap each row at its group separators within the current width."""
        del console
        yield labelled_rows(_totals_rows(self.usage), options.max_width, USAGE_LABEL_WIDTH)


def _totals_rows(usage: _RecordedUsage) -> list[Row]:
    rows = [
        (USAGE_TOTALS_LABEL, (*_headline_groups(usage), *_all_mode_billed_groups(usage))),
        (USAGE_TOKENS_LABEL, tuple(_token_detail_groups(usage))),
        (USAGE_COVERAGE_LABEL, tuple(_coverage_groups(usage))),
    ]
    billing_lines = [tuple(_billing_groups(usage)), tuple(_estimate_groups(usage))]
    populated = [groups for groups in billing_lines if groups]
    rows.extend(
        (USAGE_BILLING_LABEL if index == 0 else "", groups)
        for index, groups in enumerate(populated)
    )
    return [(grey(label), groups) for label, groups in rows if groups]


def _all_mode_billed_groups(usage: _RecordedUsage) -> list[Text]:
    amount = usage.billed_cost_usd
    differs = (
        usage.billed_cost_sessions != usage.api_billed_cost_sessions
        or amount != usage.api_billed_cost_usd
    )
    if differs and usage.billed_cost_sessions and amount:
        return [_billed_total(amount, USAGE_BILLED_WORD)]
    return []


def _token_detail_groups(usage: _RecordedUsage) -> list[Text]:
    if not _known_token_sessions(usage):
        return []
    groups = [
        _token_amount(USAGE_TOKENS_IN_LABEL, usage.input_tokens),
        _token_amount(USAGE_TOKENS_OUT_LABEL, usage.output_tokens),
    ]
    if usage.cache_read_tokens or usage.cache_write_tokens:
        groups.append(_token_amount(USAGE_CACHE_READ_LABEL, usage.cache_read_tokens))
        groups.append(_token_amount(USAGE_CACHE_WRITE_LABEL, usage.cache_write_tokens))
    return groups


def _token_amount(label: str, count: int) -> Text:
    return Text.assemble((f"{label} ", SECONDARY_TEXT_COLOR), (compact_count(count), _BOLD_STEEL))


def _coverage_groups(usage: _RecordedUsage) -> list[Text]:
    partial = usage.legacy_token_sessions or usage.unknown_token_sessions
    if not _known_token_sessions(usage) or not partial:
        return []
    return _counted_groups(
        (usage.reported_sessions, USAGE_REPORTED_WORD),
        (usage.legacy_token_sessions, USAGE_LEGACY_WORD),
        (usage.unknown_token_sessions, USAGE_WITHOUT_TOKENS_WORD),
    )


def _billing_groups(usage: _RecordedUsage) -> list[Text]:
    return _counted_groups(
        (usage.subscription_sessions, USAGE_SUBSCRIPTION_WORD),
        (usage.api_billed_cost_sessions, USAGE_API_BILLED_WORD),
        (usage.api_cost_unreported_sessions, USAGE_API_UNREPORTED_WORD),
        (usage.unknown_billing_sessions, USAGE_UNKNOWN_WORD),
    )


def _estimate_groups(usage: _RecordedUsage) -> list[Text]:
    amount = usage.api_equivalent_cost_usd
    if not usage.api_equivalent_cost_sessions or amount is None:
        return []
    sessions = _count_label(usage.api_equivalent_cost_sessions, "session")
    return [_estimate_text(amount, f" {USAGE_API_EQUIVALENT_WORD} ({sessions})")]


def _estimate_text(amount: float, qualifier: str = "") -> Text:
    return Text.assemble(
        (f"{USAGE_ESTIMATE_PREFIX}{_cost_text(amount)}", AMBER),
        (qualifier, SECONDARY_TEXT_COLOR),
    )


def _counted_groups(*counts: tuple[int, str]) -> list[Text]:
    return [_counted(count, label) for count, label in counts if count]


def _counted(count: int, label: str) -> Text:
    return Text.assemble((str(count), _BOLD_PRIMARY), (f" {label}", SECONDARY_TEXT_COLOR))


def compact_count(count: int) -> str:
    if count >= COUNT_SHORT_MILLION:
        return f"{count / COUNT_SHORT_MILLION:.{COUNT_SHORT_PRECISION}f}M"
    if count >= COUNT_SHORT_SCALE:
        return f"{count / COUNT_SHORT_SCALE:.{COUNT_SHORT_PRECISION}f}k"
    return f"{count:,}"


def _count_label(count: int, noun: str) -> str:
    return f"{count} {_plural(count, noun)}"


def _count_text(count: int, noun: str, count_style: str) -> Text:
    return Text.assemble(
        (str(count), count_style),
        (f" {_plural(count, noun)}", SECONDARY_TEXT_COLOR),
    )


def _plural(count: int, noun: str) -> str:
    return noun if count == 1 else f"{noun}s"


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
                self.sample.shikiru_process_tree_empty,
            ),
            _process_resource_text(
                "Agents",
                self.sample.agents_cpu_pct,
                self.sample.agents_rss_gb,
                self.sample.agents_process_tree_empty,
            ),
        )
        metrics = wrap_groups(groups, width)
        if state == RESOURCE_STALE:
            age = _observed_suffix(self.sample.sampled_ago)
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


def _observed_suffix(sampled_ago: str | None) -> str:
    return f" · {observed_text(sampled_ago)}" if sampled_ago else ""


def _resource_state_line(state: str, sample: _ResourceSample) -> Text:
    error = f" · {sample.error}" if sample.error else ""
    age = _observed_suffix(sample.sampled_ago)
    style = ALERT_COLOR if state == RESOURCE_FAILED else SECONDARY_TEXT_COLOR
    return Text(f"Resources {state}{error}{age}", style=style)


def _process_resource_text(
    label: str,
    cpu_pct: float | None,
    rss_gb: float | None,
    process_tree_empty: bool | None,
) -> Text:
    if cpu_pct is None or rss_gb is None:
        if process_tree_empty:
            return Text.assemble(
                (label, _BOLD_PRIMARY),
                (" process tree is empty", SECONDARY_TEXT_COLOR),
            )
        return Text(f"{label} CPU/RSS unavailable", style=SECONDARY_TEXT_COLOR)
    return _resource_group_text(
        label,
        cpu_pct,
        Text.assemble((" RSS", PRIMARY_TEXT_COLOR), (f" {format_gib(rss_gb)} GiB", STEEL_BLUE)),
    )


def _host_resource_text(sample: _ResourceSample) -> Text:
    used = sample.host_ram_used_bytes
    total = sample.host_ram_total_bytes
    if used is None or total is None:
        memory = Text(" RAM used/total unavailable", style=SECONDARY_TEXT_COLOR)
    else:
        memory = Text.assemble(
            (" RAM", PRIMARY_TEXT_COLOR),
            (
                f" {format_gib(used / BYTES_PER_GIB)}/{format_gib(total / BYTES_PER_GIB)} GiB",
                STEEL_BLUE,
            ),
        )
    return _resource_group_text("Host", sample.host_cpu_pct, memory)


def format_gib(gib: float) -> str:
    """Format a GiB amount with one decimal and no unit."""
    return f"{gib:{GIB_FORMAT}}"


def format_memory(memory_bytes: int) -> str:
    """Format bytes as GiB with one decimal from one GiB up, otherwise as whole MiB."""
    if memory_bytes >= BYTES_PER_GIB:
        return f"{format_gib(memory_bytes / BYTES_PER_GIB)} GiB"
    return f"{memory_bytes / BYTES_PER_MIB:{MIB_FORMAT}} MiB"


def free_space_text(free_bytes: int, total_bytes: int, level: UsageLevel | None) -> Text:
    """Render `free of total` as sizes, coloured by `level` and ending in `‼` at warn and alert."""
    style = _FREE_SPACE_STYLES.get(level) if level else None
    text = Text.assemble(
        (format_size(free_bytes), style or _BOLD_STEEL),
        (" of ", SECONDARY_TEXT_COLOR),
        (format_size(total_bytes), _BOLD_STEEL),
    )
    if style:
        text.append(f" {GLYPH_ESCALATED}", style=style)
    return text


def format_cpu_pct(cpu_pct: float) -> str:
    """Format a CPU percentage with one decimal below ten percent, otherwise as an integer."""
    number_format = CPU_DECIMAL_FORMAT if cpu_pct < CPU_DECIMALS_BELOW_PCT else CPU_INTEGER_FORMAT
    return f"{cpu_pct:{number_format}}%"


def _resource_group_text(label: str, cpu_pct: float, memory: Text) -> Text:
    text = Text.assemble(
        (label, _BOLD_PRIMARY),
        (" CPU", PRIMARY_TEXT_COLOR),
        (f" {format_cpu_pct(cpu_pct)}", STEEL_BLUE),
    )
    text.append(memory)
    return text


def render_resource_metric(sample: _ResourceSample | None, *, width: int | None = None) -> Text:
    """Render `sample` without starting another resource observation."""
    return ResourceMetric(sample).render(width=width)


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
            identity = f"{group.task_id or 'Project'} · {fault}"
            status = Text.assemble(("! ", ALERT_COLOR), (identity, ALERT_COLOR))
        else:
            status = Text.assemble(("! ", AMBER))
            append_key_hint(
                status,
                f"{group.task_id or 'Project'} · {' · '.join(group.reasons)} · {action_hint}",
                AMBER,
            )
    return status


def _not_escalated_status(
    task_id: str,
    other_decisions: int,
    action_hint: str,
    width: int | None,
) -> Text:
    status = Text.assemble((task_id, _BOLD_PRIMARY), (" · No decision needed", PRIMARY_TEXT_COLOR))
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
    append_key_hint(status, action_hint, SECONDARY_TEXT_COLOR)


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
    total = compact_count(
        session.input_tokens
        + session.output_tokens
        + session.cache_read_tokens
        + session.cache_write_tokens
    )
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
class AgentUsageRows:
    """Render one row of runs, tokens and cost per agent."""

    usages: Sequence[_AgentUsageSummary]

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        """Wrap each row at its group separators within the current width."""
        del console
        rows = [
            (Text(usage.agent, style=_BOLD_PRIMARY), _agent_groups(usage)) for usage in self.usages
        ]
        label_width = max(cell_len(label.plain) for label, _ in rows)
        yield labelled_rows(rows, options.max_width, label_width)


def _agent_groups(usage: _AgentUsageSummary) -> tuple[Text, ...]:
    return (
        _count_text(usage.run_count, "run", _BOLD_PRIMARY),
        *_agent_token_groups(usage),
        *_agent_cost_groups(usage),
    )


def _agent_token_groups(usage: _AgentUsageSummary) -> list[Text]:
    known_runs = usage.usage_reported_runs + usage.legacy_token_runs
    if not known_runs:
        return [grey(USAGE_TOKENS_UNKNOWN)]
    groups = [
        Text.assemble(
            (
                compact_count(
                    usage.input_tokens
                    + usage.output_tokens
                    + usage.cache_read_tokens
                    + usage.cache_write_tokens
                ),
                _BOLD_STEEL,
            ),
            (f" {USAGE_TOKENS_WORD}", SECONDARY_TEXT_COLOR),
        )
    ]
    if usage.run_count > known_runs:
        groups.append(grey(f"{usage.run_count - known_runs} {USAGE_WITHOUT_TOKENS_WORD}"))
    return groups


def _agent_cost_groups(usage: _AgentUsageSummary) -> list[Text]:
    groups: list[Text] = []
    if usage.billed_cost_runs and usage.billed_cost_usd is not None:
        groups.append(_billed_total(usage.billed_cost_usd, USAGE_BILLED_WORD))
    if usage.api_equivalent_cost_runs and usage.api_equivalent_cost_usd is not None:
        groups.append(_estimate_text(usage.api_equivalent_cost_usd))
    if usage.unknown_cost_runs:
        label = USAGE_COST_UNKNOWN
        groups.append(grey(f"{usage.unknown_cost_runs} {label}" if groups else label))
    return groups


@dataclass(frozen=True)
class AgentQuotaLimits:
    """Render each agent's quota windows as gauge rows with their reset countdowns."""

    usages: Sequence[_AgentQuotas]
    warn_pct: int
    alert_pct: int

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        """Wrap each window row within the current width and note a shared observation age."""
        del console
        windows = [(usage.agent, quota) for usage in self.usages for quota in usage.quotas]
        observed = {quota.observed_ago for _, quota in windows}
        shared = len(observed) == 1
        rows: list[Row] = []
        previous_agent: str | None = None
        for agent, quota in windows:
            label = Text(agent, style=_BOLD_PRIMARY) if agent != previous_agent else Text()
            previous_agent = agent
            gauge = Gauge(quota.name, quota.used_pct, self.warn_pct, self.alert_pct).render()
            gauge.append(f" {USAGE_RESETS_WORD} {quota.resets_in}", style=SECONDARY_TEXT_COLOR)
            if not shared:
                gauge.append(f" · {observed_text(quota.observed_ago)}", style=SECONDARY_TEXT_COLOR)
            rows.append((label, (gauge,)))
        label_width = max(cell_len(label.plain) for label, _ in rows)
        yield labelled_rows(rows, options.max_width, label_width)
        if shared:
            yield grey(observed_text(next(iter(observed))))


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
    """Render the recorded workflow milestones of one task."""

    task: _TaskDetail

    def render(self, *, glyphs_only: bool = False) -> Text:
        """Return the milestone row, as bare glyphs when `glyphs_only`."""
        line = Text()
        for index, milestone in enumerate(self.task.milestones):
            if index:
                line.append(MILESTONE_GLYPH_GAP if glyphs_only else MILESTONE_GAP)
            marker, style = _milestone_marker(milestone.state)
            line.append(marker if glyphs_only else f"{marker} {milestone.stage}", style=style)
            if not glyphs_only and milestone.state in {
                MILESTONE_SKIPPED,
                MILESTONE_REWORK,
                MILESTONE_UNKNOWN,
                MILESTONE_STOPPED,
            }:
                line.append(f" {milestone.state}", style=style)
        return line


_MILESTONE_MARKERS = {
    MILESTONE_DONE: ("✓", SAGE),
    MILESTONE_CURRENT: (GLYPH_CURRENT_STAGE, STEEL_BLUE),
    MILESTONE_LATER: ("·", SECONDARY_TEXT_COLOR),
    MILESTONE_SKIPPED: ("—", SECONDARY_TEXT_COLOR),
    MILESTONE_REWORK: ("!", AMBER),
    MILESTONE_STOPPED: (GLYPH_STOPPED, ALERT_COLOR),
}


def _milestone_marker(state: str) -> tuple[str, str]:
    return _MILESTONE_MARKERS.get(state, ("?", AMBER))


def duration_text(duration: timedelta) -> str:
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


def recorded_age(timestamp: datetime, now: datetime) -> str:
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
