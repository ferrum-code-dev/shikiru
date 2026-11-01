"""Quota waits shown on task windows and role panes, read back from real tmux and SQLite."""

from __future__ import annotations

import dataclasses
import json
from datetime import timedelta
from typing import TYPE_CHECKING

from shikiru.agents.contracts import Rung
from shikiru.config.settings import AgentConfig
from shikiru.domain.quota_waits import QuotaAction, ScheduledQuotaAction
from shikiru.domain.scheduling import WaitReason
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import Hold, Role, Status, Step
from shikiru.engine.quota_schedule import QuotaSchedule
from shikiru.paths import run_dir, task_root
from shikiru.store.quotas import QuotaStore
from shikiru.tmux.decorator import (
    PANE_COLOR_OPTION,
    PANE_GLYPH_OPTION,
    WINDOW_STATE_COLOR_OPTION,
    WINDOW_STATE_GLYPH_OPTION,
    PaneDecorator,
)
from shikiru.ui.glyphs import GLYPH_ESCALATED, GLYPH_PAUSED, QUOTA_MARKER, ROLE_GLYPHS
from shikiru.ui.tokens import AMBER, HOLD_COLOR
from shikiru.view_log import quota_status_path, read_quota_status, view_argv, view_log_path
from tests.support.clock import FakeClock
from tests.support.engine import FAKE_RUNG, build_titles
from tests.support.factories import make_settings
from tests.support.flow import FAKE_AGENT_NAME, build_flow_task, init_project_repo
from tests.support.quota_waits import record_quota_wait
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

    from shikiru.config.settings import Settings
    from shikiru.domain.tasks import Task
    from shikiru.engine.titles import TaskTitles
    from tests.support.engine import EngineEnv
    from tests.support.tmux import TmuxServer

_TIMEOUT_SECONDS = 20
_BLOCK_HOURS = 1
_NEXT_CHECK_MINUTES = 5
_LEADER_AGENT = "alpha"
_IMPLEMENTER_AGENT = "beta"
_LEADER_RUNG = Rung(agent=_LEADER_AGENT, model="lead-1", effort=None)
_IMPLEMENTER_RUNG = Rung(agent=_IMPLEMENTER_AGENT, model="impl-1", effort=None)
_STRIP_PHRASE = "Quota blocked"
_NARROW_WIDTH = 24
_WIDE_WIDTH = 100
_HEIGHT = 20
_SHORT_HEIGHT = 10
_TURN = "01-implement"
_RUNG = {"agent": "beta", "model": "impl-1", "effort": None}


def _settings() -> Settings:
    return make_settings(
        leader_ladder=[_LEADER_RUNG],
        implementer_ladder=[_IMPLEMENTER_RUNG],
        helper_ladder=[FAKE_RUNG],
        agents={
            _LEADER_AGENT: AgentConfig(),
            _IMPLEMENTER_AGENT: AgentConfig(),
            FAKE_AGENT_NAME: AgentConfig(),
        },
    )


@dataclasses.dataclass
class _Wait:
    env: EngineEnv
    settings: Settings
    schedule: QuotaSchedule
    titles: TaskTitles
    task: Task

    @property
    def clock(self) -> FakeClock:
        assert isinstance(self.env.clock, FakeClock)
        return self.env.clock

    @property
    def quotas(self) -> QuotaStore:
        return QuotaStore(self.env.connection)

    def block(self, agent: str, *, hours: int = _BLOCK_HOURS) -> datetime:
        until = self.env.clock.now() + timedelta(hours=hours)
        self.quotas.block(agent, until, "quota", self.env.clock.now())
        return until

    def schedule_check(self, agent: str, action: QuotaAction) -> None:
        blocked = self.quotas.block_for(agent)
        assert blocked is not None
        self.schedule.record(
            agent,
            ScheduledQuotaAction(
                action,
                self.env.clock.now() + timedelta(minutes=_NEXT_CHECK_MINUTES),
                blocked.blocked_at,
            ),
        )

    def queue_on_quota(self) -> None:
        self.env.tasks.set_wait_reason(self.task.id, WaitReason.QUOTA)

    def current(self) -> Task:
        return self.env.tasks.require(self.task.id)

    async def sync(self) -> None:
        await self.titles.sync_quota(self.current())

    def status_path(self, role: Role) -> Path:
        return quota_status_path(run_dir(self.env.project_root, self.task.id), role.value)

    async def window(self) -> tuple[str, str]:
        target = self.current().window_id
        assert target is not None
        return (
            await self.env.tmux.display(target, f"#{{{WINDOW_STATE_COLOR_OPTION}}}"),
            await self.env.tmux.display(target, f"#{{{WINDOW_STATE_GLYPH_OPTION}}}"),
        )

    async def pane(self, role: Role) -> tuple[str, str]:
        target = self.current().panes[role]
        return (
            await self.env.tmux.display(target, f"#{{{PANE_COLOR_OPTION}}}"),
            await self.env.tmux.display(target, f"#{{{PANE_GLYPH_OPTION}}}"),
        )


