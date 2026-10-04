"""Compact shared readouts for usage, resources and task activity."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from rich.console import Group, RenderableType
from rich.text import Text

from shikiru.agents.contracts import (
    BILLING_MODE_API,
    BILLING_MODE_SUBSCRIPTION,
    COST_BASIS_API_EQUIVALENT,
    COST_BASIS_BILLED,
)
from shikiru.domain.tasks import Role
from shikiru.ui.strip import PanelState
from shikiru.ui.tokens import ALERT_COLOR, AMBER, SAGE, SECONDARY_TEXT_COLOR, STEEL_BLUE

GIB_FORMAT = ".1f"
SESSION_COST_FORMAT = ".2f"
RESOURCE_FAILED = "failed"
RESOURCE_UNAVAILABLE = "unavailable"
RESOURCE_STALE = "stale"
RESOURCE_EMPTY = "empty"
ROLE_LABELS = {
    Role.LEADER.value: "Leader",
    Role.IMPLEMENTER.value: "Implementer",
    Role.HELPER.value: "Helper",
}
MODE_LABELS = {
    BILLING_MODE_API: "API",
    BILLING_MODE_SUBSCRIPTION: "Subscription",
}


class _RecordedUsage(Protocol):
    @property
    def session_count(self) -> int: ...

    @property
    def input_tokens(self) -> int: ...

    @property
    def output_tokens(self) -> int: ...

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
    def instance_cpu_pct(self) -> float | None: ...

    @property
    def instance_rss_gb(self) -> float | None: ...

    @property
    def sampled_ago(self) -> str | None: ...

    @property
    def error(self) -> str | None: ...


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
    def status(self) -> str: ...

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


@dataclass(frozen=True)
class ProjectUsageMetric:
    """Render explicit token and monetary coverage for retained session lineages."""

    usage: _RecordedUsage
    scope: str

    def render(self) -> Group:
        token_line = Text.assemble(
            (f"{self.scope} · ", "bold"),
            ("tokens ", SECONDARY_TEXT_COLOR),
            f"input {self.usage.input_tokens:,} · output {self.usage.output_tokens:,}",
            (" · coverage ", SECONDARY_TEXT_COLOR),
            (
                (
                    f"{self.usage.reported_sessions} reported · "
                    f"{self.usage.legacy_token_sessions} legacy positive · "
                    f"{self.usage.unknown_token_sessions} unknown / "
                    f"{_session_count(self.usage.session_count)}"
                ),
                STEEL_BLUE,
            ),
        )
        billed = _billed_total(self.usage)
        api = _api_billed_total(self.usage)
        estimate = _api_equivalent_total(self.usage)
        subscriptions = _subscription_summary(self.usage)
        lines = [
            token_line,
            Text.assemble(
                ("Charges · ", SECONDARY_TEXT_COLOR),
                (billed, SAGE),
                (" · ", SECONDARY_TEXT_COLOR),
                (api, SAGE),
                (" · ", SECONDARY_TEXT_COLOR),
                (estimate, AMBER),
                (" · ", SECONDARY_TEXT_COLOR),
                (subscriptions, AMBER),
                (" · ", SECONDARY_TEXT_COLOR),
                (
                    (
                        f"billing mode unknown for "
                        f"{_session_count(self.usage.unknown_billing_sessions)}"
                    ),
                    SECONDARY_TEXT_COLOR,
                ),
            ),
        ]
        return Group(*lines)


def _billed_total(usage: _RecordedUsage) -> str:
    if usage.billed_cost_sessions == 0 or usage.billed_cost_usd is None:
        return "no billed amount reported"
    amount = f"${usage.billed_cost_usd:{SESSION_COST_FORMAT}}"
    return f"billed {amount} across {_session_count(usage.billed_cost_sessions)}"


def _api_billed_total(usage: _RecordedUsage) -> str:
    if usage.api_billed_cost_sessions == 0 or usage.api_billed_cost_usd is None:
        amount = "no API billed amount reported"
    else:
        amount = (
            f"API billed ${usage.api_billed_cost_usd:{SESSION_COST_FORMAT}} across "
            f"{_session_count(usage.api_billed_cost_sessions)}"
        )
    missing = usage.api_cost_unreported_sessions
    if missing:
        return f"{amount}; API charges unreported for {_session_count(missing)}"
    return amount


def _api_equivalent_total(usage: _RecordedUsage) -> str:
    if usage.api_equivalent_cost_sessions == 0 or usage.api_equivalent_cost_usd is None:
        return "no API-equivalent estimate reported"
    amount = f"${usage.api_equivalent_cost_usd:{SESSION_COST_FORMAT}}"
    return (
        f"API-equivalent estimate {amount} across "
        f"{_session_count(usage.api_equivalent_cost_sessions)}"
    )


def _session_count(count: int) -> str:
    noun = "session" if count == 1 else "sessions"
    return f"{count} {noun}"


def _subscription_summary(usage: _RecordedUsage) -> str:
    if usage.subscription_sessions == 0:
        return "no subscription sessions"
    agents = ", ".join(usage.subscription_agents) or "declared agents unavailable"
    return f"Subscription {_session_count(usage.subscription_sessions)} · agents {agents}"


@dataclass(frozen=True)
class ResourceMetric:
    """Render the latest host and Shikiru-including-agents resource observation."""

    sample: _ResourceSample | None

    def render(self) -> RenderableType:
        if self.sample is None:
            return Text("Host and Shikiru resources unavailable · no sample recorded")
        state = self.sample.state
        if state == RESOURCE_FAILED:
            detail = f" · {self.sample.error}" if self.sample.error else ""
            return Text(f"Host and Shikiru resources failed{detail}", style=ALERT_COLOR)
        if state == RESOURCE_UNAVAILABLE:
            detail = f" · {self.sample.error}" if self.sample.error else ""
            return Text(
                f"Host and Shikiru resources unavailable{detail}", style=SECONDARY_TEXT_COLOR
            )
        host = Text.assemble(
            ("Host · ", "bold"),
            (f"CPU {self.sample.host_cpu_pct:.0f}%", STEEL_BLUE),
            f" · RAM {self.sample.host_ram_used_pct:.0f}%",
            f" · free {self.sample.free_ram_gb:{GIB_FORMAT}} GiB",
        )
        if state == RESOURCE_STALE:
            host = Text.assemble(("Stale · ", AMBER), host)
        if self.sample.instance_cpu_pct is None or self.sample.instance_rss_gb is None:
            instance = Text.assemble(
                ("Shikiru including agents · ", "bold"),
                (
                    "process tree empty" if state == RESOURCE_EMPTY else "CPU/RAM unavailable",
                    SECONDARY_TEXT_COLOR,
                ),
            )
        else:
            instance = Text.assemble(
                ("Shikiru including agents · ", "bold"),
                (f"CPU {self.sample.instance_cpu_pct:.0f}%", STEEL_BLUE),
                f" · RAM {self.sample.instance_rss_gb:{GIB_FORMAT}} GiB",
            )
        if self.sample.sampled_ago is not None:
            instance.append(
                f" · observed {self.sample.sampled_ago} ago", style=SECONDARY_TEXT_COLOR
            )
        lines: list[RenderableType] = [host, instance]
        if self.sample.error is not None:
            lines.append(Text(f"Latest sample failed · {self.sample.error}", style=AMBER))
        return Group(*lines)


def render_resource_metric(sample: _ResourceSample | None) -> RenderableType:
    """Render `sample` without starting another resource observation."""
    return ResourceMetric(sample).render()


@dataclass(frozen=True)
class AgentUsageRow:
    """Render one role, model and session-wide usage record together."""

    session: _TaskSession

    def render(self) -> Text:
        session = self.session
        role = ROLE_LABELS.get(session.role, session.role.title())
        identity = f"{session.agent}/{session.model}"
        if session.effort:
            identity = f"{identity}/{session.effort}"
        lineage = "current" if session.current_lineage else "previous"
        status = session.status.replace("_", " ").title()
        tokens = _session_token_text(session)
        cost, cost_style = _session_cost_text(session)
        billing = MODE_LABELS.get(session.billing_mode, "Billing mode unknown")
        return Text.assemble(
            (f"{role} · {lineage} · {status} · ", SECONDARY_TEXT_COLOR),
            (f"{identity}", "bold"),
            (f" · session totals {tokens}", SECONDARY_TEXT_COLOR),
            (f" · {billing} · {cost}", cost_style),
        )


def _session_token_text(session: _TaskSession) -> str:
    if session.usage_reported is False:
        return "tokens unknown"
    values = (
        f"input {session.input_tokens:,} · output {session.output_tokens:,}"
        f" · cache read {session.cache_read_tokens:,} · cache write {session.cache_write_tokens:,}"
    )
    if session.usage_reported is True:
        return values
    if session.legacy_token_totals:
        return f"legacy recorded · {values}"
    return "legacy usage provenance unknown"


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
        label = f"API-equivalent estimate {amount}"
        style = AMBER
    else:
        label = f"amount {amount} · basis unknown"
        style = SECONDARY_TEXT_COLOR
    if session.legacy_cost_recorded:
        label = f"{label} · legacy recorded"
    return label, style


@dataclass(frozen=True)
class StageActivity:
    """Render the actual workflow stage separately from observed signal activity."""

    task: _TaskDetail

    def render(self) -> Group:
        task = self.task
        stage = task.step.replace("_", " ").title()
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
        if active is not None:
            spinner = f" {active.spinner_frame}" if active.spinner_frame else ""
            activity = Text.assemble(
                ("Activity · ", SECONDARY_TEXT_COLOR),
                (f"signal observed{spinner}", STEEL_BLUE),
                f" · {task.acting_role.title()}",
            )
        elif any(panel.state is PanelState.STALLED for panel in task.progress.panels):
            activity = Text.assemble(("Activity · stalled", ALERT_COLOR))
        else:
            activity = Text.assemble(("Activity · no live turn", SECONDARY_TEXT_COLOR))
        return Group(stage_line, activity)
