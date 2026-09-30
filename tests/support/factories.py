"""Minimal `Task`/`Settings` factories shared by domain tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from shikiru.agents.contracts import Rung
from shikiru.config.settings import (
    AgentConfig,
    ComplexityLadder,
    ComplexityName,
    DashboardConfig,
    DashboardMotionConfig,
    DoctorConfig,
    FaultsConfig,
    ImplementerConfig,
    LayoutSplit,
    NotifyConfig,
    OrchestrationConfig,
    PaneLeaf,
    ProjectConfig,
    ResourcesConfig,
    RoleConfig,
    RolesConfig,
    SessionSettings,
    Settings,
    TimeoutsConfig,
    TitlesConfig,
)
from shikiru.domain.reports import (
    ConsultAnswer,
    EscalationRequest,
    ImplementationReport,
    Issue,
    NewTask,
    Option,
    PlanReport,
    TriageReport,
    Verdict,
)
from shikiru.domain.tasks import Mode, Status, Task, TaskOrigin

LEADER_RUNG = Rung(agent="claude", model="opus", effort="high")
LEADER_RUNG_2 = Rung(agent="codex", model="sol", effort="high")
IMPLEMENTER_RUNGS = [
    Rung(agent="codex", model="luna", effort="xhigh"),
    Rung(agent="codex", model="sol", effort="high"),
    Rung(agent="claude", model="opus", effort="high"),
]
HELPER_RUNG = Rung(agent="claude", model="haiku", effort="low")

DEFAULT_AGENTS = {"claude": AgentConfig(), "codex": AgentConfig()}

MAX_ITERATIONS = 5
CPU_UNCAPPED_PCT = 101
ESCALATE_AFTER = 2
PR_WAIT_SECONDS = 3600

TASK_CREATED_AT = datetime(2026, 1, 1, tzinfo=UTC)


def make_task(**overrides: Any) -> Task:
    fields: dict[str, Any] = {
        "id": "T-1",
        "seq": 1,
        "origin": TaskOrigin.OPERATOR,
        "title": "Add retry to parser",
        "request": "Add retry to parser",
        "status": Status.RUNNING,
        "created_at": TASK_CREATED_AT,
    }
    fields.update(overrides)
    return Task(**fields)


def make_settings(
    *,
    max_iterations: int = MAX_ITERATIONS,
    escalate_after: int = ESCALATE_AFTER,
    leader_ladder: list[Rung] | None = None,
    implementer_ladder: list[Rung] | None = None,
    implementer_complexity: dict[ComplexityName, ComplexityLadder] | None = None,
    helper_ladder: list[Rung] | None = None,
    agents: dict[str, AgentConfig] | None = None,
) -> Settings:
    return Settings(
        project=ProjectConfig(name="demo", trackers=[]),
        sessions=SessionSettings(ssh_timeout_seconds=30),
        agents={**DEFAULT_AGENTS, **(agents or {})},
        orchestration=OrchestrationConfig(
            max_parallel=2,
            max_iterations=max_iterations,
            integration="merge",
            poll_seconds=2,
            resources=ResourcesConfig(
                max_cpu_pct=85,
                min_free_ram_gb=4,
                sample_seconds=2,
                cpu_window_seconds=30,
            ),
            timeouts=TimeoutsConfig(
                stall=1200,
                tool=7200,
                max_quota_wait=28800,
                integration_wait=1800,
                pr_wait=PR_WAIT_SECONDS,
                helper=300,
            ),
            faults=FaultsConfig(
                backoff_initial_seconds=2,
                backoff_max_seconds=60,
                unexpected_fault_limit=10,
                stop_notify_seconds=30,
            ),
        ),
        roles=RolesConfig(
            leader=RoleConfig(ladder=leader_ladder or [LEADER_RUNG, LEADER_RUNG_2]),
            implementer=ImplementerConfig(
                ladder=implementer_ladder or list(IMPLEMENTER_RUNGS),
                escalate_after=escalate_after,
                complexity=implementer_complexity or {},
            ),
            helper=RoleConfig(ladder=helper_ladder or [HELPER_RUNG]),
        ),
        dashboard=DashboardConfig(
            widgets=["header"],
            layout=LayoutSplit(split="h", panes=[PaneLeaf(pane="system")]),
            titles=TitlesConfig(window="", leader="", implementer="", system="", shell=""),
            animations=True,
            docker_refresh_seconds=5,
            docker_timeout_seconds=5,
            repo_status_cache_seconds=10,
            motion=DashboardMotionConfig(
                refresh_hz=4,
                stream_active_seconds=10,
                stamp_flash_milliseconds=400,
                escalation_pulse_hz=1,
                task_event_limit=50,
                merge_event_limit=20,
            ),
        ),
        notify=NotifyConfig(command=""),
        doctor=DoctorConfig(probe_timeout=180, probe_stream_tail_lines=20),
    )


def make_escalation_request(**overrides: Any) -> EscalationRequest:
    fields: dict[str, Any] = {
        "question": "Which approach?",
        "options": [Option(label="proceed", consequences="ships now", recommended=True)],
    }
    fields.update(overrides)
    return EscalationRequest(**fields)


def make_triage_report(mode: Mode = Mode.IMPLEMENT, **overrides: Any) -> TriageReport:
    fields: dict[str, Any] = {
        "title": "Add retries",
        "mode": mode,
        "complexity": "normal",
        "acceptance_criteria": ["retries on failure"],
        "references": [],
    }
    fields.update(overrides)
    return TriageReport(**fields)


def make_plan_report(**overrides: Any) -> PlanReport:
    fields: dict[str, Any] = {"summary": "Planned", "commits": []}
    fields.update(overrides)
    return PlanReport(**fields)


def make_implementation_report(status: str = "completed", **overrides: Any) -> ImplementationReport:
    fields: dict[str, Any] = {
        "status": status,
        "summary": "Implemented",
        "commits": [],
        "tests": [],
        "trackers_updated": True,
        "question": "" if status == "completed" else "What should I do?",
    }
    fields.update(overrides)
    return ImplementationReport(**fields)


def make_consult_answer(decision: str = "answer", **overrides: Any) -> ConsultAnswer:
    fields: dict[str, Any] = {"decision": decision}
    if decision == "answer":
        fields["answer"] = "Use approach A"
    if decision == "escalate":
        fields["escalation"] = make_escalation_request()
    fields.update(overrides)
    return ConsultAnswer(**fields)


def make_verdict(decision: str = "accept", **overrides: Any) -> Verdict:
    fields: dict[str, Any] = {"decision": decision, "summary": "Reviewed"}
    if decision == "changes_requested":
        fields["issues"] = [Issue(severity="major", description="Missing tests")]
    if decision == "supersede":
        fields["new_tasks"] = [NewTask(title="Redo it", request="Redo it")]
    if decision == "escalate":
        fields["escalation"] = make_escalation_request()
    fields.update(overrides)
    return Verdict(**fields)