async def _wait(
    engine_env: EngineEnv, *, running: bool = False, schedule: QuotaSchedule | None = None
) -> _Wait:
    await init_project_repo(engine_env.project_root)
    settings = _settings()
    task = await build_flow_task(engine_env)
    if running:
        engine_env.tasks.apply_transition(
            task.id,
            dataclasses.replace(
                Transition.stay(task, step=Step.IMPLEMENT, status=Status.RUNNING), iteration=1
            ),
        )
    shared = schedule if schedule is not None else QuotaSchedule()
    titles = build_titles(
        connection=engine_env.connection,
        clock=engine_env.clock,
        decorator=PaneDecorator(engine_env.tmux),
        settings=settings,
        project_root=engine_env.project_root,
        schedule=shared,
    )
    return _Wait(engine_env, settings, shared, titles, task)


async def test_queued_wait_marks_the_window_and_only_the_blocked_role_pane(
    engine_env: EngineEnv,
) -> None:
    wait = await _wait(engine_env)
    await wait.sync()
    assert (await wait.window())[1] != QUOTA_MARKER
    window_target = wait.current().window_id
    assert window_target is not None
    name_before = await engine_env.tmux.display(window_target, "#{window_name}")

    wait.queue_on_quota()
    wait.block(_IMPLEMENTER_AGENT)
    wait.schedule_check(_IMPLEMENTER_AGENT, QuotaAction.NEXT_CHECK)
    await wait.sync()

    assert await wait.window() == (AMBER, QUOTA_MARKER)
    assert (await wait.pane(Role.IMPLEMENTER))[0] == AMBER
    assert (await wait.pane(Role.LEADER))[1] == ROLE_GLYPHS[Role.LEADER]
    assert wait.status_path(Role.IMPLEMENTER).is_file()
    assert not wait.status_path(Role.LEADER).exists()
    assert await engine_env.tmux.display(window_target, "#{window_name}") == name_before


async def test_a_blocked_leader_marks_the_leader_pane_when_the_implementer_would_act_next(
    engine_env: EngineEnv,
) -> None:
    wait = await _wait(engine_env)
    wait.queue_on_quota()
    wait.block(_LEADER_AGENT)
    await wait.sync()

    assert await wait.window() == (AMBER, QUOTA_MARKER)
    assert (await wait.pane(Role.LEADER))[0] == AMBER
    assert (await wait.pane(Role.IMPLEMENTER))[1] == ROLE_GLYPHS[Role.IMPLEMENTER]
    assert wait.status_path(Role.LEADER).is_file()
    assert not wait.status_path(Role.IMPLEMENTER).exists()


async def test_the_marks_clear_when_the_block_clears_and_stay_cleared_on_idle_refreshes(
    engine_env: EngineEnv,
) -> None:
    wait = await _wait(engine_env)
    wait.queue_on_quota()
    wait.block(_IMPLEMENTER_AGENT)
    await wait.sync()
    assert (await wait.window())[1] == QUOTA_MARKER

    wait.quotas.clear_block(_IMPLEMENTER_AGENT)
    engine_env.tasks.set_wait_reason(wait.task.id, None)
    await wait.sync()
    await wait.sync()

    assert (await wait.window())[1] != QUOTA_MARKER
    assert (await wait.pane(Role.IMPLEMENTER))[1] == ROLE_GLYPHS[Role.IMPLEMENTER]
    assert not wait.status_path(Role.IMPLEMENTER).exists()


async def test_a_passed_deadline_clears_the_pane_and_the_window_follows_the_wait_reason(
    engine_env: EngineEnv,
) -> None:
    wait = await _wait(engine_env)
    wait.queue_on_quota()
    until = wait.block(_IMPLEMENTER_AGENT)
    await wait.sync()
    assert (await wait.window())[1] == QUOTA_MARKER

    wait.clock.advance((until - engine_env.clock.now()).total_seconds() + 1)
    await wait.sync()

    assert (await wait.pane(Role.IMPLEMENTER))[1] == ROLE_GLYPHS[Role.IMPLEMENTER]
    assert not wait.status_path(Role.IMPLEMENTER).exists()

    engine_env.tasks.set_wait_reason(wait.task.id, None)
    await wait.sync()
    assert (await wait.window())[1] != QUOTA_MARKER


