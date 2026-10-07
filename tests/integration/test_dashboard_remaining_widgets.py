from __future__ import annotations

import asyncio
import json
import os
import shlex
import shutil
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

import psutil
import pytest
from rich.cells import cell_len
from textual.geometry import Region
from textual.widgets import DataTable, Label, ListItem, ListView, Static

from shikiru.agents.contracts import (
    BILLING_MODE_API,
    BILLING_MODE_SUBSCRIPTION,
    BILLING_MODE_UNKNOWN,
    BILLING_SOURCE_OPERATOR_CONFIG,
    BILLING_SOURCE_PROVIDER_REPORTED,
    BILLING_SOURCE_UNKNOWN,
    BillingDeclaration,
    QuotaWindow,
    Rung,
    Usage,
)
from shikiru.config.loader import load_settings
from shikiru.config.settings import RepoConfig
from shikiru.dashboard.activity import RoleViewTail
from shikiru.dashboard.app import DashboardApp
from shikiru.dashboard.contract import DiagnosticSampleState
from shikiru.dashboard.motion import DashboardMotion
from shikiru.dashboard.services import DashboardServices, GitRepoStatusService
from shikiru.dashboard.views import StateDashboardViews
from shikiru.domain.escalation import EscalationStatus, options_for
from shikiru.domain.events import EventKind
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import EscalationReason, Role, Status
from shikiru.operator.lifecycle import task_dashboard_command
from shikiru.operator.queries import OperatorQueries
from shikiru.paths import run_dir, state_db_path
from shikiru.plugins.widgets.dialogue import DialoguePanel, DialogueWidget
from shikiru.plugins.widgets.escalations import EscalationsPanel, EscalationsWidget
from shikiru.plugins.widgets.resources import ResourcesPanel, ResourcesWidget
from shikiru.plugins.widgets.usage import UsagePanel, UsageWidget
from shikiru.process import SubprocessRunner
from shikiru.store.database import Database
from shikiru.store.escalations import EscalationStore
from shikiru.store.events import EventLog
from shikiru.store.quotas import QuotaStore
from shikiru.store.reader import StateReader
from shikiru.store.runs import RunStore
from shikiru.store.tasks import TaskStore
from shikiru.telemetry.docker import (
    DOCKER_BINARY,
    DOCKER_FORMAT_FLAG,
    DOCKER_JSON_FORMAT,
    DOCKER_NO_STREAM_FLAG,
    DOCKER_STATS_SUBCOMMAND,
    DockerSampler,
)
from shikiru.telemetry.resources import ResourceSampler
from shikiru.tmux.client import (
    HOME_PANE_OPTION,
    HOME_PANE_OPTION_VALUE,
    LAYOUT_PANE_OPTION,
    SYSTEM_LAYOUT_PANE,
    TMUX_ENV_COMMAND,
    Tmux,
)
from shikiru.workspace.git import Git
from tests.support.clock import FakeClock
from tests.support.config import write_minimal_project
from tests.support.dashboard import WidgetHostApp, widget_context
from tests.support.factories import make_settings
from tests.support.git_repo import GIT_COMMAND_TIMEOUT
from tests.support.render import plain_text
from tests.support.store import seed_task
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.agents.contracts import BillingMode
    from shikiru.clock import Clock
    from shikiru.config.settings import Settings
    from shikiru.dashboard.contract import WidgetContext
    from shikiru.dashboard.views import DashboardSources
    from tests.support.store import Connection
    from tests.support.tmux import TmuxServer


START = datetime(2026, 1, 1, tzinfo=UTC)
BYTES_PER_GIB = 1024**3
OBSERVABLE_PANE_MEMORY_BYTES = 64 * 1024**2
MINIMUM_PANE_MEMORY_BYTES = 48 * 1024**2
CPU_WORK_ITERATIONS = 10_000_000
CPU_PERCENT_MINIMUM_TOLERANCE = 5.0
CPU_PERCENT_RELATIVE_TOLERANCE = 0.25
EXECUTABLE_MODE = 0o755
UNUSED_SESSION_TARGET = "=shikiru-task84-test"
RESOURCE_SAMPLE_FAILURE = "CPU unavailable"
COMPACT_READOUT_WIDTH = 72
RESOURCE_READOUT_WIDTH = 48
COMPACT_READOUT_HEIGHT = 20
RESOURCE_READOUT_HEIGHT = 8
USAGE_DETAIL_HEIGHT = 30
RESOURCE_PROJECT_SESSION = "task110-project"
RESOURCE_OTHER_SESSION = "task110-other"
RESOURCE_OTHER_PROJECT_DIRECTORY = "other-project"
RESOURCE_RENAMED_HOME_NAME = "renamed-home"
RESOURCE_OPERATOR_SYSTEM_WINDOW = "operator-system"
RESOURCE_SHELL_WINDOW = "project-shell"
RESOURCE_SHELL_LAYOUT_PANE = "shell"
RESOURCE_SYSTEM_SPLIT_SIZE_PCT = 40
RESOURCE_UNRELATED_ALLOCATION_MULTIPLIER = 4
RESOURCE_RSS_MATCH_TOLERANCE_BYTES = 16 * 1024**2
RESOURCE_TEST_RUNG = Rung("fake", "resource-test", None)


@dataclass(frozen=True)
class _ResourceTestPanes:
    role_pane_id: str
    task_window_id: str
    role_root: psutil.Process
    home_root: psutil.Process
    system_root: psutil.Process
    excluded_roots: tuple[psutil.Process, ...]


def _dashboard_services(
    project_dir: Path,
    clock: Clock,
    tmux: Tmux,
    state: StateReader,
) -> tuple[DashboardServices, Settings]:
    settings = load_settings(project_dir)
    runner = SubprocessRunner()
    services = DashboardServices(
        GitRepoStatusService(
            Git(runner, command_timeout=settings.orchestration.timeouts.git_command),
            settings.repo,
            project_dir,
            clock,
            cache_ttl_seconds=settings.dashboard.repo_status_cache_seconds,
        ),
        clock=clock,
        resources_refresh_seconds=settings.orchestration.resources.sample_seconds,
        engine_up=lambda: False,
        engine_identity=lambda: None,
        project_root=project_dir,
        state=state,
        tmux=tmux,
        session_target=UNUSED_SESSION_TARGET,
        resources=ResourceSampler(clock, settings.orchestration.resources),
        docker=DockerSampler(
            runner,
            clock,
            refresh_seconds=settings.dashboard.docker_refresh_seconds,
            timeout_seconds=settings.dashboard.docker_timeout_seconds,
        ),
    )
    return services, settings


