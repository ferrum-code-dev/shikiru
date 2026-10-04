"""Compact shared readouts for usage, resources and task activity."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from rich.console import Group, RenderableType
from rich.text import Text

from shikiru.agents.contracts import (
    BILLING_MODE_API,
    BILLING_MODE_SUBSCRIPTION,
    BILLING_MODE_UNKNOWN,
    COST_BASIS_API_EQUIVALENT,
    COST_BASIS_BILLED,
)
from shikiru.domain.tasks import Role, Status
from shikiru.ui.strip import PanelState
from shikiru.ui.tokens import ALERT_COLOR, AMBER, SAGE, SECONDARY_TEXT_COLOR, STEEL_BLUE

GIB_FORMAT = ".1f"
BYTES_PER_GIB = 1024**3
SESSION_COST_FORMAT = ".2f"
COUNT_SHORT_SCALE = 1_000
COUNT_SHORT_MILLION = COUNT_SHORT_SCALE * COUNT_SHORT_SCALE
COUNT_SHORT_PRECISION = 1
FOCUS_ATTENTION_MAX_HEIGHT = 3
RESOURCE_FAILED = "failed"
RESOURCE_UNAVAILABLE = "unavailable"
RESOURCE_STALE = "stale"
RESOURCE_EMPTY = "empty"
RESOURCE_AVAILABLE = "available"
RESOURCE_STATE_UNKNOWN = "sample state unknown"
UNSTARTED_STEP = "not started"
SETUP_STEP = "setup"
ROLE_LABELS = {
    Role.LEADER.value: "Leader",
    Role.IMPLEMENTER.value: "Implementer",
    Role.HELPER.value: "Helper",
}
MODE_LABELS = {
    BILLING_MODE_API: "API",
    BILLING_MODE_SUBSCRIPTION: "subscription",
    BILLING_MODE_UNKNOWN: "billing unknown",
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
    def sampled_ago(self) -> str | None: ...

    @property
    def error(self) -> str | None: ...


class _AttentionDecision(Protocol):
    @property
    def escalation_id(self) -> str: ...

    @property
    def headline(self) -> str: ...


class _AttentionGroup(Protocol):
    @property
    def task_id(self) -> str | None: ...

    @property
    def reasons(self) -> tuple[str, ...]: ...

    @property
    def escalations(self) -> tuple[_AttentionDecision, ...]: ...


class _AttentionSummary(Protocol):
    @property
    def engine_up(self) -> bool | None: ...

    @property
    def held_tasks(self) -> int | None: ...

    @property
    def holds(self) -> tuple[str, ...] | None: ...

    @property
    def operator_owned(self) -> tuple[str, ...] | None: ...


class _Attention(Protocol):
    @property
    def groups(self) -> tuple[_AttentionGroup, ...]: ...


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


class _TaskProgressPanel(Protocol):
    @property
    def state(self) -> PanelState: ...

    @property
    def spinner_frame(self) -> str: ...


class _TaskProgress(Protocol):
    @property
    def panels(self) -> tuple[_TaskProgressPanel, ...]: ...


class _TaskDetail(Protocol):
    @property
    def step(self) -> str: ...

    @property
    def status(self) -> str: ...

    @property
    def iteration(self) -> int: ...

    @property
    def progress(self) -> _TaskProgress: ...

    @property
    def acting_role(self) -> str: ...

    @property
    def signal_observed(self) -> bool: ...


@dataclass(frozen=True)
class ProjectUsageMetric:
    """Render compact recorded token and billing coverage."""

    usage: _RecordedUsage
    scope: str

    def render(self) -> Group:
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
                    f"declared agents {agents}",
                    style=AMBER,
                )
            )
        if usage.unknown_billing_sessions:
            lines.append(
                Text(
                    f"Billing mode unknown · {_session_count(usage.unknown_billing_sessions)}",
                    style=SECONDARY_TEXT_COLOR,
                )
            )
        return Group(*lines)


def _primary_billing_summary(usage: _RecordedUsage) -> str:
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
            f"API billed ${api_amount:{SESSION_COST_FORMAT}} · "
            f"{_session_count(usage.api_billed_cost_sessions)}"
        )
    if usage.api_cost_unreported_sessions:
        parts.append(f"{_count_label(usage.api_cost_unreported_sessions, 'API charge')} unreported")
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
    """Render one width-wrapping host and Shikiru process-tree observation."""

    sample: _ResourceSample | None

    def render(self) -> Text:
        if self.sample is None:
            return Text("Host and Shikiru resources unavailable · no sample recorded")
        state = self.sample.state
        if state == RESOURCE_FAILED:
            return _resource_state_line(RESOURCE_FAILED, self.sample)
        if state == RESOURCE_UNAVAILABLE:
            return _resource_state_line(RESOURCE_UNAVAILABLE, self.sample)
        state_style = AMBER if state == RESOURCE_STALE else SECONDARY_TEXT_COLOR
        if state == RESOURCE_STALE:
            state_label = "Stale · "
        elif state == RESOURCE_AVAILABLE:
            state_label = "Available · "
        elif state is None:
            state_label = f"{RESOURCE_STATE_UNKNOWN} · "
        else:
            state_label = ""
        instance = _instance_resource_text(self.sample)
        host_ram = _host_ram_text(self.sample)
        age = (
            f" · observed {self.sample.sampled_ago} ago"
            if self.sample.sampled_ago and state != RESOURCE_AVAILABLE
            else ""
        )
        error = f" · {self.sample.error}" if self.sample.error else ""
        return Text.assemble(
            (state_label, state_style),
            ("Host ", "bold"),
            (f"CPU {self.sample.host_cpu_pct:.0f}%", STEEL_BLUE),
            f" · {host_ram}",
            " · ",
            ("Shikiru + agents ", "bold"),
            instance,
            (age, SECONDARY_TEXT_COLOR),
            (error, AMBER if state == RESOURCE_STALE else ALERT_COLOR),
        )


def _resource_state_line(state: str, sample: _ResourceSample) -> Text:
    error = f" · {sample.error}" if sample.error else ""
    age = f" · observed {sample.sampled_ago} ago" if sample.sampled_ago else ""
    style = ALERT_COLOR if state == RESOURCE_FAILED else SECONDARY_TEXT_COLOR
    return Text(f"Resources {state}{error}{age}", style=style)


def _instance_resource_text(sample: _ResourceSample) -> Text:
    if sample.instance_cpu_pct is None or sample.instance_rss_gb is None:
        if sample.state == RESOURCE_EMPTY:
            return Text("process tree empty", style=SECONDARY_TEXT_COLOR)
        return Text("CPU/RAM unavailable", style=SECONDARY_TEXT_COLOR)
    return Text.assemble(
        (f"CPU {sample.instance_cpu_pct:.0f}%", STEEL_BLUE),
        f" · RAM {sample.instance_rss_gb:{GIB_FORMAT}} GiB",
    )


def _host_ram_text(sample: _ResourceSample) -> str:
    used = sample.host_ram_used_bytes
    total = sample.host_ram_total_bytes
    if used is None or total is None:
        return "RAM used/total unavailable"
    return f"RAM {used / BYTES_PER_GIB:{GIB_FORMAT}}/{total / BYTES_PER_GIB:{GIB_FORMAT}} GiB"


def render_resource_metric(sample: _ResourceSample | None) -> Text:
    """Render `sample` without starting another resource observation."""
    return ResourceMetric(sample).render()


def attention_status_text(
    attention: _Attention,
    summary: _AttentionSummary,
    *,
    action_hint: str,
    selected_group_index: int = 0,
    selected_escalation_id: str | None = None,
) -> str:
    """Render the Home status strip from current attention and observed workspace state."""
    if attention.groups:
        return _attention_group_status(
            attention.groups[selected_group_index],
            summary,
            action_hint=action_hint,
            selected_escalation_id=selected_escalation_id,
        )
    if summary.operator_owned:
        owners = ", ".join(summary.operator_owned)
        status = f"@ Operator owns {owners} · automation held"
        return _append_operational_facts(status, summary, include_held_state=False)
    if summary.held_tasks:
        status = f"= Automation held · {summary.held_tasks} tasks"
        return _append_operational_facts(status, summary, include_held_state=False)
    if summary.engine_up is False:
        return _append_operational_facts("✓ No decisions needed", summary)
    if summary.engine_up is True and summary.held_tasks == 0 and summary.holds == ():
        return "✓ No decisions needed · Automation continues"
    return "✓ No decisions needed · Automation status unavailable"


def _attention_group_status(
    group: _AttentionGroup,
    summary: _AttentionSummary,
    *,
    action_hint: str,
    selected_escalation_id: str | None,
) -> str:
    if group.escalations:
        decision = next(
            (item for item in group.escalations if item.escalation_id == selected_escalation_id),
            group.escalations[0],
        )
        status = (
            f"! {decision.escalation_id} · {group.task_id or 'Project'} · "
            f"{decision.headline} · {action_hint}"
        )
    else:
        fault = next(
            (reason for reason in group.reasons if reason.startswith("engine fault")),
            None,
        )
        if fault is not None:
            status = f"! {group.task_id or 'Project'} · {fault} · Ctrl+P actions"
        else:
            status = f"! {group.task_id or 'Project'} · {' · '.join(group.reasons)} · {action_hint}"
    return _append_operational_facts(status, summary)


def _append_operational_facts(
    status: str,
    summary: _AttentionSummary,
    *,
    include_held_state: bool = True,
) -> str:
    facts: list[str] = []
    if include_held_state and (summary.operator_owned or summary.held_tasks):
        facts.append("automation held")
    if summary.holds:
        facts.append(f"holds {', '.join(summary.holds)}")
    elif summary.holds is None or summary.held_tasks:
        facts.append("hold state unavailable")
    if summary.engine_up is False:
        facts.append("○ Engine stopped")
    elif summary.engine_up is None:
        facts.append("engine status unavailable")
    return f"{status} · {' · '.join(facts)}" if facts else status


@dataclass(frozen=True)
class AgentUsageRow:
    """Render a compact role, lineage, model, token and charge row."""

    session: _TaskSession

    def render(self) -> Text:
        session = self.session
        role = ROLE_LABELS.get(session.role, session.role.title())
        identity = f"{session.agent}/{session.model}"
        lineage = "current" if session.current_lineage else "previous"
        tokens = _session_token_summary(session)
        cost, cost_style = _session_cost_text(session)
        billing = MODE_LABELS.get(session.billing_mode, MODE_LABELS[BILLING_MODE_UNKNOWN])
        return Text.assemble(
            (f"{role} · {lineage} · ", SECONDARY_TEXT_COLOR),
            (f"{identity}", "bold"),
            (f" · session {tokens}", SECONDARY_TEXT_COLOR),
            (f" · {billing} · session {cost}", cost_style),
        )


def _session_token_summary(session: _TaskSession) -> str:
    if session.usage_reported is False:
        return "tokens unknown"
    total = _compact_count(session.input_tokens + session.output_tokens)
    if session.usage_reported is True:
        return f"{total} input+output"
    if session.legacy_token_totals:
        return f"{total} input+output legacy"
    return "tokens unknown"


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
    """Render the actual workflow stage separately from observed signal activity."""

    task: _TaskDetail

    def render(self) -> Group:
        task = self.task
        stage = task.step.replace("_", " ").title()
        if stage == UNSTARTED_STEP.title() and task.status == Status.RUNNING.value:
            stage = SETUP_STEP.title()
        stage_line = Text.assemble(
            ("Stage · ", SECONDARY_TEXT_COLOR),
            (stage, STEEL_BLUE),
            f" · {task.status.title()}",
            f" · iteration {task.iteration}" if task.iteration > 0 else "",
        )
        active = next(
            (panel for panel in task.progress.panels if panel.state is PanelState.ACTIVE),
            None,
        )
        if task.signal_observed:
            spinner = (
                f" {active.spinner_frame}" if active is not None and active.spinner_frame else ""
            )
            activity = Text.assemble(
                ("Activity · ", SECONDARY_TEXT_COLOR),
                (f"signal observed{spinner}", STEEL_BLUE),
                f" · {task.acting_role.title()}",
            )
        elif any(panel.state is PanelState.STALLED for panel in task.progress.panels):
            activity = Text.assemble(("Activity · stalled", ALERT_COLOR))
        elif active is not None:
            activity = Text.assemble(
                ("Activity · no current signal", SECONDARY_TEXT_COLOR),
                f" · {task.acting_role.title()}",
            )
        else:
            activity = Text.assemble(("Activity · no live turn", SECONDARY_TEXT_COLOR))
        return Group(stage_line, activity)
