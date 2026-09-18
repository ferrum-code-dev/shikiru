"""Scaffolding for `TaskFlow` integration tests: a monorepo git worktree and two live panes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from shikiru.clock import SystemClock
from shikiru.config.settings import AgentConfig, RepoConfig
from shikiru.domain.commands import Submit
from shikiru.domain.tasks import Role
from shikiru.engine.agents import AgentPool
from shikiru.engine.escalations import EscalationRaiser
from shikiru.engine.flow import TaskFlow
from shikiru.engine.titles import TaskTitles
from shikiru.engine.turns import TurnRunner
from shikiru.process import SubprocessRunner
from shikiru.prompts.renderer import PromptRenderer
from shikiru.store.commands import CommandQueue
from shikiru.store.escalations import EscalationStore
from shikiru.store.events import EventLog
from shikiru.workspace.facts import GitFacts
from shikiru.workspace.git import Git
from shikiru.workspace.roots import TaskRootBuilder
from tests.support.engine import FAKE_AGENT_NAME, FAKE_RUNG, build_decorator, build_registry
from tests.support.factories import make_settings
from tests.support.git_repo import commit_all, init_repo

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.config.settings import Settings
    from shikiru.domain.tasks import Mode, Task
    from tests.support.engine import EngineEnv

_MONOREPO = (RepoConfig(path="."),)


def flow_settings(*, max_iterations: int = 5, escalate_after: int = 2) -> Settings:
    """Settings whose leader, implementer and helper ladders are all the fake agent."""
    return make_settings(
        max_iterations=max_iterations,
        escalate_after=escalate_after,
        leader_ladder=[FAKE_RUNG],
        implementer_ladder=[FAKE_RUNG],
        helper_ladder=[FAKE_RUNG],
        agents={FAKE_AGENT_NAME: AgentConfig()},
    )


async def init_project_repo(project_root: Path) -> None:
    """Turn `project_root` into a one-commit monorepo git repo (03 §5's monorepo case)."""
    runner = SubprocessRunner()
    await init_repo(runner, project_root)
    (project_root / "README.md").write_text("shikiru flow test project\n")
    await commit_all(runner, project_root, "initial commit")


async def build_flow_task(
    env: EngineEnv, *, title: str = "Add retries", mode: Mode | None = None
) -> Task:
    """Insert a queued task with a leader+implementer pane and a real monorepo worktree.

    `init_project_repo` must already have made `env.project_root` a git repo: every flow test
    needs a real worktree because the fake agent commits into it during implement and plan
    turns (03 §2's facts, computed from real `git` state).
    """
    tasks = env.tasks
    queue = CommandQueue(env.connection, SystemClock())
    queued = queue.append(Submit(task_id="", title=title, request=title, mode=mode))
    assert isinstance(queued.command, Submit)
    submitted = tasks.insert_from_submit(queued.command)
    leader_pane = await env.new_pane()
    implementer_pane = await env.new_pane()
    tasks.set_slot(submitted.id, 1)
    tasks.set_panes(submitted.id, {Role.LEADER: leader_pane, Role.IMPLEMENTER: implementer_pane})
    tasks.set_window(submitted.id, leader_pane)
    builder = TaskRootBuilder(Git(SubprocessRunner()), env.project_root)
    repos = await builder.build(submitted.id, submitted.title, _MONOREPO)
    tasks.set_repos(submitted.id, repos)
    task = tasks.get(submitted.id)
    assert task is not None
    return task


@dataclass
class FlowEnv:
    """A `TaskFlow` and the collaborators a flow test reads back for assertions."""

    flow: TaskFlow
    env: EngineEnv
    events: EventLog
    escalations: EscalationStore
    titles: TaskTitles


def build_flow(env: EngineEnv, settings: Settings) -> FlowEnv:
    """Build a `TaskFlow` for `settings`, with its own `SessionManager` (leader + implementer
    both need the fake agent, unlike `engine_env`'s default single-role manager).
    """
    sessions = env.build_manager(settings)
    events = EventLog(env.connection, SystemClock())
    escalations_store = EscalationStore(env.connection, SystemClock())
    escalations = EscalationRaiser(escalations_store, events, env.runs, env.project_root)
    titles = TaskTitles(build_decorator(env.tmux), env.runs, settings)
    turns = TurnRunner(PromptRenderer(env.project_root), SystemClock())
    git_facts = GitFacts(Git(SubprocessRunner()))
    agent_pool = AgentPool(build_registry().create, SubprocessRunner(), settings, SystemClock())
    flow = TaskFlow(
        tasks=env.tasks,
        events=events,
        sessions=sessions,
        turns=turns,
        git_facts=git_facts,
        escalations=escalations,
        titles=titles,
        settings=settings,
        agent_pool=agent_pool,
        pane_factory=env.pane,
        project_root=env.project_root,
    )
    return FlowEnv(flow=flow, env=env, events=events, escalations=escalations_store, titles=titles)
