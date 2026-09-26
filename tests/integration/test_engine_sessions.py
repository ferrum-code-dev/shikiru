from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.agents.contracts import Rung
from shikiru.config.settings import AgentConfig, Settings
from shikiru.domain.reports import REPORT_FILENAME, REPORT_KIND_FILENAME, ImplementationReport
from shikiru.domain.runs import RunStatus
from shikiru.domain.tasks import Role
from shikiru.engine.sessions import SessionManager
from shikiru.workspace.roots import run_dir
from tests.support.engine import FAKE_AGENT_ALIAS, FAKE_AGENT_NAME, FAKE_RUNG
from tests.support.factories import make_settings

if TYPE_CHECKING:
    from tests.support.engine import EngineEnv


def _session_manager(env: EngineEnv, settings: Settings | None = None) -> SessionManager:
    return SessionManager(
        runs=env.runs,
        project_root=env.project_root,
        settings=settings
        or make_settings(
            implementer_ladder=[FAKE_RUNG],
            agents={FAKE_AGENT_NAME: AgentConfig()},
        ),
    )


def _write_report(env: EngineEnv, task_id: str) -> None:
    turn_dir = run_dir(env.project_root, task_id) / "turns" / "01-implement-i1"
    turn_dir.mkdir(parents=True)
    (turn_dir / REPORT_KIND_FILENAME).write_text("implementation")
    (turn_dir / REPORT_FILENAME).write_text(
        ImplementationReport(
            status="completed",
            summary="implemented",
            commits=[],
            tests=[],
            trackers_updated=True,
        ).model_dump_json()
    )


async def test_session_selection_reuses_the_same_agent_session(engine_env: EngineEnv) -> None:
    task = await engine_env.build_task()
    sessions = _session_manager(engine_env)

    first = sessions.session_for(task, Role.IMPLEMENTER)
    engine_env.runs.set_session(first.run.id, "session-1", "fake-agent resume session-1")
    second = sessions.session_for(task, Role.IMPLEMENTER)

    assert second.run.id == first.run.id
    assert second.session_id == "session-1"
    assert second.handoff is False


async def test_same_agent_model_change_keeps_the_run_and_session(engine_env: EngineEnv) -> None:
    task = await engine_env.build_task()
    first = engine_env.turn_system.sessions.session_for(task, Role.IMPLEMENTER)
    engine_env.runs.set_session(first.run.id, "session-1", "resume session-1")
    next_rung = Rung(agent=FAKE_AGENT_NAME, model="fake-2", effort="high")
    settings = make_settings(
        implementer_ladder=[FAKE_RUNG, next_rung],
        agents={FAKE_AGENT_NAME: AgentConfig()},
    )
    changed_task = task.model_copy(update={"rung_index": {Role.IMPLEMENTER: 1}})

    selected = _session_manager(engine_env, settings).session_for(changed_task, Role.IMPLEMENTER)

    assert selected.run.id == first.run.id
    assert selected.rung == next_rung
    assert selected.session_id == "session-1"
    assert selected.run.model == "fake-2"


async def test_different_agent_starts_a_handoff_run_when_history_has_a_report(
    engine_env: EngineEnv,
) -> None:
    task = await engine_env.build_task()
    first = engine_env.turn_system.sessions.session_for(task, Role.IMPLEMENTER)
    engine_env.runs.set_session(first.run.id, "session-1", "resume session-1")
    _write_report(engine_env, task.id)
    changed_rung = Rung(agent=FAKE_AGENT_ALIAS, model="fake-2", effort=None)
    settings = make_settings(
        implementer_ladder=[FAKE_RUNG, changed_rung],
        agents={FAKE_AGENT_NAME: AgentConfig(), FAKE_AGENT_ALIAS: AgentConfig()},
    )
    changed_task = task.model_copy(update={"rung_index": {Role.IMPLEMENTER: 1}})

    selected = _session_manager(engine_env, settings).session_for(changed_task, Role.IMPLEMENTER)

    runs = engine_env.runs.for_task(task.id)
    assert selected.run.id != first.run.id
    assert selected.session_id is None
    assert selected.handoff is True
    assert runs[0].status is RunStatus.LIVE
    assert runs[1].status is RunStatus.CLOSED


async def test_task_closure_closes_all_role_runs(engine_env: EngineEnv) -> None:
    task = await engine_env.build_task()
    sessions = _session_manager(engine_env)
    sessions.session_for(task, Role.IMPLEMENTER)
    sessions.session_for(task, Role.LEADER)

    sessions.close_task(task.id)

    assert all(run.status is RunStatus.CLOSED for run in engine_env.runs.for_task(task.id))