def _dashboard_views(
    reader: StateReader,
    project_dir: Path,
    clock: Clock,
    settings: Settings,
    sources: DashboardSources,
) -> StateDashboardViews:
    queries = OperatorQueries(
        reader,
        project=settings.project.name,
        project_root=project_dir,
        engine_up=sources.engine_up,
        max_parallel=settings.orchestration.max_parallel,
        clock=clock,
    )
    return StateDashboardViews(
        reader,
        focus_task=None,
        settings=settings,
        sources=sources,
        queries=queries,
        clock=clock,
        motion=DashboardMotion(reader, project_dir, settings, RoleViewTail()),
    )


def _populate_usage_records(connection: Connection, clock: FakeClock) -> str:
    task = seed_task(connection, clock, title="Read provider usage")
    runs = RunStore(connection, clock)
    api_run = runs.start(task.id, Role.IMPLEMENTER, Rung("codex", "gpt-6-luna", "low"))
    runs.record_usage(api_run.id, Usage(100, 20, 10, 5, 0.42, "api_equivalent", None))
    billed_zero_run = runs.start(task.id, Role.LEADER, Rung("codex", "gpt-6-sol", "high"))
    runs.record_usage(billed_zero_run.id, Usage(0, 0, 0, 0, 0.0, "billed", None))
    runs.start(task.id, Role.LEADER, Rung("codex", "gpt-6-sol", "high"))
    unknown_cost_run = runs.start(task.id, Role.IMPLEMENTER, Rung("codex", "gpt-6-luna", "low"))
    runs.record_usage(unknown_cost_run.id, Usage(40, 10, 0, 0, None, "unknown", None))
    quotas = QuotaStore(connection)
    quotas.record(
        "codex",
        QuotaWindow("weekly", 50.0, START + timedelta(hours=3), START),
    )
    quotas.record(
        "quota-only",
        QuotaWindow("daily", 12.0, None, START),
    )
    other_task = seed_task(connection, clock, title="Other task")
    other_run = runs.start(other_task.id, Role.IMPLEMENTER, Rung("codex", "gpt-6-sol", "high"))
    runs.record_usage(other_run.id, Usage(900, 700, 0, 0, None, "unknown", None))
    zero_run = runs.start(task.id, Role.LEADER, Rung("zero-agent", "model", "low"))
    runs.record_usage(zero_run.id, Usage(0, 0, 0, 0, 0.0, "billed", None))
    runs.start(task.id, Role.LEADER, Rung("unknown-agent", "model", "low"))
    clock.advance(2 * 60 * 60)
    return task.id


@pytest.fixture
def populated_usage_environment(
    connection: Connection,
    tmp_path: Path,
) -> tuple[Path, FakeClock, str]:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task_id = _populate_usage_records(connection, clock)
    return project_dir, clock, task_id


async def _assert_compact_readouts(context: WidgetContext) -> None:
    usage_widget = cast("UsagePanel", UsageWidget().create(context))
    async with WidgetHostApp(usage_widget).run_test(
        size=(COMPACT_READOUT_WIDTH, COMPACT_READOUT_HEIGHT)
    ):
        usage_lines = _rendered_widget_lines(usage_widget)
        usage_text = "\n".join(usage_lines)
        assert "210 input+output" in usage_text
        assert "5 reported" in usage_text
        assert "1 legacy" in usage_text
        assert "1 unknown" in usage_text
        assert "$2.50" in usage_text
        assert "$0.00" in usage_text
        assert "API charges" in usage_text
        assert "unreported" in usage_text
        assert "API-equivalent estimate" in usage_text
        assert "$1.50" in usage_text
        assert "1 subscription agent" in usage_text
        assert "claude" in usage_text
        assert "5 billing modes unknown" in usage_text
        assert all(cell_len(line) <= COMPACT_READOUT_WIDTH for line in usage_lines)

    resource_widget = cast("ResourcesPanel", ResourcesWidget().create(context))
    async with WidgetHostApp(resource_widget).run_test(
        size=(RESOURCE_READOUT_WIDTH, RESOURCE_READOUT_HEIGHT)
    ):
        resource_lines = _rendered_widget_lines(resource_widget)
        text = "\n".join(resource_lines)
        assert "Host" in text
        assert "CPU" in text
        assert "Shikiru CPU/RAM unavailable" in text
        assert "Agents CPU/RAM unavailable" in text
        assert "RAM" in text
        assert all(cell_len(line) <= RESOURCE_READOUT_WIDTH for line in resource_lines)


def _rendered_widget_lines(widget: Static) -> tuple[str, ...]:
    lines = widget.render_lines(Region(0, 0, widget.region.width, widget.region.height))
    return tuple(line.text.rstrip() for line in lines if line.text.strip())