async def test_the_status_names_the_scheduled_action_and_shows_no_time_when_unknown(
    engine_env: EngineEnv,
) -> None:
    wait = await _wait(engine_env)
    wait.queue_on_quota()
    wait.block(_IMPLEMENTER_AGENT)
    await wait.sync()
    assert read_quota_status(wait.status_path(Role.IMPLEMENTER)) == (None, None)

    wait.schedule_check(_IMPLEMENTER_AGENT, QuotaAction.RETRY)
    await wait.sync()

    status = read_quota_status(wait.status_path(Role.IMPLEMENTER))
    assert status is not None
    assert status[0] is QuotaAction.RETRY
    assert status[1] == engine_env.clock.now() + timedelta(minutes=_NEXT_CHECK_MINUTES)

    wait.schedule_check(_IMPLEMENTER_AGENT, QuotaAction.NEXT_CHECK)
    wait.clock.advance(1)
    await wait.sync()
    changed = read_quota_status(wait.status_path(Role.IMPLEMENTER))
    assert changed is not None
    assert changed[0] is QuotaAction.NEXT_CHECK


async def test_a_new_block_ignores_the_schedule_of_an_earlier_one(
    engine_env: EngineEnv,
) -> None:
    wait = await _wait(engine_env)
    wait.queue_on_quota()
    wait.block(_IMPLEMENTER_AGENT)
    wait.schedule_check(_IMPLEMENTER_AGENT, QuotaAction.RETRY)
    wait.clock.advance(1)
    wait.block(_IMPLEMENTER_AGENT)
    await wait.sync()

    assert read_quota_status(wait.status_path(Role.IMPLEMENTER)) == (None, None)


async def test_a_running_task_waiting_mid_turn_marks_its_acting_role_only(
    engine_env: EngineEnv,
) -> None:
    wait = await _wait(engine_env, running=True)
    await wait.sync()
    assert (await wait.window())[1] != QUOTA_MARKER

    until = wait.block(_IMPLEMENTER_AGENT)
    record_quota_wait(
        engine_env.connection,
        wait.clock,
        wait.current(),
        role=Role.IMPLEMENTER,
        rung=_IMPLEMENTER_RUNG,
        until=until,
    )
    await wait.sync()

    assert await wait.window() == (AMBER, QUOTA_MARKER)
    assert (await wait.pane(Role.IMPLEMENTER))[0] == AMBER
    assert (await wait.pane(Role.LEADER))[1] == ROLE_GLYPHS[Role.LEADER]
    assert wait.status_path(Role.IMPLEMENTER).is_file()
    assert not wait.status_path(Role.LEADER).exists()

    wait.quotas.clear_block(_IMPLEMENTER_AGENT)
    await wait.sync()
    assert (await wait.window())[1] != QUOTA_MARKER
    assert not wait.status_path(Role.IMPLEMENTER).exists()


async def test_a_blocked_agent_of_a_role_that_is_not_acting_leaves_a_running_task_unmarked(
    engine_env: EngineEnv,
) -> None:
    wait = await _wait(engine_env, running=True)
    wait.block(_LEADER_AGENT)
    await wait.sync()

    assert (await wait.window())[1] != QUOTA_MARKER
    assert not wait.status_path(Role.LEADER).exists()
    assert not wait.status_path(Role.IMPLEMENTER).exists()


async def test_pause_keeps_its_decoration_over_a_quota_wait_and_quota_returns_on_resume(
    engine_env: EngineEnv,
) -> None:
    wait = await _wait(engine_env)
    wait.queue_on_quota()
    wait.block(_IMPLEMENTER_AGENT)
    engine_env.tasks.set_holds(wait.task.id, frozenset({Hold.PAUSED}))
    await wait.sync()

    assert await wait.window() == (HOLD_COLOR, GLYPH_PAUSED)
    assert (await wait.pane(Role.IMPLEMENTER))[1] == GLYPH_PAUSED
    assert not wait.status_path(Role.IMPLEMENTER).exists()

    engine_env.tasks.set_holds(wait.task.id, frozenset())
    await wait.titles.refresh(wait.current())
    assert await wait.window() == (AMBER, QUOTA_MARKER)
    assert wait.status_path(Role.IMPLEMENTER).is_file()


async def test_escalation_keeps_its_decoration_over_a_quota_wait(engine_env: EngineEnv) -> None:
    wait = await _wait(engine_env)
    wait.queue_on_quota()
    wait.block(_IMPLEMENTER_AGENT)
    await wait.sync()
    task = wait.current()
    engine_env.tasks.apply_transition(
        task.id, Transition.stay(task, step=task.step, status=Status.ESCALATED)
    )
    await wait.titles.refresh(wait.current())

    assert (await wait.window())[1] == GLYPH_ESCALATED
    assert not wait.status_path(Role.IMPLEMENTER).exists()


