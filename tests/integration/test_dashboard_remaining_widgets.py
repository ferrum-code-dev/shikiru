from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

import psutil
import pytest
from textual.widgets import DataTable, ListView, Static

from shikiru.agents.contracts import QuotaWindow, Rung, Usage
from shikiru.config.loader import load_settings
from shikiru.config.settings import RepoConfig
from shikiru.dashboard.activity import RoleViewTail
from shikiru.dashboard.app import DashboardApp, TaskDetailScreen
from shikiru.dashboard.contract import DiagnosticSampleState
from shikiru.dashboard.motion import DashboardMotion
from shikiru.dashboard.services import DashboardServices, GitRepoStatusService
from shikiru.dashboard.views import StateDashboardViews
from shikiru.domain.escalation import EscalationStatus, options_for
from shikiru.domain.events import EventKind
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import EscalationReason, Role, Status
from shikiru.operator.queries import OperatorQueries
from shikiru.plugins.widgets.dialogue import DialoguePanel, DialogueWidget
from shikiru.plugins.widgets.escalations import EscalationsPanel, EscalationsWidget
from shikiru.plugins.widgets.usage import UNKNOWN_VALUE, UsagePanel, UsageWidget
from shikiru.process import SubprocessRunner
from shikiru.store.escalations import EscalationStore
from shikiru.store.events import EventLog
from shikiru.store.quotas import QuotaStore
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
from shikiru.tmux.client import Tmux
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

    from shikiru.clock import Clock
    from shikiru.config.settings import Settings
    from shikiru.dashboard.views import DashboardSources
    from shikiru.store.database import Database
    from shikiru.store.reader import StateReader
    from tests.support.store import Connection
    from tests.support.tmux import TmuxServer


START = datetime(2026, 1, 1, tzinfo=UTC)
BYTES_PER_GIB = 1024**3
OBSERVABLE_PANE_MEMORY_BYTES = 64 * 1024**2
MINIMUM_PANE_MEMORY_BYTES = 48 * 1024**2
RSS_COMPARISON_TOLERANCE_GB = 0.01
CPU_WORK_ITERATIONS = 10_000_000
CPU_PERCENT_MINIMUM_TOLERANCE = 5.0
CPU_PERCENT_RELATIVE_TOLERANCE = 0.25
EXECUTABLE_MODE = 0o755
UNUSED_SESSION_TARGET = "=shikiru-task84-test"


