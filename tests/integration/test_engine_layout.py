"""Layout restoration at the real task-window boundary."""

from __future__ import annotations

import socket
from typing import TYPE_CHECKING, Any

import pytest

from shikiru.agents.contracts import Rung
from shikiru.config.loader import validate_project_config_text
from shikiru.config.settings import AgentConfig, LayoutSplit, PaneLeaf, ProjectConfig
from shikiru.domain.commands import HandBack, Submit
from shikiru.domain.events import EventKind
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import Role, Status, Step, Task
from shikiru.engine.layout_guard import LayoutGuard
from shikiru.paths import task_root
from shikiru.process import ProcessResult, SubprocessRunner
from shikiru.store.commands import CommandQueue
from shikiru.store.events import EventLog
from shikiru.store.runs import RunStore
from shikiru.store.tasks import TaskStore
from shikiru.tmux.client import FORMAT_PANE_ID, Tmux, exact_target
from shikiru.tmux.decorator import PaneDecorator
from shikiru.tmux.layout import LayoutBuilder
from shikiru.tmux.session import SessionView, session_name
from tests.support.clock import FakeClock
from tests.support.config import MINIMAL_PROJECT_CONFIG
from tests.support.engine import build_fault_reporter, build_titles, build_tmux
from tests.support.factories import TASK_CREATED_AT, make_settings
from tests.support.tmux import attach_tmux_client
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from shikiru.clock import Clock
    from shikiru.config.settings import Settings
    from tests.support.store import Connection
    from tests.support.tmux import TmuxServer


_FAKE_RUNG = Rung(agent="fake", model="fake-1", effort=None)
_LAYOUT = LayoutSplit(
    split="h",
    panes=[
        PaneLeaf(pane="leader"),
        PaneLeaf(pane="implementer"),
        PaneLeaf(pane="system"),
        PaneLeaf(pane="shell"),
    ],
)
_PANE_SIZE_TOLERANCE_PCT = 3
_STACKED_LAYOUT = LayoutSplit(
    split="v",
    panes=[PaneLeaf(pane=name) for name in ("leader", "implementer", "system", "shell")],
)
_AGENT_TITLE = "{role} {{agent}}:{{model}} iter {{iteration}} ctx {{context}} {{flags_text}}"


def _settings(layout: LayoutSplit = _LAYOUT) -> Settings:
    settings = make_settings(
        leader_ladder=[_FAKE_RUNG],
        implementer_ladder=[_FAKE_RUNG],
        agents={"fake": AgentConfig()},
    )
    return settings.model_copy(
        update={
            "project": ProjectConfig(name="test", trackers=[]),
            "dashboard": settings.dashboard.model_copy(
                update={
                    "layout": layout,
                    "titles": settings.dashboard.titles.model_copy(
                        update={
                            "shell": "Project folder · {cwd}",
                            "leader": _AGENT_TITLE.format(role="Leader"),
                            "implementer": _AGENT_TITLE.format(role="Implementer"),
                        }
                    ),
                }
            ),
        }
    )


async def _task_window(
    connection: Connection,
    clock: Clock,
    tmux_server: TmuxServer,
    tmp_path: Path,
    *,
    layout_tree: LayoutSplit = _LAYOUT,
    tmux: Tmux | None = None,
) -> tuple[LayoutGuard, TaskStore, EventLog, str]:
    settings = _settings(layout_tree)
    tmux = tmux if tmux is not None else build_tmux(tmux_server)
    await SessionView(tmux).ensure("test", tmp_path, home_command=("/bin/sh",))
    tasks = TaskStore(connection, clock)
    events = EventLog(connection, clock)
    decorator = PaneDecorator(tmux)
    runs = RunStore(connection, clock)
    layout = LayoutGuard(
        tmux=tmux,
        view=SessionView(tmux),
        builder=LayoutBuilder(tmux),
        decorator=decorator,
        session_target=exact_target(session_name("test")),
        tasks=tasks,
        settings=settings,
        project_root=tmp_path,
        events=events,
        runs=runs,
        commands=CommandQueue(connection, clock),
        faults=build_fault_reporter(events, clock, settings),
        refresh_titles=build_titles(
            connection=connection,
            clock=clock,
            decorator=decorator,
            settings=settings,
            project_root=tmp_path,
        ).refresh,
    )
    queued = CommandQueue(connection, clock).append(
        Submit(title="restore panes", request="restore panes")
    )
    assert isinstance(queued.command, Submit)
    task = tasks.insert_from_submit(queued.command)
    restored = await layout.ensure(task)
    assert restored.window_id is not None
    return layout, tasks, events, restored.id


