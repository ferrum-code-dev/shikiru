"""A plan task without trackers writes its plan to the untracked plans folder."""

from __future__ import annotations

import asyncio
import stat
from typing import TYPE_CHECKING

from fake_agent.agent import create as create_fake_agent
from fake_agent.scripted import FAKE_CHANGE_FILENAME, FAKE_PLAN_CONTENT

from shikiru.agents.registry import AgentRegistry
from shikiru.domain.tasks import Mode, Role, Status
from shikiru.paths import plans_dir, run_dir, task_root
from shikiru.process import SubprocessRunner
from shikiru.workspace.git import Git
from tests.support.engine import FAKE_AGENT_NAME, set_role_script
from tests.support.flow import build_flow, build_flow_task, flow_settings, init_project_repo
from tests.support.git_repo import GIT_COMMAND_TIMEOUT, commit_all
from tests.support.waiting import LONG_WAIT_SECONDS

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from shikiru.agents.contracts import (
        Agent,
        AgentContext,
        CheckResult,
        HeadlessAgent,
        Rung,
        Turn,
        TurnSpec,
    )
    from tests.support.engine import EngineEnv
    from tests.support.tmux import TmuxServer


_TRACKER = "tasks/todo.md"
_RUNNER = SubprocessRunner()


class _RecordingAgent:
    def __init__(self, name: str, delegate: Agent, recorded: list[tuple[Path, ...]]) -> None:
        self.name = name
        self._delegate = delegate
        self._recorded = recorded

    async def start_turn(self, spec: TurnSpec) -> Turn:
        self._recorded.append(spec.add_dirs)
        return await self._delegate.start_turn(spec)

    def interactive_command(self, session_id: str, run_dir: Path, rung: Rung) -> Sequence[str]:
        return self._delegate.interactive_command(session_id, run_dir, rung)

    async def doctor(self, models: Sequence[str]) -> list[CheckResult]:
        return await self._delegate.doctor(models)

    def headless(self) -> HeadlessAgent | None:
        return self._delegate.headless()


def _recording_registry(recorded: list[tuple[Path, ...]]) -> AgentRegistry:
    registry = AgentRegistry()

    def create(context: AgentContext) -> Agent:
        return _RecordingAgent(FAKE_AGENT_NAME, create_fake_agent(context), recorded)

    registry.register(FAKE_AGENT_NAME, create)
    return registry


def _plan_prompt(project_root: Path, task_id: str) -> str:
    turns = run_dir(project_root, task_id) / "turns"
    [prompt] = [path.read_text() for path in turns.glob("01-plan-*/prompt.md")]
    return prompt


def _git() -> Git:
    return Git(_RUNNER, command_timeout=GIT_COMMAND_TIMEOUT)


async def test_a_plan_without_trackers_writes_the_untracked_plan_and_finishes_done(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.PLAN)
    plans = plans_dir(engine_env.project_root)
    plan_path = plans / f"{task.id}.md"
    await set_role_script(
        tmux_server,
        Role.LEADER,
        [
            {
                "kind": "accept",
                "write_plan": True,
                "new_tasks": [{"title": "execute step one", "request": f"do it, read {plan_path}"}],
            }
        ],
    )
    main_sha = await _git().rev_parse(engine_env.project_root, "main")
    recorded: list[tuple[Path, ...]] = []

    flow_env = build_flow(
        engine_env,
        flow_settings(),
        local_integration=True,
        registry=_recording_registry(recorded),
    )
    await asyncio.wait_for(flow_env.flow.run(task.id), timeout=LONG_WAIT_SECONDS)

    final = engine_env.tasks.get(task.id)
    assert final is not None
    assert final.status is Status.DONE
    assert plan_path.read_text() == FAKE_PLAN_CONTENT
    assert stat.S_IMODE(plans.stat().st_mode) == 0o700
    prompt = _plan_prompt(engine_env.project_root, task.id)
    assert f"Plans folder: `{plans}`" in prompt
    assert "Trackers:" not in prompt
    assert str(plan_path) in prompt
    assert recorded
    assert all(entry == (plans,) for entry in recorded)
    children = [t for t in engine_env.tasks.list_all() if t.parent_id == task.id]
    assert [str(plan_path) in child.request for child in children] == [True]
    assert await _git().rev_parse(engine_env.project_root, "main") == main_sha
    assert not (engine_env.project_root / FAKE_CHANGE_FILENAME).exists()
    assert not task_root(engine_env.project_root, task.id).exists()
    assert await _git().diff_name_only(engine_env.project_root, f"{main_sha}..HEAD") == []


async def test_a_plan_with_trackers_commits_to_them_as_before(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    await init_project_repo(engine_env.project_root)
    (engine_env.project_root / "tasks").mkdir()
    (engine_env.project_root / _TRACKER).write_text("- [ ] A1\n")
    await commit_all(_RUNNER, engine_env.project_root, "tracker")
    task = await build_flow_task(engine_env, mode=Mode.PLAN)
    await set_role_script(tmux_server, Role.LEADER, [{"kind": "accept"}])
    main_sha = await _git().rev_parse(engine_env.project_root, "main")
    settings = flow_settings()
    settings = settings.model_copy(
        update={"project": settings.project.model_copy(update={"trackers": [_TRACKER]})}
    )
    recorded: list[tuple[Path, ...]] = []

    flow_env = build_flow(
        engine_env,
        settings,
        local_integration=True,
        registry=_recording_registry(recorded),
    )
    await asyncio.wait_for(flow_env.flow.run(task.id), timeout=LONG_WAIT_SECONDS)

    final = engine_env.tasks.get(task.id)
    assert final is not None
    assert final.status is Status.DONE
    prompt = _plan_prompt(engine_env.project_root, task.id)
    assert f"Trackers: `{_TRACKER}`" in prompt
    assert "Plans folder:" not in prompt
    assert recorded
    assert all(entry == () for entry in recorded)
    assert not plans_dir(engine_env.project_root).exists()
    assert (engine_env.project_root / FAKE_CHANGE_FILENAME).is_file()
    assert FAKE_CHANGE_FILENAME in await _git().diff_name_only(
        engine_env.project_root, f"{main_sha}..HEAD"
    )