async def test_escalations_open_full_packet_and_route_resolution_action(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="Choose parser behavior")
    packet_path = project_dir / ".shikiru" / "escalations" / "E-1.md"
    packet_path.parent.mkdir(parents=True)
    packet = (
        "# E-1 · decision needed\n\n"
        "## Question\n\n"
        "Which parser should we use?\n\n"
        "### Trade-offs\n\n"
        "- Strict validation\n- Flexible recovery\n\n"
        "## Options\n\n"
        "- `answer` ★: Give the Leader guidance\n"
    )
    packet_path.write_text(packet)
    EscalationStore(connection, clock).open(
        task.id,
        escalation_id="E-1",
        step=None,
        reason=EscalationReason.DECISION_REQUIRED,
        headline="decision needed",
        packet_path=str(packet_path),
        options=options_for(EscalationReason.DECISION_REQUIRED),
    )
    empty_packet_path = project_dir / ".shikiru" / "escalations" / "E-2.md"
    empty_packet_path.write_text("# E-2 · retry\n\n## Question\n\n## Options\n\n- `retry` ★\n")
    EscalationStore(connection, clock).open(
        task.id,
        escalation_id="E-2",
        step=None,
        reason=EscalationReason.AGENT_ERROR,
        headline="retry",
        packet_path=str(empty_packet_path),
        options=options_for(EscalationReason.AGENT_ERROR),
    )
    actions: list[str] = []

    async def open_palette(action: str) -> None:
        actions.append(action)

    context = widget_context(reader, project_dir, clock, open_palette=open_palette)
    views = context.views.escalations()
    assert views[0].question == (
        "Which parser should we use?\n\n### Trade-offs\n\n- Strict validation\n- Flexible recovery"
    )
    assert views[1].question == ""

    widget = cast("EscalationsPanel", EscalationsWidget().create(context))
    async with WidgetHostApp(widget).run_test() as pilot:
        await pilot.pause()
        widget.focus()
        assert pilot.app.focused is widget
        await pilot.press("o")
        await pilot.pause()
        packet_view = cast("Static", pilot.app.screen.query_one("#escalation-packet"))
        packet_copy = cast("str", packet_view.content)
        assert "E-1 · T-1 · decision needed" in packet_copy
        assert "Evidence error:" in packet_copy
        assert f"Original packet:\n{packet}" in packet_copy
        await pilot.press("escape")
        await pilot.pause()
        widget.focus()
        await pilot.press("r")
        assert actions == ["resolve E-1"]


async def test_attention_keeps_unreadable_decision_and_does_not_create_its_packet(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="Choose retry behavior")
    TaskStore(connection, clock).apply_transition(
        task.id,
        Transition.stay(task, step=task.step, status=Status.ESCALATED),
    )
    EventLog(connection, clock).append(
        EventKind.ENGINE_FAULT,
        task_id=task.id,
        message="recorded engine failure",
        data={
            "scope": "engine tick",
            "error": "CommandFailed",
            "recoverable": True,
            "repeat": 1,
        },
    )
    EventLog(connection, clock).append(
        EventKind.ENGINE_RECOVERED,
        task_id=task.id,
        message="engine recovered",
        data={"scope": "engine tick"},
    )
    escalation = EscalationStore(connection, clock).open(
        task.id,
        step=task.step,
        reason=EscalationReason.DECISION_REQUIRED,
        headline="Choose whether to retry",
        packet_path=".shikiru/escalations/missing-packet.md",
        options=options_for(EscalationReason.DECISION_REQUIRED),
    )
    context = widget_context(reader, project_dir, clock)
    decision = context.views.attention().groups[0].escalations[0]
    assert context.views.attention().decisions == 1
    assert context.views.attention().faults == 0
    assert any(item.text == "engine recovered" for item in context.views.dialogue())
    assert decision.escalation_id == escalation.id
    assert decision.task_id == task.id
    assert decision.headline == "Choose whether to retry"
    assert decision.resolution_actions == ("answer", "cancel")
    assert decision.answer_available
    assert decision.evidence_error is not None
    assert "missing-packet.md" in decision.evidence_error

    panel = cast("EscalationsPanel", EscalationsWidget().create(context))
    async with WidgetHostApp(panel).run_test() as pilot:
        await wait_until(
            lambda: (
                escalation.id
                in plain_text(panel.query_one("#escalation-panels").query(Static)[0].content)
            ),
            timeout=2,
        )
        panel.focus()
        await pilot.press("o")
        await pilot.pause()
        evidence = cast("Static", pilot.app.screen.query_one("#escalation-packet"))
        evidence_text = plain_text(evidence.render())
        assert escalation.id in evidence_text
        assert "answer, cancel" in evidence_text
        assert "Evidence error" in evidence_text
        current = EscalationStore(connection, clock).get(escalation.id)
        assert current is not None
        assert current.status is EscalationStatus.OPEN
        assert not (project_dir / ".shikiru/escalations/missing-packet.md").exists()


async def test_attention_refreshes_after_another_client_resolves_without_stealing_focus(
    database: Database,
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="Choose retry behavior")
    TaskStore(connection, clock).apply_transition(
        task.id,
        Transition.stay(task, step=task.step, status=Status.ESCALATED),
    )
    escalation = EscalationStore(connection, clock).open(
        task.id,
        step=task.step,
        reason=EscalationReason.DECISION_REQUIRED,
        headline="Choose whether to retry",
        packet_path=".shikiru/escalations/missing-packet.md",
        options=options_for(EscalationReason.DECISION_REQUIRED),
    )
    context = widget_context(reader, project_dir, clock)
    panel = cast("EscalationsPanel", EscalationsWidget().create(context))
    second_client = database.connect()
    try:
        async with WidgetHostApp(panel).run_test() as pilot:
            await wait_until(
                lambda: (
                    escalation.id
                    in plain_text(panel.query_one("#escalation-panels").query(Static)[0].content)
                ),
                timeout=2,
            )
            assert context.views.workspace_summary().attention == 1
            panel.focus()
            active_screen = pilot.app.screen
            fault_data = {
                "scope": "engine tick",
                "error": "CommandFailed",
                "recoverable": True,
                "repeat": 1,
            }
            EventLog(second_client, clock).append(
                EventKind.ENGINE_FAULT,
                task_id=task.id,
                message="new engine fault",
                data=fault_data,
            )
            await wait_until(
                lambda: (
                    "engine fault"
                    in plain_text(panel.query_one("#escalation-panels").query(Static)[0].content)
                ),
                timeout=2,
            )
            assert pilot.app.focused is panel
            assert pilot.app.screen is active_screen
            assert f"▶ {task.id} · Choose retry behavior" in plain_text(
                panel.query_one("#escalation-panels").query(Static)[0].content
            )
            assert context.views.workspace_summary().attention == 1

            EscalationStore(second_client, clock).resolve(escalation.id, "cancel")
            await wait_until(
                lambda: (
                    escalation.id
                    not in plain_text(
                        panel.query_one("#escalation-panels").query(Static)[0].content
                    )
                ),
                timeout=2,
            )
            assert context.views.attention().decisions == 0
            assert pilot.app.focused is panel
            assert pilot.app.screen is active_screen
    finally:
        second_client.close()