def _dashboard_services(
    project_dir: Path,
    clock: Clock,
    tmux: Tmux,
    *,
    resource_pid: int | None = None,
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
        engine_pid=lambda: resource_pid,
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


async def test_usage_panel_separates_cost_bases_and_marks_unknown_usage_and_costs(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task_id = _populate_usage_records(connection, clock)

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
    widget = cast("UsagePanel", UsageWidget().create(context))
    async with WidgetHostApp(widget).run_test() as pilot:
        await pilot.pause()
        text = plain_text(widget.content)
        assert "codex" in text
        assert "Project recorded usage" in text
        assert "API-equivalent subtotal · $0.42 · partial · 1/5 runs" in text
        assert "Billed subtotal · $0.00 · partial · 1/5 runs" in text
        assert "Unknown cost · 3/5 runs" in text
        assert "input 1,040" in text
        assert "weekly" in text
        assert "reset " in text
        assert "observed 2h" in text
        assert f"quota-only · tokens {UNKNOWN_VALUE} · no session usage recorded" in text
        assert "zero-agent · input 0 · output 0 · cache read 0 · cache write 0" in text
        assert f"unknown-agent · tokens {UNKNOWN_VALUE} · usage not reported (0/1 runs)" in text


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
    tmux = Tmux(SubprocessRunner(), socket=tmux_server.socket_name)
    sources, settings = _dashboard_services(project_dir, clock, tmux)
    initial = await sources.resources()
    assert initial.state is DiagnosticSampleState.AVAILABLE

    clock.advance(settings.orchestration.resources.sample_seconds)

    def fail_cpu_sample(*, interval: float | None = None, percpu: bool = False) -> float:
        del interval, percpu
        raise psutil.Error

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
        assert "Latest sample failed" in plain_text(resource_panel.content)
        task_row = app.query_one(f"#task-row-{task.id} > Static", Static)
        assert task.title in plain_text(task_row.content)

        task_list = app.query_one("#task-browser-list", ListView)
        task_list.focus()
        task_list.index = 0
        await pilot.press("enter")
        await wait_until(lambda: isinstance(app.screen, TaskDetailScreen), timeout=2)
        identity = app.screen.query_one("#task-detail-identity", Static)
        assert task.id in plain_text(identity.content)


async def test_process_sampling_failure_retains_the_last_complete_tree_sample(
    tmp_path: Path,
    tmux_server: TmuxServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    tmux = Tmux(SubprocessRunner(), socket=tmux_server.socket_name)
    sources, settings = _dashboard_services(
        project_dir,
        clock,
        tmux,
        resource_pid=os.getpid(),
    )
    try:
        initial = await sources.resources()
    except psutil.AccessDenied, PermissionError:
        pytest.skip("host process enumeration is restricted")

    assert initial.instance_cpu_pct is not None
    assert initial.instance_rss_gb is not None
    assert initial.sampled_at == START

    clock.advance(settings.orchestration.resources.sample_seconds)
    process_factory = psutil.Process

    def deny_current_process(pid: int) -> psutil.Process:
        if pid == os.getpid():
            raise psutil.AccessDenied(pid=pid)
        return process_factory(pid)

    monkeypatch.setattr(psutil, "Process", deny_current_process)
    stale = await sources.resources()

    assert stale.state is DiagnosticSampleState.STALE
    assert stale.sampled_at == initial.sampled_at
    assert stale.sampled_ago is not None
    assert stale.instance_cpu_pct == initial.instance_cpu_pct
    assert stale.instance_rss_gb == initial.instance_rss_gb
    assert stale.error is not None


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
    tmux = Tmux(SubprocessRunner(), socket=tmux_server.socket_name)
    sources, settings = _dashboard_services(project_dir, clock, tmux)
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
        docker_panel = app.query_one("#docker", Static)
        await wait_until(
            lambda: "failed" in plain_text(docker_panel.content),
            timeout=2,
        )
        assert "invalid JSON" in plain_text(docker_panel.content)
        task_row = app.query_one(f"#task-row-{task.id} > Static", Static)
        assert task.title in plain_text(task_row.content)

        task_list = app.query_one("#task-browser-list", ListView)
        task_list.focus()
        task_list.index = 0
        await pilot.press("enter")
        await wait_until(lambda: isinstance(app.screen, TaskDetailScreen), timeout=2)
        identity = app.screen.query_one("#task-detail-identity", Static)
        assert task.id in plain_text(identity.content)


async def test_resources_service_samples_every_pane_in_the_project_session(
    tmux_server: TmuxServer,
    tmp_path: Path,
) -> None:
    tmux = Tmux(tmux_server.runner, socket=tmux_server.socket_name)
    session_target = "=test"
    await tmux.new_window(
        session_target,
        name="task",
        command=(
            sys.executable,
            "-c",
            f"import os; allocation=bytearray({OBSERVABLE_PANE_MEMORY_BYTES}); os.read(0, 1)",
        ),
    )
    panes = await tmux.list_session_panes(session_target, "#{pane_id} #{pane_pid}")
    assert len(panes) == 2
    pane_pids = [int(line.split()[1]) for line in panes]
    task_process = psutil.Process(pane_pids[1])

    def task_pane_owns_memory() -> bool:
        processes = [task_process, *task_process.children(recursive=True)]
        return max(process.memory_info().rss for process in processes) >= MINIMUM_PANE_MEMORY_BYTES

    await wait_until(task_pane_owns_memory, timeout=5.0)
    task_tree_rss = sum(
        process.memory_info().rss
        for process in [task_process, *task_process.children(recursive=True)]
    )
    clock = FakeClock(START)
    settings = make_settings()
    sampler = ResourceSampler(clock, settings.orchestration.resources)
    service = DashboardServices(
        GitRepoStatusService(
            Git(SubprocessRunner(), command_timeout=GIT_COMMAND_TIMEOUT),
            RepoConfig(base=None),
            tmp_path,
            clock,
            cache_ttl_seconds=settings.dashboard.repo_status_cache_seconds,
        ),
        clock=clock,
        resources_refresh_seconds=settings.orchestration.resources.sample_seconds,
        engine_up=lambda: False,
        engine_pid=lambda: None,
        tmux=tmux,
        session_target=session_target,
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

    assert snapshot.instance_rss_gb is not None
    assert snapshot.instance_rss_gb * BYTES_PER_GIB >= task_tree_rss
    clock.advance(settings.orchestration.resources.sample_seconds)
    overlapping_sample = sampler.sample_process_trees(pane_pids[1], pane_pids)
    assert overlapping_sample.instance_rss_gb is not None
    assert (
        overlapping_sample.instance_rss_gb <= snapshot.instance_rss_gb + RSS_COMPARISON_TOLERANCE_GB
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
