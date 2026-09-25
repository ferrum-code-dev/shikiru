from __future__ import annotations

from datetime import UTC, datetime, timedelta

from rich.console import Console

from shikiru.agents.contracts import QuotaWindow
from shikiru.cli.render import status_renderable
from shikiru.domain.steps import Step
from shikiru.domain.tasks import Mode, Status
from shikiru.operator.queries import (
    SlotView,
    StatusView,
    TaskDetail,
    TaskSummary,
    UsageView,
)
from tests.support.factories import make_settings, make_task

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


def test_human_status_separates_active_queue_usage_quotas_and_verdicts() -> None:
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
            {
                "turn": "02-verify-i1",
                "kind": "verdict",
                "data": {"decision": "accept", "summary": "Reviewed"},
            }
        ],
    )
    view = StatusView(
        project="demo",
        engine_up=True,
        slots=SlotView(active=1, maximum=2),
        active_tasks=[active],
        queued_tasks=[queued],
        escalations=[],
        usage=[
            UsageView(
                agent="codex",
                input_tokens=100,
                output_tokens=50,
                cache_read_tokens=0,
                cache_write_tokens=0,
                cost_usd=0.42,
                cost_basis="api_equivalent",
            )
        ],
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

    console = Console(record=True, no_color=True, width=120)
    console.print(status_renderable(view, settings=make_settings(), width=console.width))
    output = console.export_text()

    assert "T-active" in output
    assert "T-queued" in output
    assert "T-history" not in output
    assert "┏" in output
    assert "┗" in output
    assert "dependency" in output
    assert "5h" in output
    assert "API-equivalent" in output
    assert "accept" in output
    assert "Reviewed" in output