async def test_dialogue_filters_noise_and_enter_shows_the_full_implementer_text(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="Summarize report")
    events = EventLog(connection, clock)
    events.append(EventKind.VERDICT, task_id=task.id, message="Leader verdict")
    for index in range(21):
        events.append(
            EventKind.HELPER_ACTION,
            task_id=task.id,
            message=f"helper-{index}",
        )
    for index in range(8):
        events.append(
            EventKind.TURN_COMPLETED,
            task_id=task.id,
            message=f"Leader turn {index}",
            data={"role": Role.LEADER.value, "summary": f"Leader turn {index}"},
        )
    events.append(
        EventKind.TURN_COMPLETED,
        task_id=task.id,
        message="report completed",
        data={
            "role": Role.IMPLEMENTER.value,
            "summary": "A concise report summary.",
            "question": "Does this need another test?\nThe question has a second line.",
        },
    )
    events.append(EventKind.VERDICT, task_id=task.id, message="Leader approves")
    context = widget_context(reader, project_dir, clock)
    bubbles = context.views.dialogue()
    assert len(bubbles) == 20
    assert bubbles[0].text == "helper-3"
    assert bubbles[-2].text == (
        "A concise report summary.\nDoes this need another test?\nThe question has a second line."
    )
    assert bubbles[-1].text == "Leader approves"
    assert sum(bubble.text == "Leader approves" for bubble in bubbles) == 1

    widget = cast("DialoguePanel", DialogueWidget().create(context))
    async with WidgetHostApp(widget).run_test() as pilot:
        await pilot.pause()
        table = cast("DataTable[object]", widget.query_one("#dialogue-table", DataTable))
        table.move_cursor(row=table.row_count - 2)
        table.focus()
        await pilot.press("enter")
        await pilot.pause()
        full_text = cast("Static", pilot.app.screen.query_one("#dialogue-full-text"))
        assert cast("str", full_text.content) == bubbles[-2].text


async def test_usage_projection_keeps_project_and_task_scope_and_cost_evidence_separate(
    connection: Connection,
    reader: StateReader,
    populated_usage_environment: tuple[Path, FakeClock, str],
) -> None:
    del connection
    project_dir, clock, task_id = populated_usage_environment
    context = widget_context(reader, project_dir, clock)
    usage = context.views.usage()
    codex_usage = next(item for item in usage if item.agent == "codex")
    assert codex_usage.cost_usd is None
    assert codex_usage.cost_basis == "unknown"
    assert codex_usage.run_count == 5
    assert codex_usage.usage_reported_runs == 4
    assert codex_usage.input_tokens == 1040
    assert codex_usage.billed_cost_usd == 0.0
    assert codex_usage.billed_cost_runs == 1
    assert codex_usage.api_equivalent_cost_usd == 0.42
    assert codex_usage.api_equivalent_cost_runs == 1
    assert codex_usage.unknown_cost_runs == 3
    task_usage = context.views.usage(task_id)
    task_codex_usage = next(item for item in task_usage if item.agent == "codex")
    assert task_codex_usage.run_count == 4
    assert task_codex_usage.input_tokens == 140
    quota_only_usage = next(item for item in usage if item.agent == "quota-only")
    assert quota_only_usage.run_count == 0
    assert quota_only_usage.usage_reported_runs == 0


async def test_project_usage_widget_keeps_detailed_monetary_and_token_evidence(
    connection: Connection,
    reader: StateReader,
    populated_usage_environment: tuple[Path, FakeClock, str],
) -> None:
    del connection
    project_dir, clock, _ = populated_usage_environment
    context = widget_context(reader, project_dir, clock)
    widget = cast("UsagePanel", UsageWidget().create(context))
    async with WidgetHostApp(widget).run_test(
        size=(COMPACT_READOUT_WIDTH, USAGE_DETAIL_HEIGHT)
    ) as pilot:
        await pilot.pause()
        text = "\n".join(_rendered_widget_lines(widget))
        assert "codex" in text
        assert "Project" in text
        assert "$0.42" in text
        assert "API-equivalent estimate" in text
        assert "$0.00" in text
        assert "Billed" in text
        assert "estimate" in text
        assert "Cost unknown" in text
        assert "7 billing modes unknown" in text
        assert "input 1,040" in text
        assert "weekly" in text
        assert "reset " in text
        assert "observed 2h" in text
        assert "quota-only" in text
        assert "zero-agent · input 0 · output 0" in text
        codex_detail = text.split("codex ·", maxsplit=1)[1].split("quota-only", maxsplit=1)[0]
        assert "Billed $0.00" in codex_detail
        assert "API-equivalent estimate $0.42" in codex_detail
        assert "Cost unknown" in codex_detail
        unknown_agent = text.split("unknown-agent", maxsplit=1)[1].split("zero-agent", maxsplit=1)[
            0
        ]
        assert "tokens unknown" in unknown_agent
        assert "input 0" not in unknown_agent


async def test_task_usage_widget_shows_aggregate_charge_and_unknown_token_state(
    connection: Connection,
    reader: StateReader,
    populated_usage_environment: tuple[Path, FakeClock, str],
) -> None:
    project_dir, clock, task_id = populated_usage_environment
    task_context = widget_context(reader, project_dir, clock, focus_task=task_id)
    task_widget = cast("UsagePanel", UsageWidget().create(task_context))
    async with WidgetHostApp(task_widget).run_test(
        size=(COMPACT_READOUT_WIDTH, USAGE_DETAIL_HEIGHT)
    ):
        task_text = "\n".join(_rendered_widget_lines(task_widget))
        assert f"Task {task_id}" in task_text
        assert "$0.42" in task_text
        assert "API-equivalent estimate" in task_text
        assert "unknown" in task_text
        assert "input 1,040" not in task_text

    unknown_task = seed_task(connection, clock, title="No reported tokens")
    RunStore(connection, clock).start(
        unknown_task.id, Role.IMPLEMENTER, Rung("codex", "model", "low")
    )
    unknown_context = widget_context(reader, project_dir, clock, focus_task=unknown_task.id)
    unknown_widget = cast("UsagePanel", UsageWidget().create(unknown_context))
    async with WidgetHostApp(unknown_widget).run_test(
        size=(COMPACT_READOUT_WIDTH, USAGE_DETAIL_HEIGHT)
    ):
        unknown_text = "\n".join(_rendered_widget_lines(unknown_widget))
        assert f"Task {unknown_task.id}" in unknown_text
        assert "unknown" in unknown_text
        assert "input 0" not in unknown_text


