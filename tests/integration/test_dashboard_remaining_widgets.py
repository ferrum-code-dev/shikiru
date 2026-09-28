from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

import psutil
import pytest
from rich.console import Console
from textual.app import App, ComposeResult
from textual.widgets import DataTable, Static

from shikiru.agents.contracts import QuotaWindow, Rung, Usage
from shikiru.config.loader import load_settings
from shikiru.config.settings import RepoConfig
from shikiru.dashboard.activity import RoleViewTail
from shikiru.dashboard.contract import (
    DockerView,
    OpenPalette,
    RepoStatus,
    ResourceView,
    WidgetContext,
)
from shikiru.dashboard.motion import DashboardMotion
from shikiru.dashboard.services import DashboardServices, GitRepoStatusService
from shikiru.dashboard.views import StateDashboardViews
from shikiru.domain.escalation import options_for
from shikiru.domain.events import EventKind
from shikiru.domain.tasks import EscalationReason, Role
from shikiru.operator.queries import OperatorQueries
from shikiru.plugins.widgets.dialogue import DialoguePanel, DialogueWidget
from shikiru.plugins.widgets.escalations import EscalationsPanel, EscalationsWidget
from shikiru.plugins.widgets.usage import UsagePanel, UsageWidget
from shikiru.process import SubprocessRunner
from shikiru.store.escalations import EscalationStore
from shikiru.store.events import EventLog
from shikiru.store.quotas import QuotaStore
from shikiru.store.runs import RunStore
from shikiru.telemetry.docker import (
    DOCKER_BINARY,
    DOCKER_COMMAND_TIMEOUT_SECONDS,
    DOCKER_FORMAT_FLAG,
    DOCKER_JSON_FORMAT,
    DOCKER_NO_STREAM_FLAG,
    DOCKER_STATS_SUBCOMMAND,
    DockerSampler,
)
from shikiru.telemetry.resources import RESOURCE_SAMPLE_SECONDS, ResourceSampler
from shikiru.tmux.client import Tmux
from shikiru.workspace.git import Git
from tests.support.clock import FakeClock
from tests.support.config import write_minimal_project
from tests.support.store import seed_task
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from textual.widget import Widget

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


class _StubServices:
    async def repo_status(self) -> RepoStatus:
        return RepoStatus(branch="main", ahead=0, behind=0, dirty=0)

    def engine_up(self) -> bool:
        return False

    async def resources(self) -> ResourceView:
        return ResourceView(10.0, 40.0, 4.0, 2.0, 0.2)

    async def containers(self) -> DockerView:
        return DockerView(available=False, containers=())


class _WidgetHostApp(App[None]):
    def __init__(self, widget: Widget) -> None:
        super().__init__()
        self._widget = widget

    def compose(self) -> ComposeResult:
        yield self._widget


def _context(
    reader: StateReader,
    project_dir: Path,
    clock: FakeClock,
    *,
    services: _StubServices | None = None,
    open_palette: OpenPalette | None = None,
) -> WidgetContext:
    dashboard_services = services or _StubServices()
    settings = load_settings(project_dir)
    return WidgetContext(
        views=StateDashboardViews(
            reader,
            focus_task=None,
            settings=settings,
            sources=dashboard_services,
            queries=OperatorQueries(
                reader,
                project=settings.project.name,
                project_root=project_dir,
                engine_up=dashboard_services.engine_up,
                max_parallel=settings.orchestration.max_parallel,
            ),
            clock=clock,
            motion=DashboardMotion(reader, project_dir, settings, RoleViewTail()),
        ),
        focus_task=None,
        on_change=reader.watch,
        on_motion=lambda _: None,
        open_palette=open_palette or _open_palette,
    )


async def _open_palette(action: str) -> None:
    del action


