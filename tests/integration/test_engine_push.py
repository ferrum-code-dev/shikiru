"""Periodic and after-integration pushes of locally integrated bases to a real remote."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from shikiru.bootstrap import build_engine
from shikiru.config.settings import RepoConfig
from shikiru.domain.commands import COMMAND_POLL_SECONDS, Submit
from shikiru.domain.events import EventKind
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import Mode, Status, Step
from shikiru.engine.fault_reporter import FaultReporter
from shikiru.engine.integration import IntegrationMetadata, Integrator, LocalMerge, UncommittedWork
from shikiru.engine.push_service import PUSH_FAULT_SCOPE, PushService
from shikiru.engine.tick_guard import TickGuard
from shikiru.paths import state_db_path
from shikiru.process import SubprocessRunner
from shikiru.store.commands import CommandQueue, CommandStatus
from shikiru.store.database import Database, transaction
from shikiru.store.events import EventLog
from shikiru.store.pushes import PushTargets
from shikiru.store.tasks import TaskStore
from shikiru.tmux.session import SessionView
from shikiru.workspace.base_refs import LocalBaseRefs
from shikiru.workspace.git import Git
from tests.support.clock import FakeClock
from tests.support.engine import (
    build_registry,
    build_tmux,
    engine_settings,
    wait_for_command,
)
from tests.support.factories import CPU_UNCAPPED_PCT, TASK_CREATED_AT
from tests.support.flow import init_project_repo
from tests.support.git_repo import GIT_COMMAND_TIMEOUT, commit_all, init_repo, run_git
from tests.support.github import add_bare_remote
from tests.support.permissions import EXECUTABLE_MODE
from tests.support.store import seed_task
from tests.support.waiting import wait_until
from tests.support.workspace import task_root_builder

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

    from shikiru.domain.tasks import Task
    from tests.support.store import Connection
    from tests.support.tmux import TmuxServer

_RUNNER = SubprocessRunner()
_REMOTE = "origin"
_FOUR_HOURS = 14400
_HOUR = 3600
_LOGGER = logging.getLogger("push-test")


@dataclass
class Rig:
    connection: Connection
    clock: FakeClock
    project_root: Path
    remote_dir: Path
    events: EventLog
    tasks: TaskStore
    interval: int
    service: PushService
    integrator: Integrator

    async def integrate(self, name: str, *, base: str = "main") -> Task:
        task = seed_task(self.connection, self.clock, title=name, mode=Mode.IMPLEMENT)
        self.tasks.apply_transition(
            task.id, Transition.stay(task, step=Step.INTEGRATE, status=Status.INTEGRATING)
        )
        state = await task_root_builder(_RUNNER, self.project_root, RepoConfig(base=base)).build(
            task.id, task.title
        )
        self.tasks.set_repo(task.id, state)
        root = self.project_root / ".shikiru" / "worktrees" / task.id
        (root / f"{name}.txt").write_text(f"{name}\n")
        await commit_all(_RUNNER, root, name)
        current = self.tasks.get(task.id)
        assert current is not None
        await self.integrator.run(current, _resolve_nothing, IntegrationMetadata(name))
        return current

    async def push_due(self) -> None:
        self.service.poll(_LOGGER)
        await self.service.finish_outstanding()

    def restart(self) -> None:
        self.service = _service(
            self.connection, self.events, self.clock, self.project_root, self.interval
        )

    async def remote_sha(self, ref: str) -> str:
        return await run_git(_RUNNER, self.remote_dir, "rev-parse", f"refs/heads/{ref}")

    async def remote_branches(self) -> list[str]:
        refs = await run_git(
            _RUNNER, self.remote_dir, "for-each-ref", "--format=%(refname)", "refs/"
        )
        return refs.splitlines()

    async def local_sha(self, ref: str) -> str:
        return await run_git(_RUNNER, self.project_root, "rev-parse", ref)

    def kinds(self) -> list[EventKind]:
        return [event.kind for event in self.events.since(0)]


async def _resolve_nothing(
    task: Task, continue_rebase: Callable[[], Awaitable[None]], recovery: object
) -> tuple[Task, IntegrationMetadata]:
    del continue_rebase, recovery
    return task, IntegrationMetadata("resolved")


def _service(
    connection: Connection, events: EventLog, clock: FakeClock, project_root: Path, interval: int
) -> PushService:
    settings = engine_settings().orchestration.faults
    reporter = FaultReporter(events=events, clock=clock, settings=settings, logger=_LOGGER)
    return PushService(
        targets=PushTargets(connection),
        git=Git(_RUNNER, command_timeout=GIT_COMMAND_TIMEOUT),
        guard=TickGuard(
            reporter=reporter,
            clock=clock,
            settings=settings,
            logger=_LOGGER,
            scope=PUSH_FAULT_SCOPE,
        ),
        clock=clock,
        project_root=project_root,
        remote=_REMOTE,
        interval_seconds=interval,
    )


async def _rig(
    connection: Connection, tmp_path: Path, *, interval: int, branches: tuple[str, ...] = ()
) -> Rig:
    project_root = tmp_path / "project"
    await init_repo(_RUNNER, project_root)
    (project_root / "README.md").write_text("base\n")
    await commit_all(_RUNNER, project_root, "initial")
    for branch in branches:
        await run_git(_RUNNER, project_root, "branch", branch)
    remote_dir = tmp_path / "origin.git"
    await add_bare_remote(project_root, remote_dir, _REMOTE)
    clock = FakeClock(TASK_CREATED_AT)
    events = EventLog(connection, clock)
    tasks = TaskStore(connection, clock)
    service = _service(connection, events, clock, project_root, interval)

    async def keep_worktree(task: Task) -> None:
        del task

    integrator = Integrator(
        git=Git(_RUNNER, command_timeout=GIT_COMMAND_TIMEOUT),
        strategy=LocalMerge(
            Git(_RUNNER, command_timeout=GIT_COMMAND_TIMEOUT),
            clock,
            events=events,
            project_root=project_root,
            integration_wait=0,
        ),
        base_refs=LocalBaseRefs(),
        lock=asyncio.Lock(),
        project_root=project_root,
        tasks=tasks,
        events=events,
        finish_task=keep_worktree,
        publication=service,
        atomically=lambda: transaction(connection),
    )
    return Rig(
        connection, clock, project_root, remote_dir, events, tasks, interval, service, integrator
    )


async def test_integrations_accumulate_and_the_base_is_pushed_at_the_deadline_without_more_work(
    connection: Connection, tmp_path: Path
) -> None:
    rig = await _rig(connection, tmp_path, interval=_FOUR_HOURS)
    remote_start = await rig.remote_sha("main")

    await rig.integrate("first")
    rig.clock.advance(3 * _HOUR)
    await rig.integrate("second")
    await rig.push_due()
    assert await rig.remote_sha("main") == remote_start

    rig.clock.advance(_HOUR)
    await rig.push_due()

    assert await rig.remote_sha("main") == await rig.local_sha("main")
    assert await rig.remote_branches() == ["refs/heads/main"]
    assert not PushTargets(connection).pending()


async def test_zero_interval_pushes_after_each_integration_and_never_for_a_failed_one(
    connection: Connection, tmp_path: Path
) -> None:
    rig = await _rig(connection, tmp_path, interval=0)
    await rig.integrate("first")
    await rig.push_due()
    assert await rig.remote_sha("main") == await rig.local_sha("main")

    task = seed_task(connection, rig.clock, title="dirty", mode=Mode.IMPLEMENT)
    rig.tasks.apply_transition(
        task.id, Transition.stay(task, step=Step.INTEGRATE, status=Status.INTEGRATING)
    )
    state = await task_root_builder(_RUNNER, rig.project_root, RepoConfig(base="main")).build(
        task.id, task.title
    )
    rig.tasks.set_repo(task.id, state)
    root = rig.project_root / ".shikiru" / "worktrees" / task.id
    (root / "committed.txt").write_text("committed\n")
    await commit_all(_RUNNER, root, "committed")
    (root / "README.md").write_text("uncommitted\n")
    current = rig.tasks.get(task.id)
    assert current is not None
    with pytest.raises(UncommittedWork):
        await rig.integrator.run(current, _resolve_nothing, IntegrationMetadata("dirty"))

    await rig.push_due()

    assert not PushTargets(connection).pending()
    assert await rig.remote_branches() == ["refs/heads/main"]
    assert await rig.remote_sha("main") == await rig.local_sha("main")


async def test_a_configured_base_is_pushed_to_its_own_remote_branch_only(
    connection: Connection, tmp_path: Path
) -> None:
    rig = await _rig(connection, tmp_path, interval=0, branches=("release",))
    main_on_remote = await rig.remote_sha("main")

    await rig.integrate("hotfix", base="release")
    await rig.push_due()

    assert await rig.remote_sha("release") == await rig.local_sha("release")
    assert await rig.remote_sha("main") == main_on_remote
    assert sorted(await rig.remote_branches()) == ["refs/heads/main", "refs/heads/release"]


async def test_pending_work_and_its_deadline_survive_a_restart(
    connection: Connection, tmp_path: Path
) -> None:
    rig = await _rig(connection, tmp_path, interval=_FOUR_HOURS)
    remote_start = await rig.remote_sha("main")
    await rig.integrate("kept")

    rig.restart()
    rig.clock.advance(3 * _HOUR)
    await rig.push_due()
    assert await rig.remote_sha("main") == remote_start

    rig.restart()
    rig.clock.advance(_HOUR)
    await rig.push_due()
    assert await rig.remote_sha("main") == await rig.local_sha("main")


async def test_overdue_work_is_sent_right_after_a_restart(
    connection: Connection, tmp_path: Path
) -> None:
    rig = await _rig(connection, tmp_path, interval=_FOUR_HOURS)
    await rig.integrate("overdue")

    rig.clock.advance(timedelta(days=2).total_seconds())
    rig.restart()
    await rig.push_due()

    assert await rig.remote_sha("main") == await rig.local_sha("main")


async def test_an_integration_applied_before_a_crash_is_still_published_when_recovered(
    connection: Connection, tmp_path: Path
) -> None:
    rig = await _rig(connection, tmp_path, interval=0)
    task = seed_task(connection, rig.clock, title="landed", mode=Mode.IMPLEMENT)
    rig.tasks.apply_transition(
        task.id, Transition.stay(task, step=Step.INTEGRATE, status=Status.INTEGRATING)
    )
    state = await task_root_builder(_RUNNER, rig.project_root, RepoConfig(base="main")).build(
        task.id, task.title
    )
    rig.tasks.set_repo(task.id, state)
    root = rig.project_root / ".shikiru" / "worktrees" / task.id
    (root / "landed.txt").write_text("landed\n")
    await commit_all(_RUNNER, root, "landed")
    await run_git(_RUNNER, rig.project_root, "merge", "--ff-only", state.branch)
    assert not PushTargets(connection).pending()

    current = rig.tasks.get(task.id)
    assert current is not None
    await rig.integrator.run(current, _resolve_nothing, IntegrationMetadata("landed"))
    await rig.push_due()

    assert await rig.remote_sha("main") == await rig.local_sha("main")


async def test_an_integration_registered_during_a_push_stays_pending(
    connection: Connection, tmp_path: Path
) -> None:
    rig = await _rig(connection, tmp_path, interval=_FOUR_HOURS)
    targets = PushTargets(connection)
    now = rig.clock.now()
    targets.register("main", now=now, interval=_FOUR_HOURS)
    sent = targets.pending()[0]

    targets.register("main", now=now + timedelta(hours=1), interval=_FOUR_HOURS)
    acknowledged_at = now + timedelta(hours=4)
    targets.acknowledge(
        "main", registered=sent.registered, now=acknowledged_at, interval=_FOUR_HOURS
    )

    (remaining,) = targets.pending()
    assert remaining.due_at == acknowledged_at + timedelta(hours=4)


async def test_a_missing_remote_keeps_the_work_pending_and_recovers_when_it_appears(
    connection: Connection, tmp_path: Path
) -> None:
    rig = await _rig(connection, tmp_path, interval=0)
    await run_git(_RUNNER, rig.project_root, "remote", "rename", _REMOTE, "elsewhere")
    await rig.integrate("stranded")

    await rig.push_due()

    assert EventKind.ENGINE_FAULT in rig.kinds()
    assert PushTargets(connection).pending()
    await run_git(_RUNNER, rig.project_root, "remote", "rename", "elsewhere", _REMOTE)
    rig.clock.advance(engine_settings().orchestration.faults.backoff_initial_seconds)
    await rig.push_due()

    assert await rig.remote_sha("main") == await rig.local_sha("main")
    assert not PushTargets(connection).pending()
    assert EventKind.ENGINE_RECOVERED in rig.kinds()


async def test_a_diverged_remote_is_never_overwritten(
    connection: Connection, tmp_path: Path
) -> None:
    rig = await _rig(connection, tmp_path, interval=0)
    clone = tmp_path / "clone"
    await run_git(_RUNNER, None, "clone", "--branch", "main", str(rig.remote_dir), str(clone))
    (clone / "elsewhere.txt").write_text("remote work\n")
    await commit_all(_RUNNER, clone, "remote work")
    await run_git(_RUNNER, clone, "push", _REMOTE, "main")
    diverged = await rig.remote_sha("main")

    await rig.integrate("local")
    await rig.push_due()

    assert await rig.remote_sha("main") == diverged
    assert EventKind.ENGINE_FAULT in rig.kinds()
    assert PushTargets(connection).pending()


_HANGING_PRE_RECEIVE = """#!/bin/sh
echo started >> '{calls}'
while [ ! -e '{release}' ]; do sleep 0.05; done
"""
_WAIT_SECONDS = 10.0


async def test_a_hung_push_never_stops_the_engine_from_processing_commands(
    tmp_path: Path, tmux_server: TmuxServer, tmp_path_factory: pytest.TempPathFactory
) -> None:
    project_root = tmp_path / "project"
    await init_project_repo(project_root)
    settings = engine_settings(max_cpu_pct=CPU_UNCAPPED_PCT)
    settings = settings.model_copy(
        update={
            "orchestration": settings.orchestration.model_copy(update={"push_interval_seconds": 0})
        }
    )
    remote_dir = tmp_path_factory.mktemp("remote") / "origin.git"
    await add_bare_remote(project_root, remote_dir, settings.repo.remote)
    hang_dir = tmp_path_factory.mktemp("hang")
    calls, release = hang_dir / "calls", hang_dir / "go"
    hook = remote_dir / "hooks" / "pre-receive"
    hook.write_text(_HANGING_PRE_RECEIVE.format(calls=calls, release=release))
    hook.chmod(EXECUTABLE_MODE)
    (project_root / "integrated.txt").write_text("integrated\n")
    await commit_all(_RUNNER, project_root, "integrated locally")
    clock = FakeClock(TASK_CREATED_AT)
    connection = Database(state_db_path(project_root)).connect()
    PushTargets(connection).register("main", now=clock.now(), interval=0)
    tmux = build_tmux(tmux_server)
    await SessionView(tmux).ensure("test", project_root, home_command=("/bin/sh",))
    engine = build_engine(project_root, settings, clock=clock, tmux=tmux, registry=build_registry())
    queue = CommandQueue(connection, clock)
    running = asyncio.create_task(engine.run())
    try:

        async def push_started() -> bool:
            clock.advance(COMMAND_POLL_SECONDS)
            return calls.exists()

        await wait_until(push_started, _WAIT_SECONDS)

        outcome = await wait_for_command(
            queue.append(Submit(title="independent", request="independent")), queue, clock
        )

        assert outcome.status is CommandStatus.DONE
        assert PushTargets(connection).pending()
        release.touch()

        async def pushed() -> bool:
            clock.advance(COMMAND_POLL_SECONDS)
            return not PushTargets(connection).pending()

        await wait_until(pushed, _WAIT_SECONDS)
        assert await run_git(_RUNNER, remote_dir, "rev-parse", "refs/heads/main") == (
            await run_git(_RUNNER, project_root, "rev-parse", "main")
        )
    finally:
        release.touch()
        running.cancel()
        with suppress(asyncio.CancelledError):
            await running
        connection.close()