async def test_project_usage_projects_persisted_coverage_without_inventing_api_spend(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="Retained mixed billing history")
    runs = RunStore(connection, clock)

    def declaration(mode: BillingMode) -> BillingDeclaration:
        source = (
            BILLING_SOURCE_OPERATOR_CONFIG
            if mode != BILLING_MODE_UNKNOWN
            else BILLING_SOURCE_UNKNOWN
        )
        return BillingDeclaration(mode=mode, source=source)

    api_zero = runs.start(
        task.id,
        Role.IMPLEMENTER,
        Rung("codex", "gpt-6-luna", "low"),
        billing_declaration=declaration(BILLING_MODE_API),
    )
    runs.record_usage(api_zero.id, Usage(10, 0, 0, 0, 0.0, "billed", None))
    api_missing = runs.start(
        task.id,
        Role.LEADER,
        Rung("codex", "gpt-6-sol", "high"),
        billing_declaration=declaration(BILLING_MODE_API),
    )
    runs.record_usage(api_missing.id, Usage(20, 0, 0, 0, None, "unknown", None))
    subscription_charge = runs.start(
        task.id,
        Role.IMPLEMENTER,
        Rung("claude", "sonnet", "low"),
        billing_declaration=declaration(BILLING_MODE_SUBSCRIPTION),
    )
    runs.record_usage(subscription_charge.id, Usage(30, 0, 0, 0, 2.5, "billed", None))
    subscription_unknown = runs.start(
        task.id,
        Role.LEADER,
        Rung("claude", "opus", "high"),
        billing_declaration=declaration(BILLING_MODE_SUBSCRIPTION),
    )
    runs.record_usage(subscription_unknown.id, Usage(40, 0, 0, 0, None, "unknown", None))
    unknown_equivalent = runs.start(
        task.id,
        Role.IMPLEMENTER,
        Rung("unknown-agent", "model", "low"),
    )
    runs.record_usage(unknown_equivalent.id, Usage(50, 0, 0, 0, 1.5, "api_equivalent", None))
    operator_unknown = runs.start(
        task.id,
        Role.LEADER,
        Rung("codex", "model", "low"),
        billing_declaration=BillingDeclaration(
            BILLING_MODE_UNKNOWN,
            BILLING_SOURCE_OPERATOR_CONFIG,
        ),
    )
    runs.start(
        task.id,
        Role.IMPLEMENTER,
        Rung("provider-agent", "model", "low"),
        billing_declaration=BillingDeclaration(
            BILLING_MODE_UNKNOWN,
            BILLING_SOURCE_PROVIDER_REPORTED,
        ),
    )
    legacy = runs.start(task.id, Role.LEADER, Rung("legacy-agent", "model", "low"))
    runs.record_usage(legacy.id, Usage(60, 0, 0, 0, None, "unknown", None))
    connection.execute("UPDATE runs SET usage_reported = NULL WHERE id = ?", (legacy.id,))
    runs.start(task.id, Role.IMPLEMENTER, Rung("no-report-agent", "model", "low"))
    for run in runs.for_task(task.id):
        runs.close(run.id)
    TaskStore(connection, clock).apply_transition(
        task.id,
        Transition.stay(task, step=None, status=Status.DONE),
    )
    disposable_run_data = run_dir(project_dir, task.id)
    disposable_run_data.mkdir(parents=True)
    (disposable_run_data / "temporary-evidence").write_text("removed by ordinary cleanup")
    shutil.rmtree(disposable_run_data)

    context = widget_context(reader, project_dir, clock)
    projection = context.views.project_usage()

    assert projection.session_count == 9
    assert projection.input_tokens == 210
    assert projection.reported_sessions == 5
    assert projection.legacy_token_sessions == 1
    assert projection.unknown_token_sessions == 3
    assert projection.billed_cost_usd == 2.5
    assert projection.billed_cost_sessions == 2
    assert projection.api_billed_cost_usd == 0.0
    assert projection.api_billed_cost_sessions == 1
    assert projection.api_cost_unreported_sessions == 1
    assert projection.api_equivalent_cost_usd == 1.5
    assert projection.api_equivalent_cost_sessions == 1
    assert projection.unknown_billing_sessions == 5
    assert projection.unknown_billing_operator_config_sessions == 1
    assert projection.unknown_billing_provider_reported_sessions == 1
    assert projection.unknown_billing_source_unknown_sessions == 3
    assert projection.subscription_sessions == 2
    assert projection.subscription_agents == ("claude",)

    await _assert_unknown_billing_source_views(context, task.id, operator_unknown.id)
    await _assert_compact_readouts(context)


async def _assert_unknown_billing_source_views(
    context: WidgetContext,
    task_id: str,
    operator_unknown_run_id: int,
) -> None:
    task_detail = await context.views.task_detail(task_id, include_timeline=False)
    assert task_detail is not None
    assert task_detail.usage is not None
    operator_unknown_session = next(
        session for session in task_detail.sessions if session.run_id == operator_unknown_run_id
    )
    assert operator_unknown_session.billing_mode == BILLING_MODE_UNKNOWN
    assert operator_unknown_session.billing_source == BILLING_SOURCE_OPERATOR_CONFIG
    assert task_detail.usage.unknown_billing_operator_config_sessions == 1
    assert task_detail.usage.unknown_billing_provider_reported_sessions == 1
    assert task_detail.usage.unknown_billing_source_unknown_sessions == 3

    widget = cast("UsagePanel", UsageWidget().create(context))
    async with WidgetHostApp(widget).run_test(size=(COMPACT_READOUT_WIDTH, USAGE_DETAIL_HEIGHT)):
        text = " ".join(_rendered_widget_lines(widget))
        assert "Billing mode unknown · 5 sessions" in text
        assert "operator_config" in text
        assert "provider_reported" in text
        assert "unknown source" in text
        assert "API charges unreported · 1 session" in text