def _plain(content: object) -> str:
    console = Console(record=True, width=120)
    console.print(content)
    return console.export_text()


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

    context = _context(reader, project_dir, clock, open_palette=open_palette)
    views = context.views.escalations()
    assert views[0].question == (
        "Which parser should we use?\n\n### Trade-offs\n\n- Strict validation\n- Flexible recovery"
    )
    assert views[1].question == ""

    widget = cast("EscalationsPanel", EscalationsWidget().create(context))
    async with _WidgetHostApp(widget).run_test() as pilot:
        await pilot.pause()
        widget.focus()
        assert pilot.app.focused is widget
        await pilot.press("o")
        await pilot.pause()
        packet_view = cast("Static", pilot.app.screen.query_one("#escalation-packet"))
        assert cast("str", packet_view.content) == packet
        await pilot.press("escape")
        await pilot.pause()
        widget.focus()
        await pilot.press("r")
        assert actions == ["resolve E-1"]


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
    context = _context(reader, project_dir, clock)
    bubbles = context.views.dialogue()
    assert len(bubbles) == 20
    assert bubbles[0].text == "helper-3"
    assert bubbles[-2].text == (
        "A concise report summary.\nDoes this need another test?\nThe question has a second line."
    )
    assert bubbles[-1].text == "Leader approves"
    assert sum(bubble.text == "Leader approves" for bubble in bubbles) == 1

    widget = cast("DialoguePanel", DialogueWidget().create(context))
    async with _WidgetHostApp(widget).run_test() as pilot:
        await pilot.pause()
        table = cast("DataTable[object]", widget.query_one("#dialogue-table", DataTable))
        table.move_cursor(row=table.row_count - 2)
        table.focus()
        await pilot.press("enter")
        await pilot.pause()
        full_text = cast("Static", pilot.app.screen.query_one("#dialogue-full-text"))
        assert cast("str", full_text.content) == bubbles[-2].text


async def test_usage_panel_labels_api_equivalent_cost_and_hides_incomplete_totals(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="Read provider usage")
    runs = RunStore(connection, clock)
    api_run = runs.start(task.id, Role.IMPLEMENTER, Rung("codex", "gpt-6-luna", "low"))
    runs.record_usage(api_run.id, Usage(100, 20, 10, 5, 0.42, "api_equivalent", None))
    quota = QuotaWindow(
        name="weekly",
        used_pct=50.0,
        resets_at=START + timedelta(hours=1),
        observed_at=START,
    )
    QuotaStore(connection).record("codex", quota)
    claude_cost = runs.start(task.id, Role.IMPLEMENTER, Rung("claude", "haiku", None))
    unknown_cost = runs.start(task.id, Role.IMPLEMENTER, Rung("claude", "sonnet", None))
    runs.record_usage(claude_cost.id, Usage(80, 20, 0, 0, 0.10, "api_equivalent", None))
    runs.record_usage(unknown_cost.id, Usage(40, 10, 0, 0, None, "unknown", None))

    context = _context(reader, project_dir, clock)
    usage = context.views.usage()
    assert next(item for item in usage if item.agent == "claude").cost_usd is None
    widget = cast("UsagePanel", UsageWidget().create(context))
    async with _WidgetHostApp(widget).run_test() as pilot:
        await pilot.pause()
        text = _plain(widget.content)
        assert "codex" in text
        assert "$0.42 API-equivalent" in text
        assert "weekly" in text
        assert "claude" in text
        assert "$0.10" not in text


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
    sampler = ResourceSampler(clock)
    service = DashboardServices(
        GitRepoStatusService(Git(SubprocessRunner()), RepoConfig(base=None), tmp_path, clock),
        engine_up=lambda: False,
        engine_pid=lambda: None,
        tmux=tmux,
        session_target=session_target,
        resources=sampler,
        docker=DockerSampler(SubprocessRunner(), clock),
    )

    try:
        snapshot = await service.resources()
    except psutil.AccessDenied, PermissionError:
        pytest.skip("host process enumeration is restricted")

    assert snapshot.instance_rss_gb is not None
    assert snapshot.instance_rss_gb * BYTES_PER_GIB >= task_tree_rss
    clock.advance(RESOURCE_SAMPLE_SECONDS)
    overlapping_sample = sampler.sample_process_trees(pane_pids[1], pane_pids)
    assert overlapping_sample.instance_rss_gb is not None
    assert (
        overlapping_sample.instance_rss_gb <= snapshot.instance_rss_gb + RSS_COMPARISON_TOLERANCE_GB
    )


def test_resource_sampler_measures_a_real_process_tree() -> None:
    clock = FakeClock(START)
    sampler = ResourceSampler(clock)

    try:
        snapshot = sampler.sample_process_trees(os.getpid(), ())
    except psutil.AccessDenied, PermissionError:
        pytest.skip("host process enumeration is restricted")
    assert snapshot.instance_cpu_pct is not None
    assert snapshot.instance_rss_gb is not None
    assert snapshot.instance_rss_gb > 0

    clock.advance(RESOURCE_SAMPLE_SECONDS)
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
        timeout=DOCKER_COMMAND_TIMEOUT_SECONDS,
        check=False,
    )
    if direct.returncode != 0:
        pytest.skip("Docker daemon is unavailable")
    raw = [json.loads(line) for line in direct.stdout.splitlines()]
    sampler = DockerSampler(runner, FakeClock(START))

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