async def test_a_taken_over_pane_is_never_marked_while_the_other_role_pane_is(
    engine_env: EngineEnv,
) -> None:
    wait = await _wait(engine_env)
    wait.queue_on_quota()
    wait.block(_IMPLEMENTER_AGENT)
    wait.block(_LEADER_AGENT)
    run = engine_env.runs.start(wait.task.id, Role.IMPLEMENTER, _IMPLEMENTER_RUNG)
    engine_env.runs.mark_taken_over(run.id)
    await wait.sync()

    assert (await wait.pane(Role.IMPLEMENTER))[1] != ROLE_GLYPHS[Role.IMPLEMENTER]
    assert (await wait.pane(Role.IMPLEMENTER))[0] != AMBER
    assert not wait.status_path(Role.IMPLEMENTER).exists()
    assert (await wait.pane(Role.LEADER))[0] == AMBER
    assert wait.status_path(Role.LEADER).is_file()


async def test_a_restarted_engine_republishes_the_wait_with_an_unknown_time(
    engine_env: EngineEnv,
) -> None:
    first = await _wait(engine_env)
    first.queue_on_quota()
    first.block(_IMPLEMENTER_AGENT)
    first.schedule_check(_IMPLEMENTER_AGENT, QuotaAction.NEXT_CHECK)
    await first.sync()
    assert read_quota_status(first.status_path(Role.IMPLEMENTER)) is not None

    target = first.current().window_id
    assert target is not None
    await engine_env.tmux.set_window_option(target, WINDOW_STATE_GLYPH_OPTION, "")
    restarted = build_titles(
        connection=engine_env.connection,
        clock=engine_env.clock,
        decorator=PaneDecorator(engine_env.tmux),
        settings=first.settings,
        project_root=engine_env.project_root,
    )
    await restarted.sync_quota(first.current())

    assert (await first.window())[1] == QUOTA_MARKER
    assert read_quota_status(first.status_path(Role.IMPLEMENTER)) == (None, None)


def _row(signal: str, **fields: object) -> str:
    return json.dumps({"ts": "t", "turn": _TURN, "rung": _RUNG, "signal": signal, **fields})


async def _start_view(wait: _Wait, role: Role) -> str:
    pane = wait.current().panes[role]
    argv = view_argv(wait.env.project_root, wait.task.id, role.value)
    await wait.env.tmux.respawn_pane(
        pane, argv, env={}, cwd=task_root(wait.env.project_root, wait.task.id)
    )
    return pane


async def _screen(tmux_server: TmuxServer, pane: str) -> list[str]:
    return (await tmux_server.run("capture-pane", "-p", "-t", pane)).stdout.splitlines()