async def test_resource_sampling_failure_is_stale_and_task_navigation_still_works(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
    tmux_server: TmuxServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="Readable task")
    tmux = Tmux(SubprocessRunner(), socket=tmux_server.socket_name, env=tmux_server.env)
    sources, settings = _dashboard_services(project_dir, clock, tmux, reader)
    initial = await sources.resources()
    assert initial.state is DiagnosticSampleState.AVAILABLE

    clock.advance(settings.orchestration.resources.sample_seconds)

    def fail_cpu_sample(*, interval: float | None = None, percpu: bool = False) -> float:
        del interval, percpu
        raise OSError(RESOURCE_SAMPLE_FAILURE)

    monkeypatch.setattr(psutil, "cpu_percent", fail_cpu_sample)
    views = _dashboard_views(reader, project_dir, clock, settings, sources)

    async def open_palette(action: str) -> None:
        del action

    app = DashboardApp(
        focus_task=None,
        views=views,
        on_change=reader.watch,
        clock=clock,
        motion_interval_seconds=None,
        resources_refresh_seconds=settings.orchestration.resources.sample_seconds,
        docker_refresh_seconds=settings.dashboard.docker_refresh_seconds,
        open_palette=open_palette,
        widget_names=["queue", "resources"],
    )
    async with app.run_test(size=(100, 50)) as pilot:
        resource_panel = app.query_one("#resources", Static)
        await wait_until(lambda: "Stale" in plain_text(resource_panel.content), timeout=2)
        await pilot.pause()
        resource_lines = _rendered_widget_lines(resource_panel)
        resource_text = "\n".join(resource_lines)
        assert "Stale" in resource_text
        assert "Host" in resource_text
        assert "Shikiru CPU" in resource_text
        assert "Agents process tree empty" in resource_text
        assert "CPU" in resource_text
        assert "RAM" in resource_text
        assert "observed" in resource_text
        assert all(cell_len(line) <= resource_panel.size.width for line in resource_lines)
        task_row = app.query_one(f"#task-row-{task.id} > Static", Static)
        assert task.title in plain_text(task_row.content)

        home_screen = app.screen
        task_list = app.query_one("#task-browser-list", ListView)
        task_list.focus()
        task_list.index = 0
        await pilot.press("enter")
        identity = app.query_one("#task-detail-identity", Static)
        await wait_until(lambda: task.id in plain_text(identity.content), timeout=2)
        assert app.screen is home_screen
        assert task.id in plain_text(identity.content)


