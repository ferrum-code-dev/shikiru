from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.agents.contracts import (
    BILLING_MODE_API,
    BILLING_MODE_SUBSCRIPTION,
    BILLING_SOURCE_OPERATOR_CONFIG,
    COST_BASIS_UNKNOWN,
    Rung,
    Usage,
)
from shikiru.config.settings import AgentConfig, Settings
from shikiru.domain.reports import REPORT_FILENAME, REPORT_KIND_FILENAME, ImplementationReport
from shikiru.domain.runs import RunStatus
from shikiru.domain.tasks import Role
from shikiru.engine.agents import AgentPool
from shikiru.engine.sessions import SessionManager
from shikiru.operator.queries import aggregate_usage
from shikiru.process import SubprocessRunner
from shikiru.store.runs import RunStore
from shikiru.workspace.roots import run_dir
from tests.support.engine import FAKE_AGENT_ALIAS, FAKE_AGENT_NAME, FAKE_RUNG, build_registry
from tests.support.factories import make_settings

if TYPE_CHECKING:
    from shikiru.store.database import Database
    from tests.support.engine import EngineEnv

_COST_BASIS_BILLED = "billed"
_COST_BASIS_API_EQUIVALENT = "api_equivalent"
_COST_BASIS_MIXED = "mixed"


def _session_manager(env: EngineEnv, settings: Settings | None = None) -> SessionManager:
    resolved_settings = settings or make_settings(
        implementer_ladder=[FAKE_RUNG],
        agents={FAKE_AGENT_NAME: AgentConfig()},
    )
    return SessionManager(
        runs=env.runs,
        agents=AgentPool(
            build_registry().create,
            SubprocessRunner(),
            resolved_settings,
            env.clock,
        ),
        project_root=env.project_root,
        settings=resolved_settings,
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
    first = _session_manager(engine_env, _billing_settings(BILLING_MODE_SUBSCRIPTION)).session_for(
        task, Role.IMPLEMENTER
    )
    engine_env.runs.set_session(first.run.id, "session-1", "resume session-1")
    next_rung = Rung(agent=FAKE_AGENT_NAME, model="fake-2", effort="high")
    settings = make_settings(
        implementer_ladder=[FAKE_RUNG, next_rung],
        agents={FAKE_AGENT_NAME: AgentConfig(options={"billing_mode": BILLING_MODE_API})},
    )
    changed_task = task.model_copy(update={"rung_index": {Role.IMPLEMENTER: 1}})

    selected = _session_manager(engine_env, settings).session_for(changed_task, Role.IMPLEMENTER)

    assert selected.run.id == first.run.id
    assert selected.rung == next_rung
    assert selected.session_id == "session-1"
    assert selected.run.model == "fake-2"
    assert selected.run.billing_mode == BILLING_MODE_SUBSCRIPTION
    assert selected.run.billing_source == BILLING_SOURCE_OPERATOR_CONFIG


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


async def test_billing_facts_are_captured_per_session_and_survive_restart_and_usage_changes(
    engine_env: EngineEnv,
    database: Database,
) -> None:
    task = await engine_env.build_task()
    subscription_settings = _billing_settings(BILLING_MODE_SUBSCRIPTION)
    subscription = _session_manager(engine_env, subscription_settings).session_for(
        task, Role.IMPLEMENTER
    )
    engine_env.runs.record_usage(
        subscription.run.id,
        Usage(0, 0, 0, 0, None, COST_BASIS_UNKNOWN, None),
    )

    changed = _session_manager(engine_env, _billing_settings(BILLING_MODE_API)).session_for(
        task, Role.IMPLEMENTER
    )

    assert changed.run.id == subscription.run.id
    assert changed.run.billing_mode == BILLING_MODE_SUBSCRIPTION
    assert changed.run.billing_source == BILLING_SOURCE_OPERATOR_CONFIG
    assert changed.run.usage.cost_usd is None
    assert changed.run.usage.cost_basis == COST_BASIS_UNKNOWN

    restarted_connection = database.connect()
    try:
        [persisted] = RunStore(restarted_connection, engine_env.clock).for_task(task.id)
        assert persisted.billing_mode == BILLING_MODE_SUBSCRIPTION
        assert persisted.billing_source == BILLING_SOURCE_OPERATOR_CONFIG
        assert persisted.usage.cost_usd is None
        assert persisted.usage.cost_basis == COST_BASIS_UNKNOWN
    finally:
        restarted_connection.close()

    engine_env.runs.close(subscription.run.id)
    configured_again = _session_manager(engine_env, subscription_settings).session_for(
        task, Role.IMPLEMENTER
    )
    engine_env.runs.record_usage(
        configured_again.run.id,
        Usage(0, 0, 0, 0, 0.0, _COST_BASIS_BILLED, None),
    )
    assert configured_again.run.billing_mode == BILLING_MODE_SUBSCRIPTION
    persisted_zero_charge = engine_env.runs.for_task(task.id)[0]
    assert persisted_zero_charge.usage.cost_usd == 0.0
    assert persisted_zero_charge.usage.cost_basis == _COST_BASIS_BILLED

    engine_env.runs.close(configured_again.run.id)
    billed = _session_manager(engine_env, _billing_settings(BILLING_MODE_API)).session_for(
        task, Role.IMPLEMENTER
    )
    engine_env.runs.record_usage(
        billed.run.id,
        Usage(1, 2, 0, 0, 1.25, _COST_BASIS_BILLED, None),
    )
    engine_env.runs.close(billed.run.id)
    equivalent = _session_manager(engine_env, _billing_settings(BILLING_MODE_API)).session_for(
        task, Role.IMPLEMENTER
    )
    engine_env.runs.record_usage(
        equivalent.run.id,
        Usage(2, 3, 0, 0, 0.25, _COST_BASIS_API_EQUIVALENT, None),
    )

    run_history = engine_env.runs.for_task(task.id)
    [usage] = aggregate_usage(run_history)

    assert usage.cost_basis == COST_BASIS_UNKNOWN
    assert usage.cost_usd is None
    assert usage.billed_cost_usd == 1.25
    assert usage.api_equivalent_cost_usd == 0.25
    assert usage.unknown_cost_runs == 1

    priced_runs = [run for run in run_history if run.usage.cost_usd is not None]
    [priced_usage] = aggregate_usage(priced_runs)
    assert priced_usage.cost_basis == _COST_BASIS_MIXED
    assert priced_usage.cost_usd is None
    assert priced_usage.unknown_cost_runs == 0


def _billing_settings(mode: str) -> Settings:
    return make_settings(
        implementer_ladder=[FAKE_RUNG],
        agents={FAKE_AGENT_NAME: AgentConfig(options={"billing_mode": mode})},
    )
