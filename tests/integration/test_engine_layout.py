"""Layout restoration at the real task-window boundary."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.agents.contracts import Rung
from shikiru.config.settings import AgentConfig, LayoutSplit, PaneLeaf, ProjectConfig
from shikiru.domain.commands import HandBack, Submit
from shikiru.domain.events import EventKind
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import Role, Status, Step
from shikiru.engine.layout_guard import LayoutGuard
from shikiru.store.commands import CommandQueue
from shikiru.store.events import EventLog
from shikiru.store.runs import RunStore
from shikiru.store.tasks import TaskStore
from shikiru.tmux.client import FORMAT_PANE_ID, exact_target
from shikiru.tmux.decorator import PaneDecorator
from shikiru.tmux.layout import LayoutBuilder
from shikiru.tmux.session import SessionView, session_name
from tests.support.clock import FakeClock
from tests.support.engine import build_tmux
from tests.support.factories import TASK_CREATED_AT, make_settings
from tests.support.waiting import wait_until

if TYPE_CHECKING:
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


def _settings() -> Settings:
    settings = make_settings(
        leader_ladder=[_FAKE_RUNG],
        implementer_ladder=[_FAKE_RUNG],
        agents={"fake": AgentConfig()},
    )
    return settings.model_copy(
        update={
            "project": ProjectConfig(name="test", trackers=[]),
            "dashboard": settings.dashboard.model_copy(update={"layout": _LAYOUT}),
        }
    )


async def _task_window(
    connection: Connection,
    clock: Clock,
    tmux_server: TmuxServer,
    tmp_path: Path,
) -> tuple[LayoutGuard, TaskStore, EventLog, str]:
    settings = _settings()
    tmux = build_tmux(tmux_server)
    await SessionView(tmux).ensure("test", tmp_path, home_command=("/bin/sh",))
    tasks = TaskStore(connection, clock)
    events = EventLog(connection, clock)
    layout = LayoutGuard(
        tmux=tmux,
        view=SessionView(tmux),
        builder=LayoutBuilder(tmux),
        decorator=PaneDecorator(tmux),
        session_target=exact_target(session_name("test")),
        tasks=tasks,
        settings=settings,
        project_root=tmp_path,
        events=events,
        runs=RunStore(connection, clock),
        commands=CommandQueue(connection, clock),
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
    assert hand_backs == [HandBack(task_id=task_id, role=Role.IMPLEMENTER)]
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