async def test_process_sampling_failure_retains_the_last_complete_tree_sample(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
    tmux_server: TmuxServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del connection
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    tmux = Tmux(SubprocessRunner(), socket=tmux_server.socket_name, env=tmux_server.env)
    sources, settings = _dashboard_services(project_dir, clock, tmux, reader)
    try:
        initial = await sources.resources()
    except psutil.AccessDenied, PermissionError:
        pytest.skip("host process enumeration is restricted")

    assert initial.instance_cpu_pct is not None
    assert initial.instance_rss_gb is not None
    assert initial.sampled_at == START

    clock.advance(settings.orchestration.resources.sample_seconds)
    children = psutil.Process.children

    def deny_current_process_children(
        process: psutil.Process,
        *,
        recursive: bool = False,
    ) -> list[psutil.Process]:
        if process.pid == os.getpid():
            raise psutil.AccessDenied(pid=process.pid)
        return children(process, recursive=recursive)

    monkeypatch.setattr(psutil.Process, "children", deny_current_process_children)
    stale = await sources.resources()

    assert stale.state is DiagnosticSampleState.STALE
    assert stale.sampled_at == initial.sampled_at
    assert stale.sampled_ago is not None
    assert stale.instance_cpu_pct == initial.instance_cpu_pct
    assert stale.instance_rss_gb == initial.instance_rss_gb
    assert stale.error is not None
    retained = sources.resource_sample()
    assert retained is not None
    assert retained.state is DiagnosticSampleState.STALE
    assert retained.sampled_at == initial.sampled_at
    assert retained.instance_cpu_pct == initial.instance_cpu_pct
    assert retained.error == stale.error


async def test_resource_failure_without_a_prior_sample_remains_failed(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
    tmux_server: TmuxServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del connection
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    tmux = Tmux(SubprocessRunner(), socket=tmux_server.socket_name, env=tmux_server.env)
    sources, _settings = _dashboard_services(project_dir, clock, tmux, reader)

    def fail_cpu_sample(*, interval: float | None = None, percpu: bool = False) -> float:
        del interval, percpu
        raise psutil.Error

    monkeypatch.setattr(psutil, "cpu_percent", fail_cpu_sample)

    failure = await sources.resources()
    retained = sources.resource_sample()

    assert failure.state is DiagnosticSampleState.FAILED
    assert failure.sampled_at is None
    assert failure.error is not None
    assert retained is not None
    assert retained.state is DiagnosticSampleState.FAILED
    assert retained.sampled_at is None
    assert retained.error == failure.error


async def test_docker_unavailable_and_failed_samples_remain_diagnostic_and_navigable(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
    tmux_server: TmuxServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="Queue remains usable")
    tmux = Tmux(SubprocessRunner(), socket=tmux_server.socket_name, env=tmux_server.env)
    sources, settings = _dashboard_services(project_dir, clock, tmux, reader)
    monkeypatch.setenv("PATH", str(tmp_path))

    unavailable = await sources.containers()

    assert unavailable.state is DiagnosticSampleState.UNAVAILABLE
    assert unavailable.sampled_ago is None

    docker = tmp_path / DOCKER_BINARY
    docker.write_text("#!/bin/sh\nprintf 'not-json\\n'\n", encoding="utf-8")
    docker.chmod(EXECUTABLE_MODE)
    clock.advance(settings.dashboard.docker_refresh_seconds)
    views = _dashboard_views(reader, project_dir, clock, settings, sources)

    async def open_palette(action: str) -> None:
        del action

    app = DashboardApp(
        focus_task=None,
        views=views,
        on_change=reader.watch,
        clock=clock,
        motion_interval_seconds=None,
        resources_refresh_seconds=settings.orchestration.resources.sample_seconds,
        docker_refresh_seconds=settings.dashboard.docker_refresh_seconds,
        open_palette=open_palette,
        widget_names=["queue", "docker"],
    )
    async with app.run_test(size=(100, 50)) as pilot:
        home_screen = app.screen
        await pilot.press("v")
        await wait_until(lambda: bool(app.screen.query("#focus-views-list")), timeout=2)
        view_list = app.screen.query_one("#focus-views-list", ListView)
        choices = [item for item in view_list.children if isinstance(item, ListItem)]
        docker_index = next(
            index
            for index, item in enumerate(choices)
            if plain_text(item.query_one(Label).render()) == "Diagnostics · Docker"
        )
        view_list.focus()
        view_list.index = docker_index
        await pilot.press("enter")
        await wait_until(lambda: app.screen is not home_screen, timeout=2)
        await wait_until(lambda: bool(app.screen.query("#docker")), timeout=2)
        docker_panel = app.screen.query_one("#docker", Static)
        await wait_until(
            lambda: "failed" in plain_text(docker_panel.content),
            timeout=2,
        )
        assert "invalid JSON" in plain_text(docker_panel.content)
        task_row = app.query_one(f"#task-row-{task.id} > Static", Static)
        assert task.title in plain_text(task_row.content)

        await pilot.press("escape")
        await wait_until(lambda: app.screen is home_screen, timeout=2)
        task_list = app.query_one("#task-browser-list", ListView)
        task_list.focus()
        task_list.index = 0
        await pilot.press("enter")
        identity = app.query_one("#task-detail-identity", Static)
        await wait_until(lambda: task.id in plain_text(identity.content), timeout=2)
        assert app.screen is home_screen


async def _start_resource_test_panes(
    tmux: Tmux,
    project_dir: Path,
    other_project_dir: Path,
    task_id: str,
) -> _ResourceTestPanes:
    sample_command = (
        sys.executable,
        "-c",
        f"import os; allocation=bytearray({OBSERVABLE_PANE_MEMORY_BYTES}); os.read(0, 1)",
    )
    home_pane = await tmux.new_session(
        RESOURCE_PROJECT_SESSION,
        cwd=project_dir,
        env={},
        window_name="dashboard",
        size=(120, 40),
        command=sample_command,
    )
    await tmux.set_pane_option(home_pane, HOME_PANE_OPTION, HOME_PANE_OPTION_VALUE)
    await tmux.rename_window(home_pane, RESOURCE_RENAMED_HOME_NAME)
    session_target = f"={RESOURCE_PROJECT_SESSION}"
    role_pane = await tmux.new_window(session_target, name=task_id, command=sample_command)
    task_window_id = await tmux.display(role_pane, "#{window_id}")
    system_pane = await tmux.split_window(
        role_pane,
        axis="h",
        size_pct=RESOURCE_SYSTEM_SPLIT_SIZE_PCT,
        cwd=project_dir,
        detached=True,
    )
    system_command = task_dashboard_command(project_dir, task_id)
    await tmux.respawn_pane(system_pane, system_command, env={}, cwd=project_dir)
    await tmux.set_pane_option(system_pane, LAYOUT_PANE_OPTION, SYSTEM_LAYOUT_PANE)
    command_row = await tmux.display(system_pane, "#{pane_start_command}")
    assert tuple(shlex.split(command_row)) == (TMUX_ENV_COMMAND, *system_command)
    await wait_until(lambda: _pane_is_live(tmux, system_pane), timeout=5.0)
    excluded_panes: list[str] = []
    for name, marker in (
        ("dashboard", None),
        (RESOURCE_OPERATOR_SYSTEM_WINDOW, SYSTEM_LAYOUT_PANE),
        (RESOURCE_SHELL_WINDOW, RESOURCE_SHELL_LAYOUT_PANE),
    ):
        pane_id = await tmux.new_window(session_target, name=name, command=sample_command)
        if marker is not None:
            await tmux.set_pane_option(pane_id, LAYOUT_PANE_OPTION, marker)
        excluded_panes.append(pane_id)
    unrelated_memory_bytes = OBSERVABLE_PANE_MEMORY_BYTES * RESOURCE_UNRELATED_ALLOCATION_MULTIPLIER
    unrelated_pane = await tmux.new_session(
        RESOURCE_OTHER_SESSION,
        cwd=other_project_dir,
        env={},
        window_name="dashboard",
        size=(120, 40),
        command=(
            sys.executable,
            "-c",
            f"import os; allocation=bytearray({unrelated_memory_bytes}); os.read(0, 1)",
        ),
    )
    pane_ids = (role_pane, home_pane, system_pane, *excluded_panes, unrelated_pane)
    roots = tuple(
        psutil.Process(int(process_id))
        for process_id in await asyncio.gather(
            *(tmux.display(pane_id, "#{pane_pid}") for pane_id in pane_ids)
        )
    )
    return _ResourceTestPanes(
        role_pane,
        task_window_id,
        roots[0],
        roots[1],
        roots[2],
        roots[3:],
    )


def _resource_panes_ready(panes: _ResourceTestPanes) -> bool:
    roots = (panes.role_root, panes.home_root, *panes.excluded_roots)
    return all(
        max(process.memory_info().rss for process in [root, *root.children(recursive=True)])
        >= MINIMUM_PANE_MEMORY_BYTES
        for root in roots
    )


async def _pane_is_live(tmux: Tmux, pane_id: str) -> bool:
    return not await tmux.is_pane_dead(pane_id)


def _resource_tree_rss(root: psutil.Process) -> int:
    return sum(process.memory_info().rss for process in [root, *root.children(recursive=True)])


async def test_resources_service_excludes_panes_from_other_projects(
    request: pytest.FixtureRequest,
    tmux_server: TmuxServer,
    tmp_path: Path,
) -> None:
    tmux = Tmux(tmux_server.runner, socket=tmux_server.socket_name, env=tmux_server.env)
    project_dir = write_minimal_project(
        tmp_path,
        '\n[dashboard.layout]\nsplit = "h"\npanes = [ '
        f'{{ pane = "{SYSTEM_LAYOUT_PANE}", size = 30 }}, '
        f'{{ pane = "{Role.LEADER.value}" }}, '
        f'{{ pane = "{Role.IMPLEMENTER.value}" }}, '
        f'{{ pane = "{RESOURCE_SHELL_LAYOUT_PANE}", size = 20 }} ]\n',
    )
    other_project_dir = tmp_path / RESOURCE_OTHER_PROJECT_DIRECTORY
    other_project_dir.mkdir()
    other_project_dir = write_minimal_project(other_project_dir)
    clock = FakeClock(START)
    database = Database(state_db_path(project_dir))
    connection = database.connect()
    request.addfinalizer(connection.close)
    reader = StateReader(database, clock)
    request.addfinalizer(reader.close)
    settings = load_settings(project_dir)
    sampler = ResourceSampler(clock, settings.orchestration.resources)
    task = seed_task(connection, clock, title="Owned role pane")
    tasks = TaskStore(connection, clock)
    runs = RunStore(connection, clock)
    run = runs.start(task.id, Role.IMPLEMENTER, RESOURCE_TEST_RUNG)
    runs.mark_taken_over(run.id)
    panes = await _start_resource_test_panes(tmux, project_dir, other_project_dir, task.id)
    tasks.set_window(task.id, panes.task_window_id)
    tasks.set_panes(task.id, {Role.IMPLEMENTER: panes.role_pane_id})
    await wait_until(lambda: _resource_panes_ready(panes), timeout=5.0)
    assert any(
        item.id == task.id and item.window_id == panes.task_window_id
        for item in reader.list_tasks()
    )
    service = DashboardServices(
        GitRepoStatusService(
            Git(SubprocessRunner(), command_timeout=GIT_COMMAND_TIMEOUT),
            RepoConfig(base=None),
            project_dir,
            clock,
            cache_ttl_seconds=settings.dashboard.repo_status_cache_seconds,
        ),
        clock=clock,
        resources_refresh_seconds=settings.orchestration.resources.sample_seconds,
        engine_up=lambda: False,
        engine_identity=lambda: None,
        project_root=project_dir,
        state=reader,
        tmux=tmux,
        session_target=f"={RESOURCE_PROJECT_SESSION}",
        resources=sampler,
        docker=DockerSampler(
            SubprocessRunner(),
            clock,
            refresh_seconds=settings.dashboard.docker_refresh_seconds,
            timeout_seconds=settings.dashboard.docker_timeout_seconds,
        ),
    )

    try:
        snapshot = await service.resources()
    except psutil.AccessDenied, PermissionError:
        pytest.skip("host process enumeration is restricted")

    assert snapshot.shikiru_rss_gb is not None
    assert snapshot.instance_rss_gb is not None
    expected_shikiru_rss = (
        _resource_tree_rss(psutil.Process(os.getpid()))
        + _resource_tree_rss(panes.home_root)
        + _resource_tree_rss(panes.system_root)
    )
    measured_shikiru_rss = snapshot.shikiru_rss_gb * BYTES_PER_GIB
    assert abs(measured_shikiru_rss - expected_shikiru_rss) < RESOURCE_RSS_MATCH_TOLERANCE_BYTES
    assert snapshot.agents_rss_gb is not None
    expected_agents_rss = _resource_tree_rss(panes.role_root)
    measured_agents_rss = snapshot.agents_rss_gb * BYTES_PER_GIB
    assert abs(measured_agents_rss - expected_agents_rss) < RESOURCE_RSS_MATCH_TOLERANCE_BYTES
    assert snapshot.agents_process_tree_empty is False
    assert snapshot.agents_cpu_pct is not None
    assert snapshot.instance_rss_gb == pytest.approx(
        snapshot.shikiru_rss_gb + snapshot.agents_rss_gb
    )


def test_resource_sampler_measures_a_real_process_tree() -> None:
    clock = FakeClock(START)
    settings = make_settings()
    sampler = ResourceSampler(clock, settings.orchestration.resources)

    try:
        snapshot = sampler.sample_process_trees(os.getpid(), ())
    except psutil.AccessDenied, PermissionError:
        pytest.skip("host process enumeration is restricted")
    assert snapshot.instance_cpu_pct is not None
    assert snapshot.instance_rss_gb is not None
    assert snapshot.instance_rss_gb > 0
    assert snapshot.shikiru_cpu_pct is None
    assert snapshot.shikiru_rss_gb is None
    assert snapshot.agents_cpu_pct is None
    assert snapshot.agents_rss_gb is None
    assert snapshot.shikiru_process_tree_empty is None
    assert snapshot.agents_process_tree_empty is None

    clock.advance(settings.orchestration.resources.sample_seconds)
    for _ in range(CPU_WORK_ITERATIONS):
        pass
    refreshed = sampler.sample_process_trees(os.getpid(), ())
    assert refreshed is not snapshot
    assert refreshed.instance_rss_gb is not None
    assert refreshed.instance_rss_gb > 0
    assert refreshed.instance_cpu_pct is not None
    assert refreshed.instance_cpu_pct > 0


@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker CLI is not installed")
async def test_docker_sampler_reads_a_real_docker_stats_sample() -> None:
    runner = SubprocessRunner()
    direct = await runner.run(
        [
            DOCKER_BINARY,
            DOCKER_STATS_SUBCOMMAND,
            DOCKER_NO_STREAM_FLAG,
            DOCKER_FORMAT_FLAG,
            DOCKER_JSON_FORMAT,
        ],
        timeout=make_settings().dashboard.docker_timeout_seconds,
        check=False,
    )
    if direct.returncode != 0:
        pytest.skip("Docker daemon is unavailable")
    raw = [json.loads(line) for line in direct.stdout.splitlines()]
    settings = make_settings()
    sampler = DockerSampler(
        runner,
        FakeClock(START),
        refresh_seconds=settings.dashboard.docker_refresh_seconds,
        timeout_seconds=settings.dashboard.docker_timeout_seconds,
    )

    snapshot = await sampler.sample()

    if not snapshot.available:
        pytest.skip("Docker daemon is unavailable")
    cached = await sampler.sample_if_due()
    assert cached is snapshot
    expected_by_name = {record["Name"]: record for record in raw}
    assert {container.name for container in snapshot.containers} == set(expected_by_name)
    for container in snapshot.containers:
        expected = expected_by_name[container.name]
        sampled_cpu = float(container.cpu_pct.removesuffix("%"))
        direct_cpu = float(expected["CPUPerc"].removesuffix("%"))
        tolerance = max(
            CPU_PERCENT_MINIMUM_TOLERANCE,
            direct_cpu * CPU_PERCENT_RELATIVE_TOLERANCE,
        )
        assert abs(sampled_cpu - direct_cpu) <= tolerance
        assert (
            container.memory.partition("/")[2].strip()
            == expected["MemUsage"].partition("/")[2].strip()
        )