async def test_killed_agent_pane_rebuilds_the_task_window(
    connection: Connection, tmux_server: TmuxServer, tmp_path: Path
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    layout, tasks, events, task_id = await _task_window(connection, clock, tmux_server, tmp_path)
    task = tasks.get(task_id)
    assert task is not None
    assert task.window_id is not None
    old_window = task.window_id
    old_agent = task.panes[Role.LEADER]
    surviving_agent = task.panes[Role.IMPLEMENTER]
    tmux = build_tmux(tmux_server)
    await tmux_server.run("kill-pane", "-t", old_agent)

    restored = await layout.ensure(task)

    assert restored.window_id is not None
    assert restored.window_id == old_window
    assert restored.panes[Role.LEADER] != old_agent
    assert restored.panes[Role.IMPLEMENTER] == surviving_agent
    assert await tmux.target_exists(restored.window_id)
    pane_events = events.since(0, kinds=[EventKind.PANE_RESTORED], task_id=task_id)
    assert any(event.data.get("pane") == "agent" for event in pane_events)


async def test_default_vertical_stack_keeps_its_geometry_after_role_pane_recovery(
    connection: Connection, tmux_server: TmuxServer, tmp_path: Path
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    layout, tasks, _events, task_id = await _task_window(
        connection,
        clock,
        tmux_server,
        tmp_path,
        layout_tree=validate_project_config_text(MINIMAL_PROJECT_CONFIG).dashboard.layout,
    )
    task = tasks.get(task_id)
    assert task is not None
    assert task.window_id is not None
    tmux = build_tmux(tmux_server)
    leader = task.panes[Role.LEADER]
    implementer = task.panes[Role.IMPLEMENTER]
    shell = next(
        pane
        for pane in await tmux.list_panes(task.window_id, FORMAT_PANE_ID)
        if pane not in task.panes.values()
    )
    leader_pid = await tmux.display(leader, "#{pane_pid}")
    root = task_root(tmp_path, task_id)
    await _assert_vertical_shares(tmux, task.window_id, leader, implementer, shell)
    assert await tmux.display(shell, "#{pane_current_path}") == str(root)
    assert await tmux.display(shell, "#{pane_title}") == f"Project folder · {root}"

    await tmux_server.run("kill-pane", "-t", implementer)
    restored = await layout.ensure(task)
    await layout.ensure(restored)

    current = tasks.get(task_id)
    assert current is not None
    assert current.panes[Role.LEADER] == leader
    assert current.panes[Role.IMPLEMENTER] != implementer
    assert await tmux.display(leader, "#{pane_pid}") == leader_pid
    assert await tmux.display(shell, "#{pane_current_path}") == str(root)
    await _assert_vertical_shares(
        tmux,
        task.window_id,
        current.panes[Role.LEADER],
        current.panes[Role.IMPLEMENTER],
        shell,
    )


async def _assert_vertical_shares(
    tmux: Tmux, window_id: str, leader: str, implementer: str, shell: str
) -> None:
    widths = [
        int(await tmux.display(pane, "#{pane_width}")) for pane in (leader, implementer, shell)
    ]
    heights = [
        int(await tmux.display(pane, "#{pane_height}")) for pane in (leader, implementer, shell)
    ]
    total_height = sum(heights)
    window_width = int(await tmux.display(window_id, "#{window_width}"))

    assert widths == [window_width, window_width, window_width]
    assert heights[0] / total_height == pytest.approx(0.4, abs=_PANE_SIZE_TOLERANCE_PCT / 100)
    assert heights[1] / total_height == pytest.approx(0.4, abs=_PANE_SIZE_TOLERANCE_PCT / 100)
    assert heights[2] / total_height == pytest.approx(0.2, abs=_PANE_SIZE_TOLERANCE_PCT / 100)


async def test_dead_view_pane_is_respawned_in_place(
    connection: Connection, tmux_server: TmuxServer, tmp_path: Path
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    layout, tasks, events, task_id = await _task_window(connection, clock, tmux_server, tmp_path)
    task = tasks.get(task_id)
    assert task is not None
    view = task.panes[Role.IMPLEMENTER]
    tmux = build_tmux(tmux_server)
    await tmux.respawn_pane(view, ("/bin/sh", "-c", "exit"), env={}, cwd=tmp_path)

    async def dead() -> bool:
        return await tmux.is_pane_dead(view)

    await wait_until(dead, timeout=10)
    restored = await layout.ensure(task)

    assert restored.panes[Role.IMPLEMENTER] == view
    assert not await tmux.is_pane_dead(view)
    pane_events = events.since(0, kinds=[EventKind.PANE_RESTORED], task_id=task_id)
    assert [event.data.get("pane") for event in pane_events] == ["implementer"]


async def test_exited_taken_over_pane_queues_one_hand_back_and_stays_down(
    connection: Connection, tmux_server: TmuxServer, tmp_path: Path
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    layout, tasks, events, task_id = await _task_window(connection, clock, tmux_server, tmp_path)
    task = tasks.get(task_id)
    assert task is not None
    runs = RunStore(connection, clock)
    runs.mark_taken_over(runs.start(task_id, Role.IMPLEMENTER, _FAKE_RUNG).id)
    operator_pane = task.panes[Role.IMPLEMENTER]
    tmux = build_tmux(tmux_server)
    await tmux.respawn_pane(operator_pane, ("/bin/sh", "-c", "exit"), env={}, cwd=tmp_path)

    async def dead() -> bool:
        return await tmux.is_pane_dead(operator_pane)

    await wait_until(dead, timeout=10)
    await layout.ensure(task)
    await layout.ensure(task)

    pending = [queued.command for queued in CommandQueue(connection, clock).pending()]
    hand_backs = [command for command in pending if isinstance(command, HandBack)]
    assert [(command.task_id, command.role) for command in hand_backs] == [
        (task_id, Role.IMPLEMENTER)
    ]
    assert await tmux.is_pane_dead(operator_pane)
    assert not events.since(0, kinds=[EventKind.PANE_RESTORED], task_id=task_id)


async def test_dead_special_pane_is_respawned_in_place(
    connection: Connection, tmux_server: TmuxServer, tmp_path: Path
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    layout, tasks, events, task_id = await _task_window(connection, clock, tmux_server, tmp_path)
    task = tasks.get(task_id)
    assert task is not None
    assert task.window_id is not None
    tmux = build_tmux(tmux_server)
    all_panes = await tmux.list_panes(task.window_id, FORMAT_PANE_ID)
    special = next(pane for pane in all_panes if pane not in task.panes.values())
    await tmux.respawn_pane(special, ("/bin/sh", "-c", "exit"), env={}, cwd=tmp_path)

    async def dead() -> bool:
        return await tmux.is_pane_dead(special)

    await wait_until(dead, timeout=10)
    await layout.ensure(task)

    assert await tmux.pane_exists(special)
    assert not await tmux.is_pane_dead(special)
    restored = events.since(0, kinds=[EventKind.PANE_RESTORED], task_id=task_id)
    assert restored
    assert restored[-1].data.get("pane") in {"system", "shell"}


async def test_restore_from_a_stale_snapshot_leaves_a_finished_task_closed(
    connection: Connection, tmux_server: TmuxServer, tmp_path: Path
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    layout, tasks, _events, task_id = await _task_window(connection, clock, tmux_server, tmp_path)
    snapshot = tasks.get(task_id)
    assert snapshot is not None
    assert snapshot.window_id is not None
    tasks.apply_transition(
        task_id, Transition.stay(snapshot, step=Step.INTEGRATE, status=Status.DONE)
    )
    finished = tasks.get(task_id)
    assert finished is not None
    await layout.close(finished)

    await layout.restore([snapshot])

    windows = await tmux_server.run(
        "list-windows", "-t", exact_target(session_name("test")), "-F", "#{window_id}"
    )
    assert snapshot.window_id not in windows.stdout.split()
    assert len(windows.stdout.split()) == 1


class _CountingRunner(SubprocessRunner):
    def __init__(self) -> None:
        super().__init__()
        self.invocations = 0

    async def run(self, argv: Sequence[str], **options: Any) -> ProcessResult:
        self.invocations += 1
        return await super().run(argv, **options)


_ACTIVE_TASK_COUNT = 3


async def test_healthy_restore_makes_one_tmux_call_whatever_the_task_count(
    connection: Connection, tmux_server: TmuxServer, tmp_path: Path
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    runner = _CountingRunner()
    tmux = Tmux(runner, socket=tmux_server.socket_name, env=tmux_server.env)
    layout, tasks, events, first_id = await _task_window(
        connection, clock, tmux_server, tmp_path, tmux=tmux
    )
    queue = CommandQueue(connection, clock)
    for index in range(_ACTIVE_TASK_COUNT - 1):
        queued = queue.append(Submit(title=f"extra {index}", request="extra"))
        assert isinstance(queued.command, Submit)
        await layout.ensure(tasks.insert_from_submit(queued.command))
    for task in tasks.list_all():
        tasks.apply_transition(task.id, Transition.stay(task, step=task.step))
    active = tasks.list_active()
    assert len(active) == _ACTIVE_TASK_COUNT

    runner.invocations = 0
    await layout.restore(active)
    assert runner.invocations == 1

    victim = tasks.get(first_id)
    assert victim is not None
    assert victim.window_id is not None
    old_leader = victim.panes[Role.LEADER]
    await tmux_server.run("kill-pane", "-t", old_leader)
    await layout.restore(tasks.list_active())
    healed = tasks.get(first_id)
    assert healed is not None
    assert healed.panes[Role.LEADER] != old_leader
    assert await tmux.pane_exists(healed.panes[Role.LEADER])

    other = tasks.list_active()[-1]
    assert other.window_id is not None
    await tmux_server.run("kill-window", "-t", other.window_id)
    await layout.restore(tasks.list_active())
    recreated = tasks.get(other.id)
    assert recreated is not None
    assert recreated.window_id is not None
    assert await tmux.target_exists(recreated.window_id)
    assert events.since(0, kinds=[EventKind.PANE_RESTORED], task_id=first_id)

    runner.invocations = 0
    await layout.restore(tasks.list_active())
    assert runner.invocations == 1


_TITLE_FRAGMENTS = ("Leader fake:fake-1 iter ", "Implementer fake:fake-1 iter ", " ctx ")
_RENDER_TIMEOUT_SECONDS = 20.0


async def _rendered_borders(tmux_server: TmuxServer, tmux: Tmux, task: Task) -> bytes:
    assert task.window_id is not None
    await tmux.select_window(task.window_id)
    async with attach_tmux_client(tmux_server, exact_target(session_name("test"))) as client:

        async def drawn() -> bool:
            return all(fragment.encode() in client.process.output for fragment in _TITLE_FRAGMENTS)

        await wait_until(drawn, timeout=_RENDER_TIMEOUT_SECONDS)
        return client.process.output


async def test_session_restart_redraws_agent_pane_titles_without_a_usage_event(
    connection: Connection, tmux_server: TmuxServer, tmp_path: Path
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    layout, tasks, _, task_id = await _task_window(
        connection, clock, tmux_server, tmp_path, layout_tree=_STACKED_LAYOUT
    )
    task = tasks.get(task_id)
    assert task is not None
    tasks.apply_transition(task_id, Transition.stay(task, step=Step.IMPLEMENT))
    task = tasks.get(task_id)
    assert task is not None
    tmux = build_tmux(tmux_server)
    await tmux_server.run("kill-session", "-t", exact_target(session_name("test")))
    await SessionView(tmux).ensure("test", tmp_path, home_command=("/bin/sh",))

    await layout.restore([task])

    restored = tasks.get(task_id)
    assert restored is not None
    await _rendered_borders(tmux_server, tmux, restored)
    for role in (Role.LEADER, Role.IMPLEMENTER):
        assert socket.gethostname() not in await tmux.display(restored.panes[role], "#{pane_title}")


async def test_a_single_restored_agent_pane_is_retitled(
    connection: Connection, tmux_server: TmuxServer, tmp_path: Path
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    layout, tasks, _, task_id = await _task_window(connection, clock, tmux_server, tmp_path)
    task = tasks.get(task_id)
    assert task is not None
    tmux = build_tmux(tmux_server)
    await tmux_server.run("kill-pane", "-t", task.panes[Role.LEADER])

    restored = await layout.ensure(task)

    title = await tmux.display(restored.panes[Role.LEADER], "#{pane_title}")
    assert title.startswith("Leader")
    assert socket.gethostname() not in title


async def test_a_role_handed_back_before_its_pane_is_rebuilt_is_titled_without_the_operator_flag(
    connection: Connection, tmux_server: TmuxServer, tmp_path: Path
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    layout, tasks, _, task_id = await _task_window(connection, clock, tmux_server, tmp_path)
    task = tasks.get(task_id)
    assert task is not None
    runs = RunStore(connection, clock)
    run = runs.start(task_id, Role.IMPLEMENTER, _FAKE_RUNG)
    runs.mark_taken_over(run.id)
    tmux = build_tmux(tmux_server)
    await tmux_server.run("kill-pane", "-t", task.panes[Role.IMPLEMENTER])
    runs.mark_live(run.id)

    restored = await layout.ensure(task)

    title = await tmux.display(restored.panes[Role.IMPLEMENTER], "#{pane_title}")
    assert title.startswith("Implementer")
    assert "operator" not in title