async def test_the_view_pane_pins_the_strip_above_prior_output_and_removes_it_on_clear(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    wait = await _wait(engine_env)
    log = view_log_path(run_dir(engine_env.project_root, wait.task.id), Role.IMPLEMENTER.value)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(_row("Message", text="earlier output") + "\n")
    pane = await _start_view(wait, Role.IMPLEMENTER)

    async def shows(part: str) -> bool:
        return any(part in line for line in await _screen(tmux_server, pane))

    await wait_until(lambda: shows("earlier output"), timeout=_TIMEOUT_SECONDS)

    wait.queue_on_quota()
    wait.block(_IMPLEMENTER_AGENT)
    wait.schedule_check(_IMPLEMENTER_AGENT, QuotaAction.NEXT_CHECK)
    await wait.sync()

    async def strip_on_top() -> bool:
        screen = await _screen(tmux_server, pane)
        return bool(screen) and _STRIP_PHRASE in screen[0] and "next check" in screen[0]

    await wait_until(strip_on_top, timeout=_TIMEOUT_SECONDS)
    assert await shows("earlier output")

    with log.open("a") as handle:
        handle.write(_row("Message", text="later output") + "\n")
    await wait_until(lambda: shows("later output"), timeout=_TIMEOUT_SECONDS)
    await wait.sync()
    await wait.sync()
    screen = await _screen(tmux_server, pane)
    assert sum(_STRIP_PHRASE in line for line in screen) == 1
    assert await shows("earlier output")

    wait.quotas.clear_block(_IMPLEMENTER_AGENT)
    engine_env.tasks.set_wait_reason(wait.task.id, None)
    await wait.sync()

    async def strip_gone() -> bool:
        return not await shows(_STRIP_PHRASE)

    await wait_until(strip_gone, timeout=_TIMEOUT_SECONDS)
    assert await shows("earlier output")
    assert await shows("later output")


async def test_the_strip_reflows_at_narrow_widths_and_follows_a_resize(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    wait = await _wait(engine_env)
    wait.queue_on_quota()
    wait.block(_IMPLEMENTER_AGENT)
    wait.schedule_check(_IMPLEMENTER_AGENT, QuotaAction.RETRY)
    await wait.sync()
    pane = wait.current().panes[Role.IMPLEMENTER]
    await tmux_server.run("set-option", "-w", "-t", pane, "window-size", "manual")
    await tmux_server.run("resize-window", "-t", pane, "-x", str(_NARROW_WIDTH), "-y", str(_HEIGHT))
    await _start_view(wait, Role.IMPLEMENTER)

    async def wrapped() -> bool:
        screen = await _screen(tmux_server, pane)
        top = [line for line in screen[:3] if line.strip()]
        text = " ".join(line.strip() for line in top)
        return len(top) > 1 and _STRIP_PHRASE in text and "retry" in text

    await wait_until(wrapped, timeout=_TIMEOUT_SECONDS)
    assert all(len(line) <= _NARROW_WIDTH for line in await _screen(tmux_server, pane))

    await tmux_server.run("resize-window", "-t", pane, "-x", str(_WIDE_WIDTH), "-y", str(_HEIGHT))

    async def single_row() -> bool:
        screen = await _screen(tmux_server, pane)
        return _STRIP_PHRASE in screen[0] and "retry" in screen[0]

    await wait_until(single_row, timeout=_TIMEOUT_SECONDS)
    screen = await _screen(tmux_server, pane)
    assert sum(_STRIP_PHRASE in line for line in screen) == 1


async def test_the_strip_keeps_output_visible_after_a_height_only_resize(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    wait = await _wait(engine_env)
    wait.queue_on_quota()
    wait.block(_IMPLEMENTER_AGENT)
    wait.schedule_check(_IMPLEMENTER_AGENT, QuotaAction.RETRY)
    await wait.sync()
    log = view_log_path(run_dir(engine_env.project_root, wait.task.id), Role.IMPLEMENTER.value)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(_row("Message", text="earlier output") + "\n")
    pane = wait.current().panes[Role.IMPLEMENTER]
    await tmux_server.run("set-option", "-w", "-t", pane, "window-size", "manual")
    await tmux_server.run("resize-window", "-t", pane, "-x", str(_WIDE_WIDTH), "-y", str(_HEIGHT))
    await _start_view(wait, Role.IMPLEMENTER)

    async def initial_content() -> bool:
        screen = await _screen(tmux_server, pane)
        return (
            bool(screen)
            and _STRIP_PHRASE in screen[0]
            and any("earlier output" in line for line in screen)
        )

    await wait_until(initial_content, timeout=_TIMEOUT_SECONDS)
    await tmux_server.run(
        "resize-window", "-t", pane, "-x", str(_WIDE_WIDTH), "-y", str(_SHORT_HEIGHT)
    )
    with log.open("a") as handle:
        for index in range(_SHORT_HEIGHT):
            handle.write(_row("Message", text=f"resized output {index}") + "\n")

    async def latest_output_visible() -> bool:
        screen = await _screen(tmux_server, pane)
        return (
            bool(screen)
            and _STRIP_PHRASE in screen[0]
            and any("resized output 9" in line for line in screen)
        )

    await wait_until(latest_output_visible, timeout=_TIMEOUT_SECONDS)
    screen = await _screen(tmux_server, pane)
    assert sum(_STRIP_PHRASE in line for line in screen) == 1
    assert sum("resized output 9" in line for line in screen) == 1
    assert all(len(line) <= _WIDE_WIDTH for line in screen)


async def test_a_recreated_view_pane_shows_the_still_current_wait(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    wait = await _wait(engine_env)
    wait.queue_on_quota()
    wait.block(_IMPLEMENTER_AGENT)
    await wait.sync()
    pane = await _start_view(wait, Role.IMPLEMENTER)

    async def strip_on_top() -> bool:
        screen = await _screen(tmux_server, pane)
        return bool(screen) and _STRIP_PHRASE in screen[0]

    await wait_until(strip_on_top, timeout=_TIMEOUT_SECONDS)
    await _start_view(wait, Role.IMPLEMENTER)
    await wait_until(strip_on_top, timeout=_TIMEOUT_SECONDS)
    screen = await _screen(tmux_server, pane)
    assert sum(_STRIP_PHRASE in line for line in screen) == 1
