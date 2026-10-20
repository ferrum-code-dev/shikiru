from __future__ import annotations

from datetime import UTC, datetime, timedelta

from shikiru.agents.contracts import QuotaWindow
from shikiru.cli.render import status_renderable
from shikiru.domain.escalation import Escalation, EscalationStatus
from shikiru.domain.reports import ReportKind, Verdict
from shikiru.domain.steps import Step
from shikiru.domain.tasks import EscalationReason, Mode, Status
from shikiru.operator.queries import (
    EscalationView,
    ReportRow,
    RunDirectoryUsage,
    SlotView,
    StatusView,
    TaskDetail,
    TaskSummary,
    UsageView,
)
from tests.support.factories import make_task
from tests.support.render import RENDER_WIDTH, plain_text

OBSERVED_AT = datetime(2026, 1, 1, tzinfo=UTC)


def _summary(
    task_id: str,
    status: Status,
    *,
    priority: int = 0,
    wait_reason: str | None = None,
    slot: int | None = None,
) -> TaskSummary:
    return TaskSummary(
        id=task_id,
        title=task_id,
        status=status,
        step=None,
        priority=priority,
        wait_reason=wait_reason,
        holds=frozenset(),
        slot=slot,
        parent_id=None,
        summary=None,
    )


def test_project_status_and_task_status_keep_their_own_scope() -> None:
    active = _summary("T-active", Status.RUNNING, slot=0)
    queued = _summary("T-queued", Status.QUEUED, priority=5, wait_reason="dependency")
    detail = TaskDetail(
        task=make_task(
            id="T-detail",
            mode=Mode.IMPLEMENT,
            status=Status.RUNNING,
            step=Step.IMPLEMENT,
        ),
        reports=[
            ReportRow(
                turn="02-verify-i1",
                kind=ReportKind.VERDICT,
                data=Verdict(decision="accept", summary="Reviewed"),
            )
        ],
    )
    view = StatusView(
        project="demo",
        engine_up=True,
        slots=SlotView(active=1, maximum=2),
        active_tasks=[active],
        queued_tasks=[queued],
        escalations=[
            EscalationView(
                escalation=Escalation(
                    id="E-generated",
                    task_id=active.id,
                    step=None,
                    reason=EscalationReason.DECISION_REQUIRED,
                    headline=(
                        f"{active.id} · no step · {EscalationReason.DECISION_REQUIRED.value}: "
                        "The Leader needs an Operator decision to continue."
                    ),
                    packet_path="runs/T-active/escalations/E-generated.md",
                    options=(),
                    status=EscalationStatus.OPEN,
                    created_at=OBSERVED_AT,
                ),
                packet=None,
            ),
            EscalationView(
                escalation=Escalation(
                    id="E-custom",
                    task_id=queued.id,
                    step=Step.CONSULT,
                    reason=EscalationReason.DECISION_REQUIRED,
                    headline=(
                        "T-legacy · consult · decision_required: preserve "
                        "agent:model[:effort] literally"
                    ),
                    packet_path="runs/T-queued/escalations/E-custom.md",
                    options=(),
                    status=EscalationStatus.OPEN,
                    created_at=OBSERVED_AT,
                ),
                packet=None,
            ),
        ],
        usage=[
            UsageView(
                agent="codex",
                input_tokens=100,
                output_tokens=50,
                cache_read_tokens=0,
                cache_write_tokens=0,
                cost_usd=0.42,
                cost_basis="api_equivalent",
                run_count=1,
                usage_reported_runs=1,
                api_equivalent_cost_usd=0.42,
                api_equivalent_cost_runs=1,
            )
        ],
        faults=[],
        oldest_escalation_age_seconds=None,
        run_directories=RunDirectoryUsage(directories=3, size_bytes=5 * 1024 * 1024),
        quotas={
            "codex": [
                QuotaWindow(
                    name="5h",
                    used_pct=42.0,
                    resets_at=OBSERVED_AT + timedelta(hours=1),
                    observed_at=OBSERVED_AT,
                )
            ]
        },
        task=detail,
    )

    project_output = plain_text(
        status_renderable(
            view.model_copy(update={"task": None}),
            width=RENDER_WIDTH,
            uptime_seconds=None,
            quota_warn_pct=50,
            quota_alert_pct=90,
        )
    )
    task_output = plain_text(
        status_renderable(
            view,
            width=RENDER_WIDTH,
            uptime_seconds=None,
            quota_warn_pct=50,
            quota_alert_pct=90,
        )
    )

    assert "T-active" in project_output
    assert "T-queued" in project_output
    assert "dependency" in project_output
    assert (
        "E-generated · T-active · The Leader needs an Operator decision to continue."
        in project_output
    )
    custom_headline = (
        "E-custom · T-queued · T-legacy · consult · decision_required: preserve "
        "agent:model[:effort] literally"
    )
    assert custom_headline in project_output
    assert "5h" in project_output
    assert "5.0 MiB" in project_output
    assert all(fact in project_output for fact in ("100", "50", "unknown"))
    assert "T-detail · Add retry to parser" in task_output
    assert "accept" in task_output
    assert "Reviewed" in task_output
    assert "T-active" not in task_output
    assert "T-queued" not in task_output
    assert "codex" not in task_output
    assert "5h" not in task_output
