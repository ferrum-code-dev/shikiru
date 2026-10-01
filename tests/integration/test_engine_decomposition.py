"""Plan children and supersede replacements get the parent's work through the engine (03 §3)."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING

from fake_agent.scripted import FAKE_CHANGE_FILENAME, TICK_A1_ENABLED_VALUE, TICK_A1_ENV_VAR

from shikiru.bootstrap import build_engine
from shikiru.domain.commands import Submit
from shikiru.domain.tasks import Mode, Role, Status, StatusReason
from shikiru.engine.integration import PR_POLL
from shikiru.process import SubprocessRunner
from shikiru.store.commands import CommandQueue
from shikiru.store.database import Database
from shikiru.store.tasks import TaskStore
from shikiru.tmux.session import SessionView
from shikiru.workspace.roots import task_root
from tests.support.clock import FakeClock
from tests.support.engine import (
    build_registry,
    build_tmux,
    engine_settings,
    set_role_script,
    wait_for_command,
)
from tests.support.factories import CPU_UNCAPPED_PCT, TASK_CREATED_AT
from tests.support.flow import init_project_repo
from tests.support.git_repo import commit_all
from tests.support.github import MergeStyle, add_bare_remote, merge_on_remote
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable
    from pathlib import Path

    import pytest

    from shikiru.config.settings import Settings
    from shikiru.domain.tasks import Task
    from tests.support.github import FakeGitHub
    from tests.support.tmux import TmuxServer

_TRACKER = "tasks/todo.md"
_TRACKER_OPEN = "- [ ] A1: the parent entry\n"
_TRACKER_TICKED = "- [x] A1: the parent entry\n"
_POLL_ADVANCE_SECONDS = 0.25
_TIMEOUT_SECONDS = 60
_STALL: dict[str, object] = {"kind": "stall"}


@dataclass
class _Project:
    clock: FakeClock
    tasks: TaskStore
    queue: CommandQueue

    async def submit(self, command: Submit) -> str:
        outcome = await wait_for_command(self.queue.append(command), self.queue, self.clock)
        assert outcome.result is not None
        return str(outcome.result["task_id"])

    async def until(
        self, condition: Callable[[], bool], *, advance: float = _POLL_ADVANCE_SECONDS
    ) -> None:
        async def reached() -> bool:
            if self.clock.has_pending_sleep():
                self.clock.advance(advance)
            return condition()

        await wait_until(reached, timeout=_TIMEOUT_SECONDS)

    def task(self, task_id: str) -> Task:
        task = self.tasks.get(task_id)
        assert task is not None
        return task

    def child_of(self, parent_id: str) -> Task | None:
        return next((task for task in self.tasks.list_all() if task.parent_id == parent_id), None)

    def has_root(self, task_id: str) -> bool:
        child = self.tasks.get(task_id)
        return child is not None and child.repo is not None


@asynccontextmanager
async def _engine(
    tmp_path: Path, tmux_server: TmuxServer, settings: Settings
) -> AsyncGenerator[_Project]:
    clock = FakeClock(TASK_CREATED_AT)
    tmux = build_tmux(tmux_server)
    await SessionView(tmux).ensure("test", tmp_path, home_command=("/bin/sh",))
    engine = build_engine(tmp_path, settings, clock=clock, tmux=tmux, registry=build_registry())
    connection = Database(tmp_path / ".shikiru" / "state.db").connect()
    engine_task = asyncio.create_task(engine.run())
    try:
        yield _Project(clock, TaskStore(connection, clock), CommandQueue(connection, clock))
    finally:
        engine_task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await engine_task
        connection.close()


async def test_superseded_replacements_receive_the_committed_tracker_ticks_through_the_engine(
    tmp_path: Path, tmux_server: TmuxServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    await init_project_repo(tmp_path)
    (tmp_path / "tasks").mkdir()
    (tmp_path / _TRACKER).write_text(_TRACKER_OPEN)
    await commit_all(SubprocessRunner(), tmp_path, "tracker")
    monkeypatch.setenv(TICK_A1_ENV_VAR, TICK_A1_ENABLED_VALUE)
    await set_role_script(tmux_server, Role.IMPLEMENTER, [{"kind": "accept"}, _STALL])
    replacement: dict[str, object] = {"title": "redo it properly", "request": "redo"}
    await set_role_script(
        tmux_server, Role.LEADER, [{"kind": "supersede", "new_tasks": [replacement]}, _STALL]
    )
    settings = engine_settings(max_cpu_pct=CPU_UNCAPPED_PCT)
    settings = settings.model_copy(
        update={"project": settings.project.model_copy(update={"trackers": [_TRACKER]})}
    )
    async with _engine(tmp_path, tmux_server, settings) as project:
        parent_id = await project.submit(
            Submit(title="rejected work", request="work", mode=Mode.IMPLEMENT)
        )

        await project.until(
            lambda: (
                (child := project.child_of(parent_id)) is not None and project.has_root(child.id)
            )
        )

        assert project.task(parent_id).status_reason is StatusReason.SUPERSEDED
        child = project.child_of(parent_id)
        assert child is not None
        root = task_root(tmp_path, child.id)
        assert (root / _TRACKER).read_text() == _TRACKER_TICKED
        assert not (root / FAKE_CHANGE_FILENAME).exists()
        assert project.task(child.id).status is not Status.CANCELLED


async def test_plan_children_in_pr_mode_start_from_the_merged_base_through_the_engine(
    tmp_path: Path,
    tmux_server: TmuxServer,
    fake_github: FakeGitHub,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    await init_project_repo(tmp_path)
    settings = engine_settings(max_cpu_pct=CPU_UNCAPPED_PCT)
    settings = settings.model_copy(
        update={"orchestration": settings.orchestration.model_copy(update={"integration": "pr"})}
    )
    remote_dir = tmp_path_factory.mktemp("remote") / "origin.git"
    await add_bare_remote(tmp_path, remote_dir, settings.repo.remote)
    child: dict[str, object] = {"title": "execute the plan", "request": "execute"}
    await set_role_script(
        tmux_server, Role.LEADER, [{"kind": "accept", "new_tasks": [child]}, _STALL]
    )
    async with _engine(tmp_path, tmux_server, settings) as project:
        parent_id = await project.submit(
            Submit(title="plan the work", request="plan", mode=Mode.PLAN)
        )
        await project.until(lambda: len(fake_github.pull_requests()) == 1)
        pull = fake_github.pull_requests()[0]
        waiting = project.child_of(parent_id)
        assert waiting is not None
        assert not project.has_root(waiting.id)

        fake_github.merge(
            1, await merge_on_remote(remote_dir, pull["base"], pull["head"], MergeStyle.SQUASH)
        )
        await project.until(
            lambda: (
                (created := project.child_of(parent_id)) is not None
                and project.has_root(created.id)
            ),
            advance=PR_POLL.total_seconds(),
        )

        created = project.child_of(parent_id)
        assert created is not None
        assert project.task(parent_id).status is Status.DONE
        assert (task_root(tmp_path, created.id) / FAKE_CHANGE_FILENAME).exists()
